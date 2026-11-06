"""Исполнение пользовательских проверок.

Контракт проверки (task/checks/README.md): файл экспортирует
CHECK = {"id", "label", "params", "entrypoint": run}, где
run(mart, params) -> list[dict] со строками LABEL/SOURCE/ARGUMENT/CNT/
TOTAL/METRIC/APP_IDS/RESULT/CONFIDENCE.

Источники кода: встроенные (task/checks/*.py), рабочая область
(data/workspace/<user>/<name>.py), произвольный размещённый .py-файл.
Код исполняется в этом процессе через exec — для хакатонного прототипа
приемлемо; изоляция (subprocess/песочница) — следующий шаг.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Any

from .config import BUILTIN_CHECKS_DIR, WORKSPACE_DIR
from .mart import SQLiteMart


def load_check_from_code(code: str, ref: str) -> dict[str, Any]:
    """Компилирует код проверки и извлекает CHECK. Исключения — с контекстом."""
    ns: dict[str, Any] = {}
    try:
        exec(compile(code, ref, "exec"), ns)
    except Exception:
        raise ValueError(f"ошибка компиляции {ref}:\n{traceback.format_exc()}")
    check = ns.get("CHECK")
    if not isinstance(check, dict) or "entrypoint" not in check:
        raise ValueError(f"{ref}: нет объекта CHECK с entrypoint (см. task/checks/README.md)")
    return check


def load_check_from_file(path: Path) -> dict[str, Any]:
    return load_check_from_code(path.read_text(encoding="utf-8"), str(path))


def builtin_checks() -> list[dict[str, Any]]:
    """Выданные организаторами проверки — для выбора в конфигурации."""
    out = []
    for f in sorted(BUILTIN_CHECKS_DIR.glob("check_*.py")):
        try:
            c = load_check_from_file(f)
            out.append({"id": c["id"], "label": c.get("label", c["id"]),
                        "params": c.get("params", {}), "file": f.name})
        except ValueError as e:
            out.append({"id": f.stem, "label": f"[ошибка загрузки] {f.name}",
                        "params": {}, "file": f.name, "error": str(e)})
    return out


def resolve_check(spec: dict[str, Any], user: str) -> tuple[dict[str, Any], str]:
    """spec = {source: {kind, name}, ...} -> (CHECK, ref для журнала)."""
    src = spec.get("source", {"kind": "builtin", "name": ""})
    kind, name = src.get("kind", "builtin"), src.get("name", "")
    if kind == "workspace":
        path = WORKSPACE_DIR / user / f"{name}.py"
        return load_check_from_file(path), f"workspace:{user}/{name}.py"
    if kind == "file":
        path = Path(name)
        if not path.is_absolute():
            path = Path.cwd() / path
        return load_check_from_file(path), f"file:{path}"
    # builtin: имя файла или id проверки
    for f in sorted(BUILTIN_CHECKS_DIR.glob("check_*.py")):
        if name and name not in (f.name, f.stem):
            continue
        return load_check_from_file(f), f"builtin:task/checks/{f.name}"
    raise ValueError(f"встроенная проверка не найдена: {name or '(любая)'}")


def run_checks(mart: SQLiteMart, specs: list[dict[str, Any]],
               user: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Исполняет включённые проверки, возвращает (строки отчёта, ошибки).

    Строки всех проверок объединяются в единую таблицу — требование ТЗ.
    """
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for spec in specs:
        if not spec.get("enabled", True):
            continue
        try:
            check, ref = resolve_check(spec, user)
            params = {**check.get("params", {}), **spec.get("params", {})}
            for r in check["entrypoint"](mart, params) or []:
                r["_check_id"] = check.get("id", "?")
                r["_ref"] = ref
                rows.append(r)
        except Exception:
            errors.append({"check": spec.get("source", {}).get("name", "?"),
                           "error": traceback.format_exc(limit=3)})
    return rows, errors
