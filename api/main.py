"""FastAPI-приложение: API модулей 3-4 + раздача фронтенда.

Запуск: uvicorn api.main:app --port 8000
Открыть: http://localhost:8000
"""
from __future__ import annotations

from typing import Any
import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import checks as checks_mod
from . import config, llm as llm_mod, workspace
from .export_html import render_checks_html
from .facts import DIMS, apps_in_group, fact_rows, fact_result
from .models import CheckSpec, ProcedureSource, RunConfig, PivotRequest
from .runs import RunManager

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    manager.recover_interrupted()
    yield

app = FastAPI(lifespan=lifespan, title="Система анализа изменений СПР", version="0.1.0")
manager = RunManager()


def _user(x_user=None):
    # X-User-Id is a display preference only, not proof of ownership.
    from .access import current_user
    user = current_user.get()
    if not user:
        raise HTTPException(401, "SESSION_REQUIRED")
    return user


@app.middleware("http")
async def access_boundary(request: Request, call_next):
    from .access import identity, current_user, COOKIE
    if request.url.path != '/' and not request.url.path.startswith('/api/'):
        return await call_next(request)
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': {'code': 'ORIGIN_DENIED', 'message': 'Cross-origin writes are not allowed'}}, status_code=403)
    user, cookie = identity(request.cookies.get(COOKIE))
    token = current_user.set(user)
    try:
        parts = request.url.path.split('/')
        if len(parts) >= 4 and parts[2] == 'runs' and parts[3]:
            try:
                run = manager.load(parts[3])
                if run.config.user != user:
                    raise FileNotFoundError()
            except (FileNotFoundError, ValueError):
                return JSONResponse({'detail': {'code': 'NOT_FOUND', 'message': 'Прогон не найден'}}, status_code=404)
        response = await call_next(request)
        if cookie != request.cookies.get(COOKIE):
            response.set_cookie(COOKIE, cookie, httponly=True, samesite='strict',
                                secure=request.url.scheme == 'https', max_age=30 * 86400)
        return response
    finally:
        current_user.reset(token)


@app.exception_handler(FileNotFoundError)
async def missing_resource(request, exc):
    return JSONResponse({'detail': {'code': 'NOT_FOUND', 'message': 'Ресурс не найден'}}, status_code=404)


@app.exception_handler(ValueError)
async def invalid_input(request, exc):
    return JSONResponse({'detail': {'code': 'INVALID_INPUT', 'message': str(exc)}}, status_code=422)


@app.exception_handler(RuntimeError)
async def unavailable_state(request, exc):
    return JSONResponse({'detail': {'code': 'CONFLICT', 'message': str(exc)}}, status_code=409)


@app.get('/api/session')
def session_info():
    return {'user_id': _user(), 'access_model': 'signed-browser-session', 'header_identity': False}


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


@app.post("/api/datasets/upload")
def upload_dataset(file: UploadFile, name: str = "") -> dict[str, Any]:
    """Загрузка датасета zip-архивом: появится в списке датасетов."""
    from . import datasets as datasets_mod
    data = file.file.read()
    try:
        return datasets_mod.import_zip(data, name or Path(file.filename or "").stem)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/builtin/checks")
def builtin_checks() -> dict[str, Any]:
    return {"checks": checks_mod.builtin_checks()}


@app.get('/api/builtin/checks/{name}')
def builtin_check_source(name: str):
    item = next((b for b in checks_mod.builtin_checks() if name in (b['id'], b['file'])), None)
    if item is None:
        raise HTTPException(404, 'Unknown builtin check')
    return dict(item, code=(config.BUILTIN_CHECKS_DIR / item['file']).read_text(encoding='utf8'))


@app.get('/api/builtin/procedures/{stage}')
def builtin_procedure_source(stage: str):
    import inspect
    from pipeline import normalize, compare, aggregate
    definitions = {'normalize': (normalize, 'document, *, app_id, source, config', 'document, app_id=app_id, source=source, config=config'),
                   'compare': (compare, 'before_rows, after_rows', 'before_rows, after_rows'),
                   'facts': (aggregate, 'comparison_rows', 'comparison_rows')}
    if stage not in definitions:
        raise HTTPException(404, 'Unknown procedure stage')
    fn, signature, args = definitions[stage]
    code = (f'from pipeline import {fn.__name__} as default_handler\n\n'
            f'def run({signature}):\n    return default_handler({args})\n\n'
            f"PROCEDURE = {{'stage': '{fn.__name__}', 'entrypoint': run}}\n")
    return {'stage': stage, 'code': code, 'builtin_code': inspect.getsource(fn),
            'code_ref': fn.__module__ + ':' + fn.__name__}


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


