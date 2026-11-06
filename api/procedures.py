"""Procedure resolution boundary; local Python requires a server-side trust opt-in.

Participant 2 can replace execute_job with its sandbox using the same JSON job.
A subprocess isolates failures, NOT filesystem/network access of hostile code.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from . import config
from pipeline import PipelineError


def resolve_procedure(spec, stage, default, *, user, settings=None):
    if not spec or (spec.get('kind', 'builtin') == 'builtin' and spec.get('name', 'default') == 'default'):
        return default
    if os.environ.get('CSD_TRUST_LOCAL_PROCEDURES') != '1':
        raise PipelineError('Custom procedures require a configured sandbox or server CSD_TRUST_LOCAL_PROCEDURES=1 for trusted local code')
    kind, name = spec.get('kind'), spec.get('name', '')
    if kind == 'workspace':
        if not name.replace('_', '').isalnum():
            raise PipelineError('Invalid workspace procedure name')
        path = (config.WORKSPACE_DIR/user/(name+'.py')).resolve()
    elif kind == 'file':
        path = Path(name).resolve()
        approved_root = (config.WORKSPACE_DIR/user).resolve()
        if not path.is_relative_to(approved_root):
            raise PipelineError('Procedure file must be inside the current user workspace')
    else:
        raise PipelineError('Unsupported procedure source')
    if not path.is_relative_to((config.WORKSPACE_DIR/user).resolve()):
        raise PipelineError('Procedure must resolve inside the current user workspace')
    if not path.is_file():
        raise PipelineError('Procedure file not found')
    code = path.read_text(encoding='utf-8-sig')
    def execute(*args, **kwargs):
        job = {'version': 1, 'stage': stage, 'code': code, 'origin': f'{kind}:{name}', 'args': args,
               'settings': settings, 'app_id': kwargs.get('app_id'), 'source': kwargs.get('source')}
        # JSON only. No pickle and no inherited API credentials.
        bootstrap = 'import sys;sys.path.insert(0,sys.argv[1]);from pipeline.procedure_worker import main;main()'
        try:
            result = subprocess.run([sys.executable, '-I', '-c', bootstrap, str(config.REPO_ROOT)],
                                    input=json.dumps(job, allow_nan=False), capture_output=True,
                                    text=True, timeout=60, cwd=config.REPO_ROOT,
                                    env={'PATH': os.environ.get('PATH', ''), 'PYTHONIOENCODING': 'utf-8'})
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f'Procedure timed out: stage={stage}') from exc
        if result.returncode:
            raise PipelineError(f'Procedure failed: stage={stage}, exit={result.returncode}')
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise PipelineError(f'Procedure returned invalid JSON: stage={stage}') from exc
    execute.code_snapshot = code
    execute.origin = f'{kind}:{name}'
    return execute
