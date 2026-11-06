"""Факты counts для сводной таблицы.

Сводный отчёт живёт поверх агрегатов, а не сырых строк: одна группа =
сочетание измерений (путь атрибута, тип изменения, бакет и т.п.).
Сервер считает GROUP BY по выбранным измерениям; клик по ячейке —
это тот же запрос плюс фильтр по группе, возвращающий APP_ID.
"""
from __future__ import annotations

from typing import Any

from .mart import SQLiteMart

# измерения, доступные в сводной (значение -> колонка витрины)
DIMS: dict[str, str] = {
    "prefix": "PREFIX",
    "class": "CLASS",
    "key": "`KEY`",
    "path": "PATH",
    "array_key": "ARRAY_KEY",
    "change_type": "CHANGE_TYPE",
    "value_before": "VALUE_BEFORE",
    "value_after": "VALUE_AFTER",
    "adjusted_before": "ADJUSTED_BEFORE",
    "adjusted_after": "ADJUSTED_AFTER",
    "diff_flag": "DIFF_FLAG",
}

# фильтры, применимые к фактам
_FACT_FILTERS: dict[str, str] = {
    "path_prefix": "PATH LIKE ? || '%'",
    "path": "PATH = ?",
    "key": "`KEY` = ?",
    "change_type": "CHANGE_TYPE = ?",
    "diff_flag": "DIFF_FLAG = ?",
    "value_after": "VALUE_AFTER = ?",
    "array_key": "ARRAY_KEY = ?",
}


def fact_rows(mart: SQLiteMart, dims: list[str],
              filters: dict[str, Any] | None = None,
              limit: int = 5000) -> list[dict[str, Any]]:
    filters = filters or {}
    bad = [d for d in dims if d not in DIMS]
    if bad:
        raise ValueError(f"неизвестные измерения: {bad}")
    cols = [DIMS[d] for d in dims]
    clauses, params = [], []
    for name, sql in _FACT_FILTERS.items():
        v = filters.get(name)
        if v is not None and v != "":
            clauses.append(sql)
            params.append(v)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # каждое измерение — отдельная колонка с псевдонимом d<i>
    select_dims = ", ".join(f"{c} AS d{i}" for i, c in enumerate(cols))
    group = ", ".join(cols) if cols else "1"
    sql = (
        f"SELECT {select_dims + ',' if cols else ''} COUNT(*) AS cnt_rows, "
        f"COUNT(DISTINCT APP_ID) AS cnt_apps FROM mart{where} "
        f"GROUP BY {group} ORDER BY cnt_apps DESC LIMIT ?"
    )
    params.append(limit)
    out = []
    for r in mart.conn.execute(sql, params).fetchall():
        dims_dict = {d: ("" if r[f"d{i}"] is None else str(r[f"d{i}"]))
                     for i, d in enumerate(dims)}
        out.append({"dims": dims_dict, "cnt_rows": r["cnt_rows"],
                    "cnt_apps": r["cnt_apps"]})
    return out


def apps_in_group(mart: SQLiteMart, dims: dict[str, str],
                  filters: dict[str, Any] | None = None,
                  offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Заявки, попавшие в выбранную группу фактов (drill-through)."""
    filters = {**(filters or {}), **dims}
    clauses, params = [], []
    for name, sql in _FACT_FILTERS.items():
        v = filters.get(name)
        if v is not None and v != "":
            clauses.append(sql)
            params.append(v)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT DISTINCT APP_ID FROM mart{where} ORDER BY APP_ID LIMIT ? OFFSET ?"
    params += [limit, offset]
    ids = [r[0] for r in mart.conn.execute(sql, params).fetchall()]
    total = mart.conn.execute(
        f"SELECT COUNT(DISTINCT APP_ID) FROM mart{where}", params[:-2]
    ).fetchone()[0]
    return {"app_ids": ids, "total": total}