@app.get('/api/configs/{name}')
def get_config(name: str):
    return {'config': manager.load_config(_user(), name).model_dump()}


@app.post('/api/configs/{name}/run')
def run_config(name: str):
    return create_run(manager.load_config(_user(), name))


@app.post('/api/runs/{run_id}/repeat')
def repeat_run(run_id: str):
    run = manager.repeat(run_id)
    manager.start(run.id)
    return {'run': run.model_dump()}


# ---------- прогоны ----------

@app.post("/api/runs")
def create_run(cfg: RunConfig,
               x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    cfg.user = _user(x_user_id)
    from .access import validate_dataset
    validate_dataset(cfg.dataset, cfg.user)
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
def facts(run_id: str, request: Request, dims: str = Query(default="path,change_type"),
          limit: int = 5000, offset: int = 0) -> dict[str, Any]:
    run = manager.load(run_id)
    filters: dict[str, Any] = {k: v for k, v in request.query_params.items()
                               if k not in ('dims', 'limit', 'offset') and v != ''}
    if 'diff_flag' in filters:
        filters['diff_flag'] = int(filters['diff_flag'])
        if filters['diff_flag'] == -1:
            filters.pop('diff_flag')
    custom_facts = run.config.facts.kind != 'builtin' or run.config.facts.name != 'default'
    mart = manager.open_mart(run_id)
    try:
        return fact_result(mart, [d.strip() for d in dims.split(',') if d.strip()],
                           filters, limit=limit, offset=offset,
                           segment_rows=run.segment_details,
                           fact_scope=run.fact_rows if custom_facts else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    finally:
        mart.close()


@app.get("/api/runs/{run_id}/segment_values")
def segment_values(run_id: str) -> dict[str, Any]:
    """Допустимые значения фильтров разрезов — для выпадающих списков."""
    run = manager.load(run_id)
    products, partners, routings, models, cutoffs = set(), set(), set(), set(), set()
    for s in run.segment_details:
        for piece in (s.get("PRODUCTS") or "").replace(",", ";").split(";"):
            if piece.strip():
                products.add(piece.strip())
        for k, target in (("PARTNER_TYPE", partners), ("ROUTING_METHOD", routings),
                          ("PD_MODEL_BEFORE", models), ("PD_MODEL_AFTER", models),
                          ("CUTOFF_BEFORE", cutoffs), ("CUTOFF_AFTER", cutoffs)):
            if s.get(k):
                target.add(str(s[k]))
    return {"product": sorted(products), "partner": sorted(partners),
            "routing": sorted(routings), "pd_model": sorted(models),
            "cutoff": sorted(cutoffs)}


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
    run = manager.load(run_id)
    return {'rows': run.check_rows, 'errors': run.errors, 'executions': run.check_executions,
            'status': run.check_report_status}


@app.post("/api/runs/{run_id}/checks/try")
def try_check(run_id: str, body: dict[str, Any]):
    source = body.get('source') or {k: body[k] for k in ('kind', 'name', 'code') if k in body}
    source.setdefault('kind', 'code')
    source.setdefault('name', 'ad_hoc')
    spec = CheckSpec(source=ProcedureSource.model_validate(source), params=body.get('params', {}))
    result = manager.try_check(run_id, spec.model_dump())
    result.setdefault('check_id', spec.source.name)
    return result


@app.post("/api/runs/{run_id}/checks/add_result")
def add_check_result(run_id: str, body: dict[str, Any]):
    # Legacy UI sends the returned rows after /try. They are already persisted.
    # Never accept client-forged PASS/FAIL rows as execution evidence.
    run = manager.load(run_id)
    rows = body.get('rows', [])
    if any(row not in run.check_rows for row in rows):
        raise HTTPException(422, 'UNVERIFIED_RESULT: execute the check through /checks/try')
    return {'ok': True, 'total': len(run.check_rows), 'already_saved': True}


@app.get("/api/runs/{run_id}/checks/export.html", response_class=HTMLResponse)
def export_checks(run_id: str, request: Request) -> HTMLResponse:
    run = manager.load(run_id).model_dump()
    url = str(request.base_url).rstrip("/") + f"/#/runs/{run_id}"
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
    with manager._lock:
        run = manager.load(run_id)
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
