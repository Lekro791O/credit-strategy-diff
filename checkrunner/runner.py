"""One process/container per check; failures are diagnostics, never fake PASS.

Docker is the default for user code. Local subprocesses require explicit
trusted=True and provide fault isolation, NOT a security sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from .validation import validate_rows

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class CheckSpec:
    code: str | None = None
    path: str | Path | None = None
    params: dict = field(default_factory=dict)
    name: str = 'workspace.py'

    def source(self):
        if (self.code is None) == (self.path is None):
            raise ValueError('Specify exactly one of code or path')
        if not isinstance(self.params, dict):
            raise ValueError('params must be a dictionary')
        if self.path is not None:
            path = Path(self.path)
            if path.suffix != '.py':
                raise ValueError('Selected file must have .py extension')
            code, origin = path.read_text(encoding='utf-8-sig'), str(path.resolve())
        else:
            code, origin = self.code, self.name
        if not isinstance(code, str) or len(code.encode('utf8')) > 1024 * 1024:
            raise ValueError('Python source must be a string no larger than 1 MiB')
        return code, origin


def provided_checks(directory=None):
    folder = Path(directory) if directory else ROOT / 'task' / 'checks'
    return [CheckSpec(path=p) for p in sorted(folder.glob('check_*.py'))]


class CheckRunner:
    def __init__(self, *, backend='docker', trusted=False, timeout=30.0,
                 image='credit-strategy-checkrunner:local', max_output_bytes=8 * 1024 * 1024):
        if backend not in ('docker', 'subprocess'):
            raise ValueError('backend must be docker or subprocess')
        if backend == 'subprocess' and not trusted:
            raise ValueError('Local subprocess is not a sandbox; trusted=True required')
        if timeout <= 0 or max_output_bytes <= 0:
            raise ValueError('timeout and max_output_bytes must be positive')
        self.backend, self.timeout, self.image = backend, timeout, image
        self.max_output_bytes = max_output_bytes

    def run(self, store, run_id, checks=None, *, on_event=None, persist=True):
        specs = provided_checks() if checks is None else list(checks)
        report = dict(run_id=run_id, status='RUNNING', rows=[], executions=[], diagnostics=[])
        seen_ids = set()

        def event(data):
            if on_event:
                try:
                    on_event(data)
                except Exception as exc:
                    report['diagnostics'].append(dict(stage='progress_callback', message=str(exc)))

        with tempfile.TemporaryDirectory(prefix='checks-') as work:
            snapshot = Path(work) / 'run.duckdb'
            revision = store.snapshot(run_id, snapshot)
            report['revision'] = revision
            for index, spec in enumerate(specs):
                started = time.perf_counter()
                execution = dict(index=index, status='RUNNING', row_start=len(report['rows']), row_count=0)
                event(dict(index=index, status='RUNNING'))
                try:
                    code, origin = spec.source()
                    execution.update(origin=origin, code=code, sha256=hashlib.sha256(code.encode()).hexdigest())
                    request = dict(code=code, origin=origin, params=spec.params, run_id=run_id)
                    result, logs = self._launch(snapshot, request)
                    execution['logs'] = logs
                    if not result.get('ok'):
                        raise WorkerError(result)
                    if not isinstance(result.get('check_id'), str) or not result['check_id']:
                        raise ValueError('Worker returned invalid check ID')
                    if result['check_id'] in seen_ids:
                        raise ValueError(f"Duplicate CHECK.id: {result['check_id']}")
                    rows = validate_rows(result['rows'], result['params'].get('app_ids_limit', 20))
                    # Revalidate in the parent; the worker is an untrusted boundary.
                    known = {s['APP_ID'] for s in store.for_run(run_id).segments()}
                    if any(set(r['APP_IDS']) - known for r in rows):
                        raise ValueError('APP_IDS contains applications outside this run')
                    seen_ids.add(result['check_id'])
                    report['rows'].extend(rows)
                    execution.update(status='COMPLETED', check_id=result['check_id'],
                                     label=result['label'], params=result['params'], row_count=len(rows))
                except Exception as exc:
                    execution.update(status='TIMEOUT' if isinstance(exc, TimeoutError) else 'ERROR',
                                     diagnostic=exc.detail if isinstance(exc, WorkerError) else
                                     dict(error_type=type(exc).__name__, message=str(exc)))
                execution['duration_seconds'] = round(time.perf_counter() - started, 6)
                report['executions'].append(execution)
                event({k: v for k, v in execution.items() if k != 'code'})
            errors = any(e['status'] != 'COMPLETED' for e in report['executions'])
            report['status'] = 'COMPLETED_WITH_ERRORS' if errors else 'COMPLETED'
            report['verdict'] = 'FAIL' if any(r['RESULT'] == 'FAIL' for r in report['rows']) else (
                None if errors or not report['rows'] else 'PASS')
            if persist:
                store.save_check_report(run_id, revision, report)
            return report

    def _launch(self, snapshot, request):
        with tempfile.TemporaryDirectory(prefix='check-') as work:
            folder = Path(work)
            # Each check gets its own snapshot; even trusted accidental writes
            # cannot poison another check. The production database is never passed.
            if snapshot is not None:
                shutil.copyfile(snapshot, folder / 'run.duckdb')
            result_path = folder / 'result.json'
            container_name = 'strategy-check-' + uuid.uuid4().hex
            if self.backend == 'docker':
                if not shutil.which('docker'):
                    raise RuntimeError('Docker is unavailable. Build checkrunner/Dockerfile and start Docker; untrusted code was not executed.')
                request['database'] = '/input/run.duckdb'
                command = ['docker', 'run', '--rm', '--name', container_name,
                           '--network=none', '--read-only', '--cap-drop=ALL',
                           '--security-opt=no-new-privileges', '--pids-limit=64',
                           '--memory=512m', '--cpus=1', '--user=65534:65534',
                           '--tmpfs=/tmp:rw,noexec,nosuid,size=32m,mode=1777',
                           '--mount', f'type=bind,source={folder},target=/input,readonly',
                           self.image, '/input/request.json', '/tmp/result.json']
                # Container emits result through bounded stdout after the worker;
                # check stdout is redirected to stderr by container_entry.
            else:
                request['database'] = str(folder / 'run.duckdb')
                module = 'checkrunner.procedure_worker' if request.get('worker_kind') == 'procedure' else 'checkrunner.worker'
                command = [sys.executable, '-m', module, str(folder / 'request.json'), str(result_path)]
            (folder / 'request.json').write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding='utf8')
            os.chmod(folder, 0o755)
            for name in ['request.json', 'run.duckdb']:
                if (folder / name).exists():
                    os.chmod(folder / name, 0o444)
            env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT), 'PYTHONIOENCODING': 'utf-8',
                   'HOME': work, 'TMPDIR': work, 'PYTHONDONTWRITEBYTECODE': '1'}
            # Docker client needs its context configuration, not forwarded into the container.
            if self.backend == 'docker':
                env = {**os.environ, 'PYTHONPATH': str(ROOT)}
            with open(folder / 'stdout', 'wb') as stdout, open(folder / 'stderr', 'wb') as stderr:
                proc = subprocess.Popen(command, cwd=folder, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
                deadline = time.monotonic() + self.timeout
                try:
                    while proc.poll() is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f'Check exceeded {self.timeout:g} seconds')
                        if sum(p.stat().st_size for p in [folder / 'stdout', folder / 'stderr', result_path] if p.exists()) > self.max_output_bytes:
                            raise ValueError('Check exceeded output size limit')
                        time.sleep(0.02)
                finally:
                    # Kill descendants too, including children left after parent exit.
                    try:
                        os.killpg(proc.pid, getattr(signal, 'SIGKILL', signal.SIGTERM))
                    except (ProcessLookupError, OSError, AttributeError):
                        try:
                            proc.kill()  # Windows: killpg недоступен — хватит корня
                        except Exception:
                            pass
                    proc.wait()
                    if self.backend == 'docker':
                        subprocess.run(['docker', 'rm', '-f', container_name], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=10, check=False)
            if sum(p.stat().st_size for p in [folder / 'stdout', folder / 'stderr', result_path] if p.exists()) > self.max_output_bytes:
                raise ValueError('Check exceeded output size limit')
            logs = {}
            for key in ['stdout', 'stderr']:
                with (folder / key).open('rb') as stream:
                    logs[key] = stream.read(16000).decode('utf8', errors='replace')
            if proc.returncode:
                raise WorkerError(dict(error_type='WorkerExit', message=f'Worker exited with code {proc.returncode}', logs=logs))
            output = folder / 'stdout' if self.backend == 'docker' else result_path
            if not output.exists() or output.stat().st_size > self.max_output_bytes:
                raise ValueError('Worker result missing or exceeds output size limit')
            result = json.loads(output.read_text(encoding='utf8'))
            if self.backend == 'docker':
                logs.pop('stdout', None)
            return result, logs


class WorkerError(Exception):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(detail.get('message', 'Worker error'))
