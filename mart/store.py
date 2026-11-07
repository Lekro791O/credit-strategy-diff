"""Vertical storage; horizontal comparison is a compatibility view only.

Connections are short lived and protected by a process/thread lock. Never pass
this service to untrusted Python: checkrunner exports a run-only snapshot.
"""
from __future__ import annotations

from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # Windows: advisory-locks нет, сервер однопроцессный — безопасный no-op
    class _FcntlStub:
        LOCK_EX = LOCK_UN = 0

        @staticmethod
        def flock(_file, _operation):
            return None

    fcntl = _FcntlStub()
import json
from pathlib import Path
import tempfile
import threading
import uuid

import duckdb

STRUCTURE = ["PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "PATH"]
COMPARISON = ["APP_ID", *STRUCTURE, "VALUE_BEFORE", "VALUE_AFTER",
              "ADJUSTED_BEFORE", "ADJUSTED_AFTER", "DIFF_FLAG", "CHANGE_TYPE"]
SEGMENTS = ["APP_ID", "DECISION_BEFORE", "DECISION_AFTER", "DECISION_CHANGED",
            "PRODUCTS", "TOP_OFFER_BEFORE", "TOP_OFFER_AFTER", "PARTNER_TYPE",
            "ROUTING_METHOD", "PD_MODEL_BEFORE", "PD_MODEL_AFTER", "CUTOFF_BEFORE",
            "CUTOFF_AFTER", "REJECT_REASON_BEFORE", "REJECT_REASON_AFTER",
            "SCORE_OKB_BEFORE", "SCORE_OKB_AFTER"]
FACT_DIMS = ["PREFIX", "CLASS", "SUFFIX", "ARRAY_KEY", "KEY", "VALUE_ADJUSTED",
             "DIFF_FLAG", "SOURCE"]
SOURCES = {"BEFORE": "BEFORE", "AFTER": "AFTER",
           "BEFORE (MASTER)": "BEFORE", "AFTER (BETA)": "AFTER"}
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def records(cursor):
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _where(filters, allowed, alias=""):
    clauses, args = [], []
    for field, value in filters.items():
        if field not in allowed:
            raise ValueError(f"Unknown filter: {field}")
        col = alias + quoted(allowed[field])
        if field == "path_prefix":
            if not isinstance(value, str):
                raise ValueError("path_prefix must be a string")
            clauses.append(f"starts_with({col}, ?)")
            args.append(value)
        elif isinstance(value, (list, tuple, set)):
            vals = list(value)
            clauses.append(f"{col} IN ({','.join('?' for _ in vals)})" if vals else "FALSE")
            args.extend(vals)
        elif value is None:
            clauses.append(f"{col} IS NULL")
        else:
            clauses.append(f"{col} = ?")
            args.append(value)
    return (" AND " + " AND ".join(clauses) if clauses else ""), args


ROW_FILTERS = {k.lower(): k for k in ["PATH", "KEY", "ARRAY_KEY", "DIFF_FLAG",
               "CHANGE_TYPE", "VALUE_BEFORE", "VALUE_AFTER"]}
ROW_FILTERS.update(path_prefix="PATH", app_ids="APP_ID")


class MartStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.path), threading.RLock())
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS runs (run_id VARCHAR PRIMARY KEY, revision VARCHAR, metadata JSON)")
            con.execute('''CREATE TABLE IF NOT EXISTS attributes (
                run_id VARCHAR, APP_ID VARCHAR, PREFIX VARCHAR, CLASS VARCHAR,
                SUFFIX VARCHAR, ARRAY_KEY VARCHAR, KEY VARCHAR, PATH VARCHAR,
                DIFF_FLAG INTEGER, CHANGE_TYPE VARCHAR,
                PRIMARY KEY (run_id, APP_ID, PATH, ARRAY_KEY))''')
            con.execute('''CREATE TABLE IF NOT EXISTS datamart (
                run_id VARCHAR, APP_ID VARCHAR, PATH VARCHAR, ARRAY_KEY VARCHAR,
                SOURCE VARCHAR, VALUE VARCHAR, ADJUSTED_VALUE VARCHAR,
                PRIMARY KEY (run_id, APP_ID, PATH, ARRAY_KEY, SOURCE))''')
            seg_sql = ', '.join(quoted(k) + (' INTEGER' if k == 'DECISION_CHANGED' else ' VARCHAR') for k in SEGMENTS)
            con.execute(f"CREATE TABLE IF NOT EXISTS segments (run_id VARCHAR, {seg_sql}, PRIMARY KEY(run_id, APP_ID))")
            con.execute("CREATE TABLE IF NOT EXISTS reports (run_id VARCHAR PRIMARY KEY, revision VARCHAR, payload JSON)")
            struct_sql = ', '.join('a.' + quoted(k) for k in ["APP_ID", *STRUCTURE])
            con.execute(f'''CREATE OR REPLACE VIEW comparison AS
                SELECT a.run_id, {struct_sql},
                coalesce(b.VALUE, '') AS VALUE_BEFORE, coalesce(z.VALUE, '') AS VALUE_AFTER,
                coalesce(b.ADJUSTED_VALUE, '') AS ADJUSTED_BEFORE,
                coalesce(z.ADJUSTED_VALUE, '') AS ADJUSTED_AFTER, a.DIFF_FLAG, a.CHANGE_TYPE
                FROM attributes a
                LEFT JOIN datamart b ON a.run_id=b.run_id AND a.APP_ID=b.APP_ID
                    AND a.PATH=b.PATH AND a.ARRAY_KEY=b.ARRAY_KEY AND b.SOURCE='BEFORE'
                LEFT JOIN datamart z ON a.run_id=z.run_id AND a.APP_ID=z.APP_ID
                    AND a.PATH=z.PATH AND a.ARRAY_KEY=z.ARRAY_KEY AND z.SOURCE='AFTER' ''')
            con.execute('''CREATE OR REPLACE VIEW fact_members AS
                SELECT a.run_id, a.APP_ID, a.PREFIX, a.CLASS, a.SUFFIX, a.ARRAY_KEY,
                    a.KEY, a.PATH, d.VALUE, d.ADJUSTED_VALUE AS VALUE_ADJUSTED, a.DIFF_FLAG,
                    CASE d.SOURCE WHEN 'BEFORE' THEN 'BEFORE (MASTER)' ELSE 'AFTER (BETA)' END AS SOURCE,
                    a.CHANGE_TYPE
                FROM attributes a JOIN datamart d USING (run_id, APP_ID, PATH, ARRAY_KEY)''')
            dim_sql = ', '.join(map(quoted, FACT_DIMS))
            con.execute(f'''CREATE TABLE IF NOT EXISTS counts AS
                SELECT run_id, {dim_sql}, count(DISTINCT APP_ID)::BIGINT AS CNT
                FROM fact_members GROUP BY run_id, {dim_sql}''')

    @contextmanager
    def connection(self):
        with self._lock, open(str(self.path) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                con = duckdb.connect(str(self.path), config={"threads": 2})
                try:
                    yield con
                finally:
                    con.close()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _insert(con, table, rows):
        if not rows:
            return
        # Bulk JSON import avoids one SQL statement per attribute. Explicit target
        # column types prevent numeric-looking IDs and literal "NULL" being coerced.
        columns = con.execute(f"DESCRIBE {table}").fetchall()
        types = {c[0]: c[1] for c in columns}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf8') as file:
            json.dump(rows, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            con.execute(f"INSERT INTO {table} SELECT * FROM read_json(?, columns = ?)", [file.name, types])

    def save_run(self, run_id, comparison_rows, segments, *, normalized_rows=None, metadata=None):
        """Atomically replace a run. Inputs follow task/docs unchanged.

        Optional normalized_rows must include PATH (identity, never guessed from
        structural columns) and exactly reproduce the comparison values/presence.
        If omitted, vertical rows are losslessly reconstructed from CHANGE_TYPE.
        """
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be a non-empty string")
        attrs, vertical, segs, seen = [], [], [], set()
        for raw in comparison_rows:
            row = dict(raw)
            missing = set(COMPARISON) - row.keys()
            if missing:
                raise ValueError(f"Comparison missing fields: {sorted(missing)}")
            if any(not isinstance(row[k], str) for k in COMPARISON if k != 'DIFF_FLAG'):
                raise ValueError("Comparison text fields must be strings; missing values use empty string")
            if not row['APP_ID'] or not row['PATH']:
                raise ValueError("APP_ID and PATH cannot be empty")
            key = (row['APP_ID'], row['PATH'], row['ARRAY_KEY'])
            if key in seen:
                raise ValueError(f"Duplicate comparison key: {key}")
            seen.add(key)
            change = row['CHANGE_TYPE']
            if change not in ('SAME', 'VALUE_CHANGED', 'ATTRIBUTE_NEW', 'ATTRIBUTE_LOST'):
                raise ValueError(f"Invalid CHANGE_TYPE: {change}")
            if type(row['DIFF_FLAG']) is not int or row['DIFF_FLAG'] != int(change != 'SAME'):
                raise ValueError("DIFF_FLAG must be integer 0/1 consistent with CHANGE_TYPE")
            if change in ('SAME', 'VALUE_CHANGED') and (row['VALUE_BEFORE'] == row['VALUE_AFTER']) != (change == 'SAME'):
                raise ValueError("CHANGE_TYPE must compare raw VALUE, not buckets")
            attrs.append(dict(run_id=run_id, **{k: row[k] for k in ['APP_ID', *STRUCTURE, 'DIFF_FLAG', 'CHANGE_TYPE']}))
            for source, absent in [('BEFORE', 'ATTRIBUTE_NEW'), ('AFTER', 'ATTRIBUTE_LOST')]:
                if change == absent:
                    if row['VALUE_' + source] or row['ADJUSTED_' + source]:
                        raise ValueError("Absent attribute must have empty value and bucket")
                    continue
                vertical.append(dict(run_id=run_id, APP_ID=row['APP_ID'], PATH=row['PATH'],
                                     ARRAY_KEY=row['ARRAY_KEY'], SOURCE=source,
                                     VALUE=row['VALUE_' + source], ADJUSTED_VALUE=row['ADJUSTED_' + source]))
        if normalized_rows is not None:
            supplied = {}
            for row in normalized_rows:
                r = {k: row[k] for k in ['APP_ID', 'PATH', 'ARRAY_KEY', 'SOURCE', 'VALUE', 'ADJUSTED_VALUE']}
                r['SOURCE'] = SOURCES[r['SOURCE']]
                identity = tuple(r[k] for k in ['APP_ID', 'PATH', 'ARRAY_KEY', 'SOURCE'])
                if identity in supplied:
                    raise ValueError(f"Duplicate normalized key: {identity}")
                supplied[identity] = dict(run_id=run_id, **r)
            expected = {tuple(r[k] for k in ['APP_ID', 'PATH', 'ARRAY_KEY', 'SOURCE']): r for r in vertical}
            if supplied != expected:
                raise ValueError("Normalized rows disagree with comparison (presence, VALUE or ADJUSTED_VALUE)")
        segment_ids = set()
        for raw in segments:
            missing = set(SEGMENTS) - raw.keys()
            if missing:
                raise ValueError(f"Segments missing fields: {sorted(missing)}")
            row = {k: raw[k] for k in SEGMENTS}
            if not isinstance(row['APP_ID'], str) or not row['APP_ID'] or row['APP_ID'] in segment_ids:
                raise ValueError("Segment APP_ID must be unique non-empty string")
            if type(row['DECISION_CHANGED']) is not int or row['DECISION_CHANGED'] not in (0, 1):
                raise ValueError("DECISION_CHANGED must be integer 0/1, not string")
            if row['DECISION_CHANGED'] != int(row['DECISION_BEFORE'] != row['DECISION_AFTER']):
                raise ValueError("DECISION_CHANGED disagrees with decisions")
            for k in SEGMENTS:
                if k != 'DECISION_CHANGED' and row[k] is not None and not isinstance(row[k], str):
                    row[k] = str(row[k]) if k.startswith('SCORE_OKB_') else row[k]
                    if not isinstance(row[k], str):
                        raise ValueError(f"{k} must be a string or None")
            segment_ids.add(row['APP_ID'])
            segs.append(dict(run_id=run_id, **row))
        if {k[0] for k in seen} - segment_ids:
            raise ValueError("Every comparison APP_ID needs a segment row")
        revision = uuid.uuid4().hex
        with self.connection() as con:
            con.execute("BEGIN")
            try:
                for table in ['reports', 'counts', 'datamart', 'attributes', 'segments', 'runs']:
                    con.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
                con.execute("INSERT INTO runs VALUES (?, ?, ?)", [run_id, revision, json.dumps(metadata or {}, allow_nan=False)])
                for table, data in [('attributes', attrs), ('datamart', vertical), ('segments', segs)]:
                    self._insert(con, table, data)
                dims = ', '.join(map(quoted, FACT_DIMS))
                con.execute(f'''INSERT INTO counts SELECT run_id, {dims}, count(DISTINCT APP_ID)
                    FROM fact_members WHERE run_id=? GROUP BY run_id, {dims}''', [run_id])
                # Build secondary ART indexes after the bulk load. A leading
                # run_id in a composite index does not provide the same selective
                # APP_ID scan as a single-column ART index in DuckDB's drill query.
                # Primary keys still enforce the unchanged identity contracts.
                for statement in (
                    'CREATE INDEX IF NOT EXISTS attr_path ON attributes(run_id, PATH)',
                    'CREATE INDEX IF NOT EXISTS attr_change ON attributes(run_id, CHANGE_TYPE)',
                    'CREATE INDEX IF NOT EXISTS attr_app_lookup ON attributes(APP_ID)',
                ):
                    con.execute(statement)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        return revision

    def for_run(self, run_id):
        self.run_info(run_id)
        return MartAPI(self, run_id)

    def run_info(self, run_id):
        with self.connection() as con:
            rows = records(con.execute("SELECT * FROM runs WHERE run_id=?", [run_id]))
        if not rows:
            raise KeyError(f"Unknown run: {run_id}")
        rows[0]['metadata'] = json.loads(rows[0]['metadata'])
        return rows[0]

    def save_check_report(self, run_id, revision, report):
        with self.connection() as con:
            current = con.execute("SELECT revision FROM runs WHERE run_id=?", [run_id]).fetchone()
            if not current or current[0] != revision:
                raise ValueError("Run changed during checks; report was not saved")
            con.execute("INSERT OR REPLACE INTO reports VALUES (?, ?, ?)",
                        [run_id, revision, json.dumps(report, ensure_ascii=False, allow_nan=False)])

    def get_check_report(self, run_id):
        with self.connection() as con:
            row = con.execute("SELECT payload FROM reports WHERE run_id=?", [run_id]).fetchone()
        return json.loads(row[0]) if row else None

    def get_check_results(self, run_id, *, check_id=None, result=None, app_id=None):
        """Filter business rows without adding columns to the organizers' schema."""
        report = self.get_check_report(run_id)
        if report is None:
            return []
        if result is not None and result not in ('PASS', 'FAIL'):
            raise ValueError('result must be PASS or FAIL')
        rows = report['rows']
        if check_id is not None:
            rows = [row for e in report['executions'] if e.get('check_id') == check_id
                    for row in report['rows'][e['row_start']:e['row_start'] + e['row_count']]]
        return [row for row in rows if (result is None or row['RESULT'] == result)
                and (app_id is None or app_id in row['APP_IDS'])]

    def snapshot(self, run_id, destination):
        """Export only the authorized run, atomically with respect to writers."""
        if Path(destination).resolve() == self.path:
            raise ValueError('Snapshot destination must differ from source database')
        target = MartStore(destination)
        with self.connection() as con:
            meta = con.execute("SELECT revision, metadata FROM runs WHERE run_id=?", [run_id]).fetchone()
            if not meta:
                raise KeyError(run_id)
            rows = records(con.execute('SELECT ' + ','.join(map(quoted, COMPARISON)) + ' FROM comparison WHERE run_id=?', [run_id]))
            segs = records(con.execute('SELECT ' + ','.join(map(quoted, SEGMENTS)) + ' FROM segments WHERE run_id=?', [run_id]))
            target.save_run(run_id, rows, segs, metadata=json.loads(meta[1]))
        return meta[0]


class MartAPI:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id

    def _select(self, projection, filters, tail=""):
        where, args = _where(filters, ROW_FILTERS)
        with self.store.connection() as con:
            return records(con.execute(f"SELECT {projection} FROM comparison WHERE run_id=?{where} {tail}", [self.run_id, *args]))

    def rows(self, **filters):
        return self._select(','.join(map(quoted, COMPARISON)), filters, 'ORDER BY APP_ID, PATH, ARRAY_KEY')

    def count(self, **filters):
        return self._select('count(*) AS n', filters)[0]['n']

    def app_ids(self, **filters):
        return [r['APP_ID'] for r in self._select('DISTINCT APP_ID', filters, 'ORDER BY APP_ID')]

    def segments(self):
        with self.store.connection() as con:
            return records(con.execute('SELECT ' + ','.join(map(quoted, SEGMENTS)) + ' FROM segments WHERE run_id=? ORDER BY APP_ID', [self.run_id]))

    def total_apps(self):
        with self.store.connection() as con:
            return con.execute('SELECT count(*) FROM segments WHERE run_id=?', [self.run_id]).fetchone()[0]

    def summary(self):
        with self.store.connection() as con:
            changed = con.execute('SELECT count(DISTINCT APP_ID) FROM attributes WHERE run_id=? AND DIFF_FLAG=1', [self.run_id]).fetchone()[0]
            total, decisions = con.execute('SELECT count(*), coalesce(sum(DECISION_CHANGED), 0) FROM segments WHERE run_id=?', [self.run_id]).fetchone()
        return dict(total_apps=total, changed_apps=changed, decision_changed_apps=decisions)

    def page(self, *, limit=100, offset=0, **filters):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 10000 or offset < 0:
            raise ValueError("limit must be 1..10000; offset must be non-negative")
        return self._select(','.join(map(quoted, COMPARISON)), filters,
                            f'ORDER BY APP_ID, PATH, ARRAY_KEY LIMIT {limit} OFFSET {offset}')

    def application(self, app_id, *, path=None, array_key=None):
        filters = dict(app_ids=[app_id])
        if path is not None:
            filters['path'] = path
        if array_key is not None:
            filters['array_key'] = array_key
        return self.rows(**filters)

    def _fact_query(self, dimensions, filters, segment_filters, members=False):
        allowed = set(FACT_DIMS + ['PATH', 'CHANGE_TYPE'] + SEGMENTS[1:])
        if not dimensions or len(set(dimensions)) != len(dimensions) or set(dimensions) - allowed:
            raise ValueError("Unknown, empty or duplicate grouping dimensions")
        if 'product' in segment_filters:
            segment_filters = dict(segment_filters)
            product = segment_filters.pop('product')
        else:
            product = None
        where, args = _where(filters, {k: k for k in [*FACT_DIMS, 'PATH', 'CHANGE_TYPE', 'APP_ID']}, 'f.')
        sw, sa = _where(segment_filters, {k: k for k in SEGMENTS[1:]}, 's.')
        if product is not None:
            sw += " AND list_contains(string_split(s.PRODUCTS, ';'), ?)"
            sa.append(product)
        columns = ', '.join(('s.' if d in SEGMENTS[1:] else 'f.') + quoted(d) for d in dimensions)
        with self.store.connection() as con:
            if members:
                return records(con.execute(f'''SELECT DISTINCT f.APP_ID, f.PATH, f.ARRAY_KEY
                    FROM fact_members f JOIN segments s USING(run_id, APP_ID)
                    WHERE f.run_id=?{where}{sw} ORDER BY f.APP_ID, f.PATH, f.ARRAY_KEY''', [self.run_id, *args, *sa]))
            # SUM of precomputed counts is unsafe when the layout drops dimensions:
            # the same application may occur several times. Recount unique IDs.
            if list(dimensions) == FACT_DIMS and not filters and not segment_filters and product is None:
                return records(con.execute('SELECT ' + ','.join(map(quoted, FACT_DIMS)) + ', CNT FROM counts WHERE run_id=? ORDER BY ALL', [self.run_id]))
            return records(con.execute(f'''SELECT {columns}, count(DISTINCT f.APP_ID) AS CNT
                FROM fact_members f JOIN segments s USING(run_id, APP_ID)
                WHERE f.run_id=?{where}{sw} GROUP BY {columns} ORDER BY ALL''', [self.run_id, *args, *sa]))

    def aggregates(self, dimensions=None, *, filters=None, segment_filters=None):
        return self._fact_query(FACT_DIMS if dimensions is None else dimensions, filters or {}, segment_filters or {})

    def drill_down(self, *, filters=None, segment_filters=None):
        """Use a fact row's dimensions (excluding CNT) as filters."""
        return self._fact_query(FACT_DIMS, filters or {}, segment_filters or {}, members=True)
