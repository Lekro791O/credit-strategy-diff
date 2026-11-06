"""Факты counts и drill-down по вертикальной модели (контракт подписки 1).

Факт = атрибут заявки на конкретном срезе, как в pipeline.fact_members:
    (APP_ID, PATH, PREFIX, CLASS, SUFFIX, ARRAY_KEY, KEY,
     DIFF_FLAG, CHANGE_TYPE, SOURCE, VALUE, VALUE_ADJUSTED)
SOURCE — 'BEFORE (MASTER)' или 'AFTER (BETA)'; атрибут существует на срезе,
если он там не отсутствует (NEW нет в BEFORE, LOST нет в AFTER).

Сводная считает COUNT(DISTINCT APP_ID) по выбранным измерениям; клик по
ячейке — тот же набор условий, поэтому список заявок точно соответствует
показателю, включая SOURCE, SUFFIX, бакет и значения среза.

Фильтры разрезов заявки (продукт, партнёр, маршрутизация, модель, порог)
применяются через JOIN со слоем разрезов одинаково и в фактах, и в drill.
Работает поверх SQLiteMart (встроенный) и DuckMart (подписка 2): оба
отдают comparison с одинаковыми колонками.
"""
from __future__ import annotations

from typing import Any

# человекочитаемые подписи типов изменения (в т.ч. алиасы из примеров)
CHANGE_TYPE_RU = {
    "SAME": "без изменений",
    "VALUE_CHANGED": "значение изменено",
    "ATTRIBUTE_NEW": "атрибут появился",
    "ATTRIBUTE_LOST": "атрибут исчез",
    "ADDED": "атрибут появился",
    "REMOVED": "атрибут исчез",
}

def change_type_ru(value: str) -> str:
    return CHANGE_TYPE_RU.get(value, value)


# измерения сводной: ключ -> выражение в модели фактов fm
# (fm — подзапрос-UNION из таблицы сравнения, см. _fm())
DIMS: dict[str, str] = {
    "path": "PATH",
    "prefix": "PREFIX",
    "class": "CLASS",
    "suffix": "SUFFIX",
    "key": "\"KEY\"",
    "array_key": "ARRAY_KEY",
    "change_type": "CHANGE_TYPE",
    "diff_flag": "DIFF_FLAG",
    "source": "SOURCE",
    "value": "VALUE",
    "bucket": "VALUE_ADJUSTED",
    "value_before": "VALUE_BEFORE",
    "value_after": "VALUE_AFTER",
    "adjusted_before": "ADJUSTED_BEFORE",
    "adjusted_after": "ADJUSTED_AFTER",
}

# фильтры ячеек/дрель-дауна на модели fm
_FACT_FILTERS: dict[str, str] = {
    "path": "fm.PATH = ?",
    "path_prefix": "fm.PATH LIKE ? || '%'",
    "key": "fm.\"KEY\" = ?",
    "array_key": "fm.ARRAY_KEY = ?",
    "suffix": "fm.SUFFIX = ?",
    "change_type": "fm.CHANGE_TYPE = ?",
    "diff_flag": "fm.DIFF_FLAG = ?",
    "source": "fm.SOURCE = ?",
    "value": "fm.VALUE = ?",
    "bucket": "fm.VALUE_ADJUSTED = ?",
    "value_before": "fm.VALUE_BEFORE = ?",
    "value_after": "fm.VALUE_AFTER = ?",
    "adjusted_before": "fm.ADJUSTED_BEFORE = ?",
    "adjusted_after": "fm.ADJUSTED_AFTER = ?",
}

# фильтры разрезов заявки (JOIN со слоем разрезов sg)
SEG_FILTERS: dict[str, str] = {
    "product": "(sg.PRODUCTS LIKE '%' || ? || '%' OR sg.TOP_OFFER_BEFORE = ? OR sg.TOP_OFFER_AFTER = ?)",
    "partner": "(sg.PARTNER_TYPE LIKE '%' || ? || '%')",
    "routing": "(sg.ROUTING_METHOD LIKE '%' || ? || '%')",
    "pd_model": "(sg.PD_MODEL_BEFORE = ? OR sg.PD_MODEL_AFTER = ?)",
    "cutoff": "(sg.CUTOFF_BEFORE = ? OR sg.CUTOFF_AFTER = ?)",
}


