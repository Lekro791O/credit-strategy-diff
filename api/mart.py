"""MartAPI — доступ к витрине сравнения для пользовательских проверок.

Реализует интерфейс из task/checks/README.md:
    mart.rows(**filters)      -> Iterable[dict]
    mart.count(**filters)     -> int
    mart.app_ids(**filters)   -> list[str]   (уникальные, отсортированные)
    mart.segments()           -> Iterable[dict]
    mart.total_apps()         -> int

Хранение — SQLite (стандартная библиотека): 290 тыс. строк — копейки,
индексы покрывают все фильтры контракта. Если подписка 2 отдаст свой
провайдер (mart/ пакет), RunManager предпочтёт его, этот адаптер —
рабочий дефолт и эталон поведения.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

MART_COLUMNS = [
    "APP_ID", "PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "PATH",
    "VALUE_BEFORE", "VALUE_AFTER", "ADJUSTED_BEFORE", "ADJUSTED_AFTER",
    "DIFF_FLAG", "CHANGE_TYPE",
]

SEGMENT_COLUMNS = [
    "APP_ID", "DECISION_BEFORE", "DECISION_AFTER", "DECISION_CHANGED",
    "PRODUCTS", "TOP_OFFER_BEFORE", "TOP_OFFER_AFTER", "PARTNER_TYPE",
    "ROUTING_METHOD", "PD_MODEL_BEFORE", "PD_MODEL_AFTER",
    "CUTOFF_BEFORE", "CUTOFF_AFTER", "REJECT_REASON_BEFORE",
    "REJECT_REASON_AFTER", "SCORE_OKB_BEFORE", "SCORE_OKB_AFTER",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mart (
    APP_ID TEXT NOT NULL,
    PREFIX TEXT, CLASS TEXT, SUFFIX TEXT, ARRAY_KEY TEXT, KEY TEXT,
    PATH TEXT NOT NULL,
    VALUE_BEFORE TEXT, VALUE_AFTER TEXT,
    ADJUSTED_BEFORE TEXT, ADJUSTED_AFTER TEXT,
    DIFF_FLAG INTEGER NOT NULL DEFAULT 0,
    CHANGE_TYPE TEXT NOT NULL DEFAULT 'SAME'
);
CREATE INDEX IF NOT EXISTS idx_mart_path ON mart(PATH);
CREATE INDEX IF NOT EXISTS idx_mart_app ON mart(APP_ID);
CREATE INDEX IF NOT EXISTS idx_mart_ct ON mart(CHANGE_TYPE);
CREATE INDEX IF NOT EXISTS idx_mart_diff ON mart(DIFF_FLAG);
CREATE TABLE IF NOT EXISTS segments (
    APP_ID TEXT PRIMARY KEY,
    DECISION_BEFORE TEXT, DECISION_AFTER TEXT, DECISION_CHANGED INTEGER,
    PRODUCTS TEXT, TOP_OFFER_BEFORE TEXT, TOP_OFFER_AFTER TEXT,
    PARTNER_TYPE TEXT, ROUTING_METHOD TEXT,
    PD_MODEL_BEFORE TEXT, PD_MODEL_AFTER TEXT,
    CUTOFF_BEFORE TEXT, CUTOFF_AFTER TEXT,
    REJECT_REASON_BEFORE TEXT, REJECT_REASON_AFTER TEXT,
    SCORE_OKB_BEFORE TEXT, SCORE_OKB_AFTER TEXT
);
"""

# фильтры контракта -> SQL
_FILTERS = {
    "path": "PATH = ?", "path_prefix": "PATH LIKE ? || '%'",
    "key": "`KEY` = ?", "array_key": "ARRAY_KEY = ?",
    "diff_flag": "DIFF_FLAG = ?", "value_before": "VALUE_BEFORE = ?",
    "value_after": "VALUE_AFTER = ?",
}


