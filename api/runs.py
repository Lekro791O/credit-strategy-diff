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
        order = [s for s in cfg.stage_order if s in _STAGES]
        for n in _STAGES:  # гарантируем полный набор этапов
            if n not in order:
                order.append(n)
        run.stages = [Stage(name=n, title=_STAGE_TITLES[n]) for n in order
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
                    st.error = run.error.splitlines()[-1][:300]
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

        # --- этап 1+2: нормализация и сравнение (подписка 1) ---
        rows, segments, ref, snapshot = self._build_mart(cfg)
        mart = self._make_mart(run_id)
        mart.load(rows, segments)
        (config.RUNS_DIR / f"{run_id}.backend").write_text(
            "duck" if isinstance(mart, DuckMart) else "sqlite")
        backend = "DuckDB (подписка 2)" if isinstance(mart, DuckMart) \
            else "SQLite (встроенный)"
        self._stage_begin(run, "normalize", ref, snapshot)
        self._stage_end(run, "normalize",
                        f"{len(rows)} строк витрины, {len(segments)} заявок · {backend}")

        run = self.load(run_id)
        self._stage_begin(run, "compare", ref, snapshot)
        where_ct = (f"WHERE {mart.run_clause} AND" if mart.run_clause else "WHERE") + \
                   " CHANGE_TYPE = 'SAME'"
        same = mart.query(
            f"SELECT COUNT(*) AS n FROM {mart.fact_table} {where_ct}")[0]["n"]
        self._stage_end(run, "compare",
                        f"{same} совпадающих строк, "
                        f"{len(rows) - same} с различиями")

        # --- этап 3: факты (демонстрация агрегатов; сводная считает по запросу) ---
        run = self.load(run_id)
        facts_proc = self._procedure_callable(cfg.facts, cfg.user, "facts")
        facts_ref, facts_snap = self._procedure_ref(cfg.facts, cfg.user)
        self._stage_begin(run, "facts", facts_ref, facts_snap)
        if facts_proc is not None:
            # пользовательская процедура фактов: контракт run(comparison_rows, segments)
            # -> dict(message=..., facts=[...]) или список фактов
            out = facts_proc(rows, segments)
            msg = out.get("message", f"фактов: {len(out.get('facts', []))}") \
                if isinstance(out, dict) else f"фактов: {len(list(out))}"
            self._stage_end(run, "facts", str(msg))
        else:
            top = fact_rows(mart, ["path", "change_type"], {"diff_flag": 1}, limit=5)
            if top:
                d = top[0]["dims"]
                self._stage_end(run, "facts",
                                f"чаще всего меняется {d.get('path', '—')} "
                                f"({change_type_ru(d.get('change_type', ''))}), "
                                f"{top[0]['cnt_apps']} заявок")
            else:
                self._stage_end(run, "facts", "различий не найдено")

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
        run.errors = [{"check": e.get("check") or e.get("label") or e.get("origin", "?"),
                       "status": e.get("status", "ERROR"),
                       "error": e.get("error") or (e.get("diagnostic") or {})}
                      for e in errors]
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

    def _build_mart(self, cfg: RunConfig) -> tuple[list[dict], list[dict], str, str]:
        """Витрина: из pipeline (подписка 1), если доступен, иначе демо.

        Процедуры нормализации/сравнения: builtin (default), рабочая область
        или .py-файл — последние два загружаются как вызываемые объекты и
        передаются в pipeline.build_mart. Настройки нормализации
        (исключения, режимы массивов, бакеты) собираются в pipeline.Config.
        """
        if cfg.dataset.startswith("demo://"):
            return (*build_demo(), "builtin:api/demo_data.py",
                    "Синтетический датасет: настройки нормализации не применяются")
        try:  # ожидаемый интерфейс подписки 1
            from pipeline import build_mart  # type: ignore
            norm_arg = self._procedure_callable(cfg.normalize, cfg.user, "normalize")
            if norm_arg is None:
                norm_arg = {"kind": cfg.normalize.kind, "name": cfg.normalize.name}
                pconf = self._pipeline_config(cfg)
                if pconf is not None:
                    norm_arg["config"] = pconf
            comp_arg = self._procedure_callable(cfg.compare, cfg.user, "compare")
            if comp_arg is None:
                comp_arg = {"kind": cfg.compare.kind, "name": cfg.compare.name}
            rows, segments = build_mart(cfg.dataset, norm_arg, comp_arg)
            ref = f"normalize: {self._procedure_ref(cfg.normalize, cfg.user)[0]} · " \
                  f"compare: {self._procedure_ref(cfg.compare, cfg.user)[0]}"
            return rows, segments, ref, ""
        except ImportError:
            raise RuntimeError(
                "pipeline/ ещё не готов, а датасет не demo:// — "
                "выберите демо-датасет или дождитесь подписки 1")
        except Exception:
            raise

    # ---------- пользовательские процедуры и настройки нормализации ----------

    def _procedure_callable(self, src, user: str, stage: str):
        """Загрузить процедуру из workspace/файла как callable; None для builtin.
        Контракт модуля: функция с именем этапа (normalize/compare/facts) или run."""
        if src.kind == "builtin":
            return None
        path = self._check_path(type("S", (), {"source": src})(), user) \
            if src.kind == "workspace" else src.name
        import importlib.util
        key = f"csd_proc_{stage}_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"не удалось загрузить процедуру: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, stage, None) or getattr(mod, "run", None)
        if not callable(fn):
            raise RuntimeError(
                f"{path}: нужна функция «{stage}» или «run» — см. контракт процедур")
        return fn

    def _procedure_ref(self, src, user: str) -> tuple[str, str]:
        if src.kind == "workspace":
            p = config.WORKSPACE_DIR / user / f"{src.name}.py"
            return f"workspace:{user}/{src.name}.py", p.read_text("utf-8") if p.exists() else ""
        if src.kind == "file":
            p = Path(src.name)
            return f"file:{src.name}", p.read_text("utf-8") if p.exists() else ""
        return "builtin:default", ""

    def _bucket_fn(self, b):
        from pipeline.config import example_bucket, identity_bucket
        if not b:
            return identity_bucket
        if b.get("preset") == "example":
            return example_bucket
        rules = [(r.get("path_prefix", ""),
                  sorted(float(x) for x in r.get("edges", [])))
                 for r in (b.get("rules") or [])]
        if not rules:
            return identity_bucket

        def bucket(path, value):
            for prefix, edges in rules:
                if prefix and not str(path).startswith(prefix):
                    continue
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    return value
                prev = None
                for edge in edges:
                    if n <= edge:
                        lo = "-∞" if prev is None else f"{prev:g}"
                        return f"({lo} - {edge:g}]"
                    prev = edge
                return f"({edges[-1]:g}+)"
            return value
        return bucket

    def _pipeline_config(self, cfg: RunConfig):
        """Настройки нормализации UI -> pipeline.Config (контракт подписки 1).
        База — default_config(); пользовательские правила дополняют/переопределяют."""
        n = cfg.normalization or {}
        if not any(n.get(k) for k in ("exclude", "array_rules", "aliases", "bucket")):
            return None
        from pipeline import ArrayRule
        from pipeline.config import default_config
        conf = default_config()
        if n.get("exclude"):
            conf.exclude = tuple(n["exclude"])
        for r in n.get("array_rules", []):
            conf.arrays[r["path"]] = ArrayRule(
                mode=r.get("mode", "field"),
                keys=tuple(r.get("keys", [])),
                element_name=r.get("element_name", "") or "",
                scalar_key=r.get("scalar_key") or None)
        if n.get("aliases"):
            conf.aliases = dict(n["aliases"])
        conf.bucket = self._bucket_fn(n.get("bucket"))
        return conf

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
        id_by_label = {e.get("label"): e.get("check_id")
                       for e in report["executions"] if e.get("check_id")}
        rows = [dict(r, _check_id=id_by_label.get(r.get("LABEL"), "?"),
                     _ref=e) for r in report["rows"]]
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
