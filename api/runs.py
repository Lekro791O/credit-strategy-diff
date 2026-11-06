"""Менеджер прогонов: фоновое выполнение, этапы, журнал, персистентность.

Прогон неблокирующий: HTTP-запрос только ставит задачу, выполнение идёт
в фоне (asyncio.to_thread для синхронного кода), фронт опрашивает статус.
Состояние сериализуется в data/runs/<id>.json — перезагрузка страницы
(и перезапуск процесса между этапами) не теряет результат.

Интеграция с подпиской 1 (pipeline/): ожидаемый интерфейс —
    from pipeline import build_mart
    rows, segments = build_mart(dataset_dir, normalize_cfg, compare_cfg)
где rows — список dict по схеме task/docs/СХЕМА_ВИТРИНЫ.md,
segments — список dict по слою разрезов. Если pipeline ещё не готов
или датасет demo:// — строим демо-витрину и помечаем это в журнале.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from . import checks as checks_mod
from . import config, llm as llm_mod, workspace
from .demo_data import build_demo
from .facts import change_type_ru, fact_rows
from .mart import DuckMart, SQLiteMart
from .models import Run, RunConfig, RunSummary, Stage

_STAGES = ["normalize", "compare", "facts", "checks", "llm"]
_STAGE_TITLES = {
    "normalize": "Нормализация ответов в базовый формат",
    "compare": "Сопоставление срезов и сравнение",
    "facts": "Подготовка фактов counts для отчёта",
    "checks": "Исполнение автоматических проверок",
    "llm": "Мнение языковой модели",
}


class RunManager:
    def __init__(self) -> None:
        config.ensure_dirs()

    # ---------- persistence ----------

    def _run_path(self, run_id: str) -> Path:
        return config.RUNS_DIR / f"{run_id}.json"

    def _mart_path(self, run_id: str) -> Path:
        return config.MART_DIR / f"{run_id}.sqlite"

    def save(self, run: Run) -> None:
        self._run_path(run.id).write_text(
            run.model_dump_json(), encoding="utf-8")

    def load(self, run_id: str) -> Run:
        return Run.model_validate_json(self._run_path(run_id).read_text("utf-8"))

    def list_runs(self, user: str | None = None) -> list[Run]:
        runs = []
        for f in sorted(config.RUNS_DIR.glob("*.json"), reverse=True):
            try:
                r = Run.model_validate_json(f.read_text("utf-8"))
            except Exception:
                continue
            if user and r.config.user != user:
                continue
            runs.append(r)
        return runs

    # ---------- конфигурации ----------

    def _configs_path(self, user: str) -> Path:
        return config.DATA_DIR / f"configs-{user}.json"

    def list_configs(self, user: str) -> list[dict[str, Any]]:
        p = self._configs_path(user)
        return json.loads(p.read_text("utf-8")) if p.exists() else []

    def save_config(self, user: str, cfg: dict[str, Any]) -> None:
        cfgs = self.list_configs(user)
        cfg = {**cfg, "user": user}
        cfgs = [c for c in cfgs if c.get("name") != cfg.get("name")]
        cfgs.append(cfg)
        self._configs_path(user).write_text(
            json.dumps(cfgs, ensure_ascii=False, indent=1), encoding="utf-8")

    def delete_config(self, user: str, name: str) -> None:
        cfgs = [c for c in self.list_configs(user) if c.get("name") != name]
        self._configs_path(user).write_text(
            json.dumps(cfgs, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- запуск ----------

    def create(self, cfg: RunConfig) -> Run:
        run = Run(id=uuid.uuid4().hex[:12], config=cfg)
        run.stages = [Stage(name=n, title=_STAGE_TITLES[n]) for n in _STAGES
                      if not (n == "llm" and not cfg.llm_opinion)]
        self.save(run)
        return run

    def start(self, run_id: str) -> None:
        """Фоновый запуск в потоке: HTTP-запрос не ждёт завершения прогона."""
        threading.Thread(target=self._run_guarded, args=(run_id,),
                         daemon=True).start()

    def _run_guarded(self, run_id: str) -> None:
        try:
            self._run_sync(run_id)
        except Exception:
            run = self.load(run_id)
            run.status = "error"
            run.error = traceback.format_exc(limit=5)
            for st in run.stages:
                if st.status == "running":
                    st.status = "error"
            self.save(run)

    def _run_sync(self, run_id: str) -> None:
        run = self.load(run_id)
        run.status = "running"
        self.save(run)
        self._run_body(run_id)

    def _stage_begin(self, run: Run, name: str, code_ref: str,
                     code_snapshot: str) -> None:
        st = next(s for s in run.stages if s.name == name)
        st.status, st.started = "running", time.time()
        st.code_ref, st.code_snapshot = code_ref, code_snapshot
        self.save(run)

    def _stage_end(self, run: Run, name: str, message: str = "") -> None:
        st = next(s for s in run.stages if s.name == name)
        st.status, st.finished = "done", time.time()
        st.message = message
        self.save(run)

    def _run_body(self, run_id: str) -> None:
        run = self.load(run_id)
        cfg = run.config
        mart_path = self._mart_path(run_id)
        if mart_path.exists():
            mart_path.unlink()

        # The pipeline emits stage events while each procedure is actually running.
        def pipeline_event(event):
            stage = event['stage']
            current = self.load(run_id)
            if event['status'] == 'start':
                self._stage_begin(current, stage, event.get('code_ref', 'pipeline:' + stage), event.get('code_snapshot', ''))
            elif event['status'] == 'finish':
                if stage == 'facts':
                    message = f"Подготовлено групп фактов: {event.get('facts', 0)}"
                else:
                    message = f"Корректных пар: {event.get('eligible', 0)}"
                self._stage_end(current, stage, message)
        result = self._build_dataset(cfg, event=pipeline_event)
        rows, segments = result['comparison'], result['segments']
        run = self.load(run_id)
        run.quality, run.diagnostics = result['quality'], result['diagnostics']
        run.fact_rows, run.segment_details = result['facts'], segments
        self.save(run)
        mart = self._make_mart(run_id)
        mart.load(rows, segments)
        (config.RUNS_DIR / f"{run_id}.backend").write_text(
            "duck" if isinstance(mart, DuckMart) else "sqlite")

        # --- этап 4: проверки ---
        run = self.load(run_id)
        code_refs = "\n".join(f"• {c.source.kind}:{c.source.name}" for c in cfg.checks)
        self._stage_begin(run, "checks", "см. конфигурацию прогона", code_refs)
        if isinstance(mart, DuckMart):
            check_rows, errors, run_backend = self._run_checks_isolated(mart, cfg)
        else:
            specs = [c.model_dump() for c in cfg.checks]
            check_rows, errors = checks_mod.run_checks(mart, specs, cfg.user)
            run_backend = "в процессе приложения"
        run = self.load(run_id)
        run.check_rows = check_rows
        fails = sum(1 for r in check_rows if r.get("RESULT") == "FAIL")
        msg = f"{len(check_rows)} строк, {fails} FAIL · исполнение: {run_backend}"
        if errors:
            msg += f", ошибок исполнения: {len(errors)}"
        self._stage_end(run, "checks", msg)

        # --- итог ---
        run = self.load(run_id)
        run.summary = RunSummary(**mart.decision_stats())
        run.status = "done"
        self.save(run)
        mart.close()

        # --- этап 5: ИИ-мнение (не влияет на статус прогона) ---
        if cfg.llm_opinion:
            run = self.load(run_id)
            self._stage_begin(run, "llm", "api/llm.py", "")
            mart2 = self.open_mart(run_id)
            opinion = llm_mod.ask_llm(llm_mod.build_summary(mart2, run.check_rows))
            mart2.close()
            run = self.load(run_id)
            run.llm = opinion
            if opinion.get("status") == "ok":
                self._stage_end(run, "llm", f"модель {opinion.get('model', '?')} ответила")
            else:
                st = next(s for s in run.stages if s.name == "llm")
                st.status = "skipped"
                st.message = ("модель не настроена — задайте CSD_LLM_API_KEY, "
                              "CSD_LLM_BASE_URL и CSD_LLM_MODEL (см. README)")
                st.finished = time.time()
                self.save(run)

    def _build_dataset(self, cfg, event=None):
        from pipeline import build_dataset
        from .procedures import resolve_procedure
        def resolver(spec, stage, default):
            return resolve_procedure(spec, stage, default, user=cfg.user,
                                     settings=cfg.normalize.settings)
        def process(path):
            return build_dataset(path, cfg.normalize.model_dump(), cfg.compare.model_dump(),
                                 cfg.facts.model_dump(), event=event, resolver=resolver,
                                 expected_app_ids=cfg.expected_app_ids)
        if not cfg.dataset.startswith('demo://'):
            return process(cfg.dataset)
        # Demo is a real JSON input to the same eligibility/normalization pipeline.
        import tempfile
        from .demo_data import demo_documents
        with tempfile.TemporaryDirectory(dir=config.DATA_DIR, prefix='demo-input-') as name:
            root = Path(name)
            (root/'BEFORE').mkdir(); (root/'AFTER').mkdir()
            for app_id, before, after in demo_documents():
                for source, document in [('BEFORE', before), ('AFTER', after)]:
                    (root/source/(app_id+'.json')).write_text(json.dumps(document), encoding='utf-8')
            return process(root)

    # ---------- доступ к витрине готового прогона ----------

    def _make_mart(self, run_id: str):
        """DuckDB-хранилище подписки 2, если стек доступен на этой машине;
        иначе встроенный SQLiteMart. (mart.store требует fcntl — на Windows
        импорт падает, и приложение честно работает на фолбэке.)"""
        duck = DuckMart(self._mart_path(run_id).with_suffix(".duckdb"))
        try:
            duck.probe()
            return duck
        except Exception:
            return SQLiteMart(self._mart_path(run_id))

    def _check_path(self, spec, user: str) -> str:
        src = spec.source
        if src.kind == "workspace":
            return str(config.WORKSPACE_DIR / user / f"{src.name}.py")
        if src.kind == "file":
            return src.name
        return str(config.BUILTIN_CHECKS_DIR / src.name)

    def _run_checks_isolated(self, mart: DuckMart, cfg: RunConfig):
        """Проверки подписки 2: отдельный процесс на проверку, снапшот
        витрины, таймаут, валидация строк и APP_IDS."""
        from checkrunner import CheckRunner, CheckSpec
        specs = [CheckSpec(path=self._check_path(c, cfg.user),
                           params=dict(c.params))
                 for c in cfg.checks if c.enabled]
        runner = CheckRunner(backend="subprocess", trusted=True, timeout=120)
        report = runner.run(store=mart._ensure_store(), run_id=DuckMart.RUN_ID,
                            checks=specs)
        rows = []
        for execution in report['executions']:
            start, count = execution.get('row_start', 0), execution.get('row_count', 0)
            rows.extend(dict(row, _check_id=execution.get('check_id', '?'),
                             _ref=execution.get('origin', ''))
                        for row in report['rows'][start:start+count])
        errors = [e for e in report["executions"] if e["status"] != "COMPLETED"]
        return rows, errors, "изолированный процесс (checkrunner/)"

    def open_mart(self, run_id: str):
        marker = config.RUNS_DIR / f"{run_id}.backend"
        duck_path = self._mart_path(run_id).with_suffix(".duckdb")
        backend = marker.read_text().strip() if marker.exists() else (
            "duck" if duck_path.exists() else "sqlite")
        if backend == "duck":
            return DuckMart(duck_path)
        mart = SQLiteMart()
        mart.path = self._mart_path(run_id)
        mart.reopen()
        return mart
