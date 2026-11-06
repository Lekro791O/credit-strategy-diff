"""FastAPI-приложение: API модулей 3-4 + раздача фронтенда.

Запуск: uvicorn api.main:app --port 8000
Открыть: http://localhost:8000
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import checks as checks_mod
from . import config, llm as llm_mod, workspace
from .export_html import render_checks_html
from .facts import DIMS, apps_in_group, fact_rows, fact_result
from .models import CheckSpec, ProcedureSource, RunConfig, PivotRequest
from .runs import RunManager

app = FastAPI(title="Система анализа изменений СПР", version="0.1.0")
manager = RunManager()


def _user(x_user: str | None) -> str:
    u = (x_user or "anon").strip()
    if not u or any(ch in u for ch in "/\\.."):
        raise HTTPException(400, "некорректное имя пользователя")
    return u


# ---------- справочники ----------

@app.get("/api/datasets")
def datasets() -> dict[str, Any]:
    """Доступные датасеты: демо всегда; выданный — если положен в data/."""
    found = []
    for d in sorted(config.DATA_DIR.glob("RUN-*")):
        # оба допустимых layout: RESULTS/BEFORE_MASTER+AFTER_BETA (прогон) и BEFORE+AFTER (samples)
        for before_name, after_name in (("BEFORE_MASTER", "AFTER_BETA"), ("BEFORE", "AFTER")):
            before = d / "RESULTS" / before_name
            if not before.exists():
                before = d / before_name
            after = d / "RESULTS" / after_name if (d / "RESULTS" / after_name).exists() \
                else d / after_name
            if before.is_dir() and after.is_dir():
                found.append({"id": str(d),
                              "apps": len(list(before.glob("*.json"))),
                              "requires_pipeline": True})
                break
    return {"datasets": [{"id": config.DEMO_DATASET, "apps": 300,
                          "requires_pipeline": False}] + found}


@app.get("/api/builtin/checks")
def builtin_checks() -> dict[str, Any]:
    return {"checks": checks_mod.builtin_checks()}


@app.get("/api/facts/dims")
def fact_dims() -> dict[str, Any]:
    return {"dims": sorted(DIMS)}


@app.get('/api/normalization/schema')
def normalization_schema():
    import json
    import pipeline
    from pathlib import Path
    return json.loads((Path(pipeline.__file__).parent/'normalization.schema.json').read_text())


@app.get('/api/normalization/defaults')
def normalization_defaults():
    from pipeline import default_config
    cfg = default_config()
    return {'version': 1, 'exclude': list(cfg.exclude),
            'arrays': {path: {'mode': rule.mode, 'keys': list(rule.keys)} for path, rule in cfg.arrays.items()},
            'buckets': {}}


# ---------- конфигурации ----------

@app.get("/api/configs")
def get_configs(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    return {"configs": manager.list_configs(_user(x_user_id))}


@app.post("/api/configs")
def post_config(cfg: dict[str, Any],
                x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    manager.save_config(_user(x_user_id), cfg)
    return {"ok": True}


@app.delete("/api/configs/{name}")
def del_config(name: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    manager.delete_config(_user(x_user_id), name)
    return {"ok": True}


# ---------- прогоны ----------

@app.post("/api/runs")
def create_run(cfg: RunConfig,
               x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    cfg.user = _user(x_user_id)
    from pipeline import config_from_settings
    try:
        config_from_settings(cfg.normalize.settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not cfg.checks:
        # разумный дефолт: все выданные проверки
        cfg.checks = [CheckSpec(
            source=ProcedureSource(kind="builtin", name=b["file"]),
            params=b.get("params", {}), enabled=True)
            for b in checks_mod.builtin_checks() if not b.get("error")]
    run = manager.create(cfg)
    manager.start(run.id)
    return {"run": run.model_dump()}


@app.get("/api/runs")
def list_runs(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    runs = manager.list_runs(_user(x_user_id))
    return {"runs": [r.model_dump() for r in runs]}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return {"run": manager.load(run_id).model_dump()}
    except Exception:
        raise HTTPException(404, "прогон не найден")


# ---------- факты / drill-down ----------

@app.get("/api/runs/{run_id}/facts")
def facts(run_id: str, dims: str = Query(default="path,change_type"),
          path_prefix: str = "", change_type: str = "", diff_flag: int = -1,
          key: str = "", limit: int = 5000, column_dimension: str | None = None) -> dict[str, Any]:
    mart = manager.open_mart(run_id)
    try:
        filters: dict[str, Any] = {}
        if path_prefix:
            filters["path_prefix"] = path_prefix
        if change_type:
            filters["change_type"] = change_type
        if diff_flag >= 0:
            filters["diff_flag"] = diff_flag
        if key:
            filters["key"] = key
        run = manager.load(run_id)
        custom_facts = run.config.facts.kind != 'builtin' or run.config.facts.name != 'default'
        return fact_result(mart, [d.strip() for d in dims.split(',') if d.strip()],
                           filters, limit=limit, column_dimension=column_dimension,
                           segment_rows=run.segment_details, fact_scope=run.fact_rows if custom_facts else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        mart.close()


@app.get("/api/runs/{run_id}/apps")
def apps(run_id: str, request: Request, offset: int = 0, limit: int = 200):
    filters = dict(request.query_params)
    filters.pop('offset', None); filters.pop('limit', None)
    if 'diff_flag' in filters:
        filters['diff_flag'] = int(filters['diff_flag'])
        if filters['diff_flag'] == -1:
            filters.pop('diff_flag')
    run = manager.load(run_id)
    custom = run.config.facts.kind != 'builtin' or run.config.facts.name != 'default'
    mart = manager.open_mart(run_id)
    try:
        return apps_in_group(mart, {}, filters, offset, limit,
                             segment_rows=run.segment_details, fact_scope=run.fact_rows if custom else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        mart.close()


@app.post('/api/runs/{run_id}/pivot')
def pivot(run_id: str, body: PivotRequest):
    run = manager.load(run_id)
    mart = manager.open_mart(run_id)
    custom = run.config.facts.kind != 'builtin' or run.config.facts.name != 'default'
    try:
        return fact_result(mart, body.dimensions, body.filters,
                           segment_filters=body.segment_filters, segment_rows=run.segment_details,
                           column_dimension=body.column_dimension, limit=body.limit, offset=body.offset,
                           fact_scope=run.fact_rows if custom else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        mart.close()


@app.post('/api/runs/{run_id}/pivot/apps')
def pivot_apps(run_id: str, body: PivotRequest):
    run = manager.load(run_id)
    mart = manager.open_mart(run_id)
    custom = run.config.facts.kind != 'builtin' or run.config.facts.name != 'default'
    try:
        return apps_in_group(mart, {}, body.filters, body.offset, body.limit,
                             segment_filters=body.segment_filters, segment_rows=run.segment_details,
                             fact_scope=run.fact_rows if custom else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        mart.close()


@app.get('/api/runs/{run_id}/quality')
def run_quality(run_id: str):
    run = manager.load(run_id)
    return {'quality': run.quality, 'diagnostics': run.diagnostics}


@app.get("/api/runs/{run_id}/apps/{app_id}")
def app_detail(run_id: str, app_id: str) -> dict[str, Any]:
    mart = manager.open_mart(run_id)
    try:
        rows = mart.app_attribute_rows(app_id)
        seg = next((s for s in mart.segments() if s["APP_ID"] == app_id), None)
        return {"app_id": app_id, "attributes": rows, "segment": seg}
    finally:
        mart.close()


@app.get("/api/runs/{run_id}/decision_matrix")
def decision_matrix(run_id: str) -> dict[str, Any]:
    """Матрица переходов решений BEFORE → AFTER (2×2) с заявками в ячейках."""
    mart = manager.open_mart(run_id)
    try:
        where = f" WHERE {mart.run_clause}" if mart.run_clause else ""
        rows = mart.query(
            "SELECT APP_ID, DECISION_BEFORE, DECISION_AFTER FROM segments" + where)
        cells: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = f"{r.get('DECISION_BEFORE') or '?'}→{r.get('DECISION_AFTER') or '?'}"
            cell = cells.setdefault(key, {"before": r.get("DECISION_BEFORE") or "?",
                                          "after": r.get("DECISION_AFTER") or "?",
                                          "cnt": 0, "app_ids": []})
            cell["cnt"] += 1
            if len(cell["app_ids"]) < 500:
                cell["app_ids"].append(r["APP_ID"])
        total = sum(c["cnt"] for c in cells.values())
        return {"cells": cells, "total": total}
    finally:
        mart.close()


# ---------- проверки ----------

@app.get("/api/runs/{run_id}/checks")
def run_checks(run_id: str) -> dict[str, Any]:
    return {"rows": manager.load(run_id).check_rows}


@app.post("/api/runs/{run_id}/checks/try")
def try_check(run_id: str, body: dict[str, Any],
              x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    """Проверка «на лету»: код из workspace/тела — сразу против витрины прогона.

    Сценарий демо: написали новую проверку в приложении → применили →
    она попала в тот же отчёт.
    """
    mart = manager.open_mart(run_id)
    try:
        kind, name = body.get("kind", "code"), body.get("name", "ad_hoc")
        if kind == "workspace":
            code = workspace.read_workspace(_user(x_user_id), name)
            ref = f"workspace:{name}"
        else:
            code = body.get("code", "")
            ref = "inline"
        check = checks_mod.load_check_from_code(code, ref)
        params = {**check.get("params", {}), **body.get("params", {})}
        rows = [dict(r, _check_id=check.get("id", "?"), _ref=ref)
                for r in (check["entrypoint"](mart, params) or [])]
        return {"rows": rows, "check_id": check.get("id")}
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        mart.close()


@app.post("/api/runs/{run_id}/checks/add_result")
def add_check_result(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Дописать строки ad-hoc проверки в отчёт готового прогона."""
    run = manager.load(run_id)
    for r in body.get("rows", []):
        run.check_rows.append(r)
    manager.save(run)
    return {"ok": True, "total": len(run.check_rows)}