def _fm(mart) -> str:
    """Подзапрос фактов: вертикальный UNION обеих сторон сравнения."""
    base = f"FROM {mart.fact_table}"
    run = f" WHERE {mart.run_clause}" if mart.run_clause else ""
    cols = ("APP_ID, PATH, PREFIX, CLASS, SUFFIX, ARRAY_KEY, \"KEY\", "
            "DIFF_FLAG, CHANGE_TYPE")
    return (
        f"(SELECT {cols}, 'BEFORE (MASTER)' AS SOURCE, "
        "VALUE_BEFORE AS VALUE, ADJUSTED_BEFORE AS VALUE_ADJUSTED "
        f"{base}{run}{' AND' if mart.run_clause else ' WHERE'} CHANGE_TYPE <> 'ATTRIBUTE_NEW' "
        "UNION ALL "
        f"SELECT {cols}, 'AFTER (BETA)' AS SOURCE, "
        "VALUE_AFTER AS VALUE, ADJUSTED_AFTER AS VALUE_ADJUSTED "
        f"{base}{run}{' AND' if mart.run_clause else ' WHERE'} CHANGE_TYPE <> 'ATTRIBUTE_LOST'"
        ") fm"
    )


def _clauses(mart, filters: dict[str, Any]) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for name, sql in _FACT_FILTERS.items():
        v = filters.get(name)
        if v is not None and v != "":
            clauses.append(sql)
            params.append(v)
    for name, sql in SEG_FILTERS.items():
        v = filters.get(name)
        if v is not None and v != "":
            clauses.append(sql)
            if name == "product":
                params.extend([v, v, v])
            elif name in ("pd_model", "cutoff"):
                params.extend([v, v])
            else:
                params.append(v)
    return clauses, params


def _from(mart, filters: dict[str, Any]) -> tuple[str, list[str], list[Any]]:
    """FROM с JOIN разрезов, если заданы сегментные фильтры."""
    clauses, params = _clauses(mart, filters)
    join = ""
    if any(filters.get(k) not in (None, "") for k in SEG_FILTERS):
        join = " JOIN segments sg ON sg.APP_ID = fm.APP_ID"
        if mart.run_clause:
            join += f" AND {mart.run_clause.replace('run_id', 'sg.run_id')}"
    return _fm(mart) + join, clauses, params


def fact_rows(mart, dims: list[str], filters: dict[str, Any] | None = None,
              limit: int = 5000) -> list[dict[str, Any]]:
    """Агрегаты counts: сколько заявок (COUNT DISTINCT APP_ID) в каждой группе."""
    filters = filters or {}
    bad = [d for d in dims if d not in DIMS]
    if bad:
        raise ValueError(f"неизвестные измерения: {bad}")
    cols = [DIMS[d] for d in dims]
    frm, clauses, params = _from(mart, filters)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    select_dims = ", ".join(f"{c} AS d{i}" for i, c in enumerate(cols))
    group = ", ".join(cols) if cols else "1"
    sql = (
        f"SELECT {select_dims + ',' if cols else ''} COUNT(*) AS cnt_rows, "
        f"COUNT(DISTINCT fm.APP_ID) AS cnt_apps FROM {frm}{where} "
        f"GROUP BY {group} ORDER BY cnt_apps DESC LIMIT ?"
    )
    params.append(limit)
    out = []
    for r in mart.query(sql, params):
        dims_dict = {d: ("" if r[f"d{i}"] is None else str(r[f"d{i}"]))
                     for i, d in enumerate(dims)}
        out.append({"dims": dims_dict, "cnt_rows": r["cnt_rows"],
                    "cnt_apps": r["cnt_apps"]})
    return out


def apps_in_group(mart, dims: dict[str, str], filters: dict[str, Any] | None = None,
                  offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Заявки выбранной группы — ровно те условия, что и у ячейки сводной."""
    merged = {**(filters or {}), **dims}
    frm, clauses, params = _from(mart, merged)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (f"SELECT DISTINCT fm.APP_ID AS APP_ID FROM {frm}{where} "
           "ORDER BY fm.APP_ID LIMIT ? OFFSET ?")
    rows = mart.query(sql, params + [limit, offset])
    total = mart.query(
        f"SELECT COUNT(DISTINCT fm.APP_ID) AS n FROM {frm}{where}", params)[0]["n"]
    return {"app_ids": [r["APP_ID"] for r in rows], "total": total}
