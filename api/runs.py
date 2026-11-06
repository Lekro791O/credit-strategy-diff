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
from .facts import fact_rows
from .mart import SQLiteMart
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

        # --- этап 1+2: нормализация и сравнение (подписка 1) ---
        rows, segments, ref, snapshot = self._build_mart(cfg)
        mart = SQLiteMart(mart_path)
        mart.load(rows, segments)
        self._stage_begin(run, "normalize", ref, snapshot)
        self._stage_end(run, "normalize",
                        f"{len(rows)} строк витрины, {len(segments)} заявок")

        run = self.load(run_id)
        self._stage_begin(run, "compare", ref, snapshot)
        same = mart.conn.execute(
            "SELECT COUNT(*) FROM mart WHERE CHANGE_TYPE = 'SAME'").fetchone()[0]
        self._stage_end(run, "compare",
                        f"{same} совпадающих строк, "
                        f"{len(rows) - same} с различиями")

        # --- этап 3: факты (демонстрация агрегатов; сводная считает по запросу) ---
        run = self.load(run_id)
        self._stage_begin(run, "facts", "api/facts.py", "")
        top = fact_rows(mart, ["path", "change_type"], limit=5)
        self._stage_end(run, "facts",
                        f"топ-группа: {top[0]['dims'] if top else '—'} "
                        f"({top[0]['cnt_apps'] if top else 0} заявок)")

        # --- этап 4: проверки ---
        run = self.load(run_id)
        specs = [c.model_dump() for c in cfg.checks]
        code_refs = "\n".join(f"• {c.source.kind}:{c.source.name}" for c in cfg.checks)
        self._stage_begin(run, "checks", "см. конфигурацию прогона", code_refs)
        check_rows, errors = checks_mod.run_checks(mart, specs, cfg.user)
        run = self.load(run_id)
        run.check_rows = check_rows
        fails = sum(1 for r in check_rows if r.get("RESULT") == "FAIL")
        msg = f"{len(check_rows)} строк, {fails} FAIL"
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
            mart2 = SQLiteMart(mart_path)
            opinion = llm_mod.ask_llm(llm_mod.build_summary(mart2, run.check_rows))
            mart2.close()
            run = self.load(run_id)
            run.llm = opinion
            self._stage_end(run, "llm", opinion.get("status", "?"))

    def _build_mart(self, cfg: RunConfig) -> tuple[list[dict], list[dict], str, str]:
        """Витрина: из pipeline (подписка 1), если доступен, иначе демо."""
        if cfg.dataset.startswith("demo://"):
            return (*build_demo(), "builtin:api/demo_data.py",
                    "Синтетический датасет: pipeline ещё не подключён")
        try:  # ожидаемый интерфейс подписки 1
            from pipeline import build_mart  # type: ignore
            rows, segments = build_mart(
                cfg.dataset,
                cfg.normalize.model_dump(), cfg.compare.model_dump())
            return rows, segments, "pipeline:build_mart", ""
        except ImportError:
            raise RuntimeError(
                "pipeline/ ещё не готов, а датасет не demo:// — "
                "выберите демо-датасет или дождитесь подписки 1")
        except Exception:
            raise

    # ---------- доступ к витрине готового прогона ----------

    def open_mart(self, run_id: str) -> SQLiteMart:
        mart = SQLiteMart()
        mart.path = self._mart_path(run_id)
        mart.reopen()
        return mart