@app.get("/api/runs/{run_id}/checks/export.html", response_class=HTMLResponse)
def export_checks(run_id: str) -> HTMLResponse:
    run = manager.load(run_id).model_dump()
    url = f"http://localhost:8000/#/runs/{run_id}"
    resp = HTMLResponse(render_checks_html(run, url))
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# ---------- ИИ-мнение ----------

@app.get("/api/llm/config")
def llm_config() -> dict[str, Any]:
    """Настроена ли модель — чтобы UI честно показывал, будет ли шаг ИИ."""
    from . import config as app_config
    return {"configured": app_config.llm_configured()}


@app.post("/api/runs/{run_id}/llm")
def llm_opinion(run_id: str) -> dict[str, Any]:
    run = manager.load(run_id)
    if run.llm and run.llm.get("status") == "ok":
        return {"llm": run.llm}
    mart = manager.open_mart(run_id)
    try:
        opinion = llm_mod.ask_llm(llm_mod.build_summary(mart, run.check_rows))
    finally:
        mart.close()
    run.llm = opinion
    manager.save(run)
    return {"llm": opinion}


# ---------- рабочая область ----------

@app.get("/api/workspace")
def ws_list(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    return {"files": workspace.list_workspace(_user(x_user_id))}


@app.get("/api/workspace/{name}")
def ws_read(name: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    return {"name": name, "code": workspace.read_workspace(_user(x_user_id), name)}


@app.put("/api/workspace/{name}")
def ws_save(name: str, body: dict[str, Any],
            x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        workspace.save_workspace(_user(x_user_id), name, body.get("code", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/workspace/{name}")
def ws_delete(name: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    workspace.delete_workspace(_user(x_user_id), name)
    return {"ok": True}


# ---------- фронтенд ----------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = config.FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>frontend/ не найден</h1>", status_code=404)


app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR / "static"),
          name="static")
