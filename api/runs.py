"""Single-server run coordinator: atomic state, real stage events, isolated code."""
from __future__ import annotations
import hashlib
import inspect
import json
import re
import threading
import time
import traceback
import uuid
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import config, checks, llm, workspace
from .mart import DuckMart
from .models import Run, RunConfig, RunSummary, Stage
from .persistence import atomic_write

TITLES = {'prepare': 'Подготовка входов и выбранных скриптов', 'normalize': 'Нормализация ответов', 'compare': 'Сопоставление срезов',
          'facts': 'Подготовка фактов counts', 'store': 'Сохранение DuckDB',
          'checks': 'Исполнение проверок', 'llm': 'Мнение языковой модели'}


class RunManager:
    def __init__(self):
        config.ensure_dirs()
        self._lock = threading.RLock()
        self._active = set()
        self._adhoc = set()
        self._pool = ThreadPoolExecutor(max_workers=int(os.environ.get('CSD_RUN_WORKERS', '2')), thread_name_prefix='csd-run')

    def _run_path(self, run_id):
        if not re.fullmatch(r'[a-f0-9]{12}', run_id):
            raise FileNotFoundError('Invalid run ID')
        return config.RUNS_DIR / (run_id + '.json')

    def _mart_path(self, run_id):
        self._run_path(run_id)
        return config.MART_DIR / (run_id + '.duckdb')

    def save(self, run):
        with self._lock:
            atomic_write(self._run_path(run.id), run.model_dump_json())

    def load(self, run_id):
        return Run.model_validate_json(self._run_path(run_id).read_text(encoding='utf8'))

    def list_runs(self, user=None):
        result = []
        for path in config.RUNS_DIR.glob('*.json'):
            try:
                run = self.load(path.stem)
                if user is None or run.config.user == user:
                    result.append(run)
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda r: r.created, reverse=True)

    def recover_interrupted(self):
        for run in self.list_runs():
            if run.status in ('pending', 'running') and run.id not in self._active:
                run.status = 'error'
                run.error = 'SERVER_RESTARTED: запуск прерван перезапуском сервера; используйте повтор'
                self._fail_stages(run)
                self.save(run)

    def _configs_path(self, user):
        workspace._user_dir(user)  # validate identifier
        return config.DATA_DIR / ('configs-' + user + '.json')

    def list_configs(self, user):
        path = self._configs_path(user)
        return json.loads(path.read_text(encoding='utf8')) if path.exists() else []

    def load_config(self, user, name):
        match = next((c for c in self.list_configs(user) if c['name'] == name), None)
        if match is None: raise FileNotFoundError('Configuration not found')
        return RunConfig.model_validate(match)

    def save_config(self, user, cfg):
        cfg = RunConfig.model_validate(dict(cfg, user=user)).model_dump()
        with self._lock:
            rows = [r for r in self.list_configs(user) if r['name'] != cfg['name']]
            atomic_write(self._configs_path(user), json.dumps(rows + [cfg], ensure_ascii=False))
        return cfg

    def delete_config(self, user, name):
        with self._lock:
            self.load_config(user, name)
            rows = [r for r in self.list_configs(user) if r['name'] != name]
            atomic_write(self._configs_path(user), json.dumps(rows, ensure_ascii=False))

    def create(self, cfg):
        run = Run(id=uuid.uuid4().hex[:12], config=cfg)
        names = [n for n in cfg.stage_order if n != 'llm' or cfg.llm_opinion]
        names.insert(names.index('facts') + 1, 'store')
        names.insert(0, 'prepare')
        run.stages = [Stage(name=n, title=TITLES[n]) for n in names]
        self.save(run)
        return run

    def repeat(self, run_id):
        old = self.load(run_id)
        run = self.create(old.config.model_copy(deep=True))
        run.rerun_of = old.id
        self.save(run)
        return run

    def start(self, run_id):
        with self._lock:
            if run_id in self._active or self.load(run_id).status != 'pending':
                raise ValueError('Run already started; create a repeat instead')
            self._active.add(run_id)
        self._pool.submit(self._run_guarded, run_id)

    def _fail_stages(self, run):
        active = [s for s in run.stages if s.status == 'running']
        if not active:
            active = [s for s in run.stages if s.status == 'pending'][:1]
        for s in active:
            s.status, s.finished, s.error = 'error', time.time(), run.error.splitlines()[-1]
            if s.started is not None: s.duration_seconds = s.finished - s.started
        for s in run.stages:
            if s.status == 'pending':
                s.status, s.message = 'skipped', 'Не выполнялся из-за ошибки предыдущего этапа'

    def _run_guarded(self, run_id):
        try:
            self._run_sync(run_id)
        except Exception:
            run = self.load(run_id)
            run.status, run.error = 'error', traceback.format_exc(limit=6)
            self._fail_stages(run)
            self.save(run)
        finally:
            with self._lock: self._active.discard(run_id)

    def _run_sync(self, run_id):
        run = self.load(run_id)
        if run.status != 'pending': raise ValueError('Run is not pending')
        run.status = 'running'
        self.save(run)
        self._run_body(run_id)

    def _event(self, run_id, event):
        run = self.load(run_id)
        stage = next(s for s in run.stages if s.name == event['stage'])
        status = event['status']
        if status == 'start':
            stage.status, stage.started = 'running', time.time()
            stage.code_ref = event.get('code_ref', 'pipeline:' + stage.name)
            stage.code_snapshot = event.get('code_snapshot', '')
            stage.code_sha256 = hashlib.sha256(stage.code_snapshot.encode()).hexdigest()
            stage.total = event.get('total')
        elif status == 'progress':
            stage.completed, stage.total = event['completed'], event['total']
        elif status == 'finish':
            stage.status, stage.finished = 'done', time.time()
            stage.duration_seconds = stage.finished - stage.started
            if stage.total is not None: stage.completed = stage.total
            stage.message = event.get('message', f"Обработано: {event.get('eligible', event.get('facts', ''))}")
        self.save(run)

    def _begin(self, run_id, stage, ref, snapshot, total=None):
        self._event(run_id, dict(stage=stage, status='start', code_ref=ref, code_snapshot=snapshot, total=total))

    def _end(self, run_id, stage, message):
        self._event(run_id, dict(stage=stage, status='finish', message=message))

    def _build_dataset(self, cfg, event=None):
        from pipeline import build_dataset
        from .procedures import resolve_procedure
        def resolver(spec, stage, default):
            fn = resolve_procedure(spec, stage, default, user=cfg.user, settings=cfg.normalize.settings)
            if stage != 'compare' or event is None:
                return fn
            def measured(*args, **kwargs):
                try:
                    return list(fn(*args, **kwargs))
                finally:
                    measured.completed += 1
                    event(dict(stage='compare', status='progress', completed=measured.completed, total=compare_total[0]))
            measured.completed = 0
            measured.origin = getattr(fn, 'origin', fn.__module__ + ':' + fn.__name__)
            measured.code_snapshot = getattr(fn, 'code_snapshot', None) or inspect.getsource(fn)
            return measured
        compare_total = [0]
        def events(e):
            if e['stage'] == 'compare' and e['status'] == 'start':
                compare_total[0] = e.get('total', 0)
            if event: event(e)
        def process(path):
            return build_dataset(path, cfg.normalize.model_dump(), cfg.compare.model_dump(),
                                 cfg.facts.model_dump(), event=events, resolver=resolver,
                                 expected_app_ids=cfg.expected_app_ids)
        if not cfg.dataset.startswith('demo://'):
            return process(cfg.dataset)
        if cfg.dataset != config.DEMO_DATASET: raise ValueError('Unknown demo dataset')
        import tempfile
        from .demo_data import demo_documents
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR, prefix='demo-input-') as name:
            root = Path(name)
            (root/'BEFORE').mkdir(); (root/'AFTER').mkdir()
            for app_id, before, after in demo_documents():
                for source, document in [('BEFORE', before), ('AFTER', after)]:
                    (root/source/(app_id+'.json')).write_text(json.dumps(document), encoding='utf8')
            return process(root)

    def _make_mart(self, run_id):
        # A broken DuckDB dependency is a visible run failure, never unsafe exec fallback.
        mart = DuckMart(self._mart_path(run_id))
        mart.probe()
        return mart

    def _run_body(self, run_id):
        cfg = self.load(run_id).config
        self._begin(run_id, 'prepare', 'api.runs:RunManager._build_dataset', inspect.getsource(self._build_dataset))
        def pipeline_event(e):
            if e['stage'] == 'normalize' and e['status'] == 'start':
                self._end(run_id, 'prepare', 'Входы найдены; выбранные процедуры разрешены')
            self._event(run_id, e)
        result = self._build_dataset(cfg, event=pipeline_event)
        run = self.load(run_id)
        run.quality, run.diagnostics = result['quality'], result['diagnostics']
        run.fact_rows, run.segment_details = result['facts'], result['segments']
        self.save(run)
        from mart import MartStore
        self._begin(run_id, 'store', 'mart.store:MartStore.save_run', inspect.getsource(MartStore.save_run))
        mart = self._make_mart(run_id)
        try:
            mart.load(result['comparison'], result['segments'])
            run = self.load(run_id)
            run.summary = RunSummary(**mart.decision_stats())
            self.save(run)
            self._end(run_id, 'store', f"DuckDB: {len(result['comparison'])} атрибутов, {len(result['segments'])} заявок")
            for stage in cfg.stage_order[3:]:
                if stage == 'checks':
                    self._execute_checks(run_id, mart)
                elif stage == 'llm' and cfg.llm_opinion:
                    self._execute_llm(run_id, mart)
            run = self.load(run_id)
            run.status = 'done'
            self.save(run)
        finally:
            mart.close()

    def _execute_checks(self, run_id, mart):
        run = self.load(run_id)
        prepared = checks.prepare([c.model_dump() for c in run.config.checks], run.config.user)
        snapshot = json.dumps([{k: i.get(k) for k in ('origin', 'code', 'params', 'diagnostic')} for i in prepared], ensure_ascii=False)
        self._begin(run_id, 'checks', 'checkrunner:CHECK', snapshot, total=len(prepared))
        def event(e):
            if e['status'] != 'RUNNING':
                self._event(run_id, dict(stage='checks', status='progress', completed=e['index'] + 1, total=len(prepared)))
        report = checks.execute(mart, prepared, on_event=event)
        self._save_report(run_id, report, append=False)
        count = len([e for e in report['executions'] if e['status'] != 'COMPLETED'])
        self._end(run_id, 'checks', f"{len(report['rows'])} строк, ошибок исполнения: {count}")

    def _save_report(self, run_id, report, append):
        with self._lock:
            run = self.load(run_id)
            if not append:
                run.check_rows, run.check_executions, run.errors = [], [], []
            row_offset = len(run.check_rows)
            run.check_rows.extend(checks.decorated_rows(report))
            for e in report['executions']:
                entry = dict(e, index=len(run.check_executions), row_start=e['row_start'] + row_offset)
                run.check_executions.append(entry)
                if e['status'] != 'COMPLETED':
                    run.errors.append(dict(check=e.get('origin', '?'), status=e['status'], error=e.get('diagnostic', {})))
            run.check_report_status = 'COMPLETED_WITH_ERRORS' if run.errors else 'COMPLETED'
            self.save(run)

    def try_check(self, run_id, spec):
        with self._lock:
            if run_id in self._adhoc: raise RuntimeError('Another check is running for this run')
            self._adhoc.add(run_id)
        try:
            run = self.load(run_id)
            mart = self.open_mart(run_id)
            try:
                prepared = checks.prepare([spec], run.config.user)
                def event(e):
                    with self._lock:
                        current = self.load(run_id)
                        current.active_check = dict(e)
                        self.save(current)
                event(dict(status='RUNNING', source=spec['source'], started=time.time()))
                report = checks.execute(mart, prepared, on_event=event)
                self._save_report(run_id, report, append=True)
                return dict(rows=checks.decorated_rows(report), executions=report['executions'],
                            errors=[e for e in report['executions'] if e['status'] != 'COMPLETED'], status=report['status'], persisted=True)
            finally:
                mart.close()
        finally:
            with self._lock: self._adhoc.discard(run_id)

    def _execute_llm(self, run_id, mart):
        self._begin(run_id, 'llm', 'api.llm', inspect.getsource(llm.build_summary) + '\n' + inspect.getsource(llm.ask_llm))
        run = self.load(run_id)
        try:
            opinion = llm.ask_llm(llm.build_summary(mart, run.check_rows))
        except Exception as exc:
            opinion = dict(status='unavailable', reason=f'{type(exc).__name__}: {exc}')
        run = self.load(run_id)
        run.llm = opinion
        self.save(run)
        self._end(run_id, 'llm', opinion.get('reason', 'Ответ модели получен'))
        if opinion.get('status') != 'ok':
            run = self.load(run_id)
            next(s for s in run.stages if s.name == 'llm').status = 'skipped'
            self.save(run)

    def open_mart(self, run_id):
        run = self.load(run_id)
        if run.status != 'done': raise RuntimeError('RUN_NOT_READY: прогон ещё не завершён успешно')
        path = self._mart_path(run_id)
        if not path.is_file(): raise FileNotFoundError('Stored mart is missing')
        return DuckMart(path)