class SQLiteMart:
    """Витрина сравнения + слой разрезов поверх SQLite."""

    fact_table = "mart"   # имя таблицы для фактов (facts.py)
    run_clause = ""       # условие на run_id для много-прогонных сторов

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self.conn = sqlite3.connect(
            str(self.path) if self.path else ":memory:", check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # ---------- загрузка ----------

    def load(self, rows: Iterable[dict], segments: Iterable[dict]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO mart VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [[r.get(c, "") for c in MART_COLUMNS] for r in rows],
            )
            self.conn.executemany(
                f"INSERT OR REPLACE INTO segments VALUES ({','.join('?' * len(SEGMENT_COLUMNS))})",
                [[s.get(c, "") for c in SEGMENT_COLUMNS] for s in segments],
            )

    # ---------- MartAPI (контракт) ----------

    def _where(self, filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses, params = [], []
        for name, sql in _FILTERS.items():
            if name in filters and filters[name] is not None:
                clauses.append(sql)
                params.append(filters[name])
        ct = filters.get("change_type")
        if ct is not None:
            if isinstance(ct, (list, tuple, set)):
                clauses.append(f"CHANGE_TYPE IN ({','.join('?' * len(ct))})")
                params.extend(ct)
            else:
                clauses.append("CHANGE_TYPE = ?")
                params.append(ct)
        app_ids = filters.get("app_ids")
        if app_ids is not None:
            ids = list(app_ids)
            clauses.append(f"APP_ID IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def rows(self, **filters: Any) -> Iterable[dict]:
        where, params = self._where(filters)
        cur = self.conn.execute(f"SELECT * FROM mart{where}", params)
        return [dict(r) for r in cur.fetchall()]

    def count(self, **filters: Any) -> int:
        where, params = self._where(filters)
        return self.conn.execute(f"SELECT COUNT(*) FROM mart{where}", params).fetchone()[0]

    def app_ids(self, **filters: Any) -> list[str]:
        where, params = self._where(filters)
        cur = self.conn.execute(
            f"SELECT DISTINCT APP_ID FROM mart{where} ORDER BY APP_ID", params
        )
        return [r[0] for r in cur.fetchall()]

    def segments(self) -> Iterable[dict]:
        cur = self.conn.execute("SELECT * FROM segments ORDER BY APP_ID")
        return [dict(r) for r in cur.fetchall()]

    def total_apps(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]

    # ---------- сервисные запросы для UI ----------

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Унифицированный запрос для facts.py: список dict."""
        cur = self.conn.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()

    def reopen(self) -> None:
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def decision_stats(self) -> dict[str, Any]:
        segs = list(self.segments())
        before: dict[str, int] = {}
        after: dict[str, int] = {}
        changed = 0
        for s in segs:
            b, a = s.get("DECISION_BEFORE") or "?", s.get("DECISION_AFTER") or "?"
            before[b] = before.get(b, 0) + 1
            after[a] = after.get(a, 0) + 1
            if s.get("DECISION_CHANGED"):
                changed += 1
        with_diffs = self.conn.execute(
            "SELECT COUNT(DISTINCT APP_ID) FROM mart WHERE DIFF_FLAG = 1"
        ).fetchone()[0]
        return {
            "total_apps": len(segs),
            "apps_with_diffs": with_diffs,
            "apps_decision_changed": changed,
            "decision_before": before,
            "decision_after": after,
        }

    def app_attribute_rows(self, app_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM mart WHERE APP_ID = ? ORDER BY PATH, ARRAY_KEY", (app_id,)
        )
        return [dict(r) for r in cur.fetchall()]


class DuckMart:
    """Витрина поверх MartStore подписки 2 (DuckDB + изоляция проверок).

    Тот же публичный интерфейс, что у SQLiteMart: rows/count/app_ids/
    segments/total_apps (делегирует MartAPI), query/decision_stats/
    app_attribute_rows — прямые SQL по view comparison. Файл — один
    прогон, run_id фиксирован: один прогон = один стор.
    """
    RUN_ID = "run"
    fact_table = "comparison"
    run_clause = f"run_id = '{RUN_ID}'"
    backend_name = "DuckDB (mart/)"

    def __init__(self, path):
        from pathlib import Path as _P
        self.path = _P(path)
        self._store = None

    def _ensure_store(self):
        if self._store is None:
            from mart.store import MartStore  # на Windows падает (fcntl) — фолбэк снаружи
            self._store = MartStore(self.path)
        return self._store

    def probe(self) -> None:
        """Проверка доступности стека подписки 2 на этой машине."""
        self._ensure_store()

    # ---------- загрузка ----------

    def load(self, rows: Iterable[dict], segments: Iterable[dict]) -> None:
        self._ensure_store().save_run(self.RUN_ID, rows, segments)

    # ---------- MartAPI (контракт, делегируем) ----------

    @property
    def api(self):
        return self._ensure_store().for_run(self.RUN_ID)

    def rows(self, **filters) -> Iterable[dict]:
        return self.api.rows(**filters)

    def count(self, **filters) -> int:
        return self.api.count(**filters)

    def app_ids(self, **filters) -> list[str]:
        return self.api.app_ids(**filters)

    def segments(self) -> Iterable[dict]:
        return self.api.segments()

    def total_apps(self) -> int:
        return self.api.total_apps()

    # ---------- сервисные запросы для UI ----------

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._ensure_store().connection() as con:
            cur = con.execute(sql, list(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def decision_stats(self) -> dict:
        segs = self.query(
            "SELECT DECISION_BEFORE, DECISION_AFTER, DECISION_CHANGED "
            f"FROM segments WHERE {self.run_clause}")
        before: dict[str, int] = {}
        after: dict[str, int] = {}
        changed = 0
        for s in segs:
            b, a = s.get("DECISION_BEFORE") or "?", s.get("DECISION_AFTER") or "?"
            before[b] = before.get(b, 0) + 1
            after[a] = after.get(a, 0) + 1
            if s.get("DECISION_CHANGED"):
                changed += 1
        with_diffs = self.query(
            "SELECT COUNT(DISTINCT APP_ID) AS n FROM comparison "
            f"WHERE {self.run_clause} AND DIFF_FLAG = 1")[0]["n"]
        return {"total_apps": len(segs), "apps_with_diffs": with_diffs,
                "apps_decision_changed": changed,
                "decision_before": before, "decision_after": after}

    def app_attribute_rows(self, app_id: str) -> list[dict]:
        return self.query(
            "SELECT APP_ID, PREFIX, CLASS, SUFFIX, ARRAY_KEY, KEY, PATH, "
            "VALUE_BEFORE, VALUE_AFTER, ADJUSTED_BEFORE, ADJUSTED_AFTER, "
            f"DIFF_FLAG, CHANGE_TYPE FROM comparison "
            f"WHERE {self.run_clause} AND APP_ID = ? "
            "ORDER BY PATH, ARRAY_KEY", (app_id,))

    def close(self) -> None:
        self._store = None

    def reopen(self) -> None:
        self._store = None
