"""Private worker protocol: JSON request/result, never pickle or stdout RPC."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

import duckdb

from mart.store import COMPARISON, SEGMENTS, ROW_FILTERS, _where, quoted, records
from checkrunner.validation import validate_rows


class ReadOnlyMart:
    def __init__(self, database, run_id):
        self.con = duckdb.connect(database, read_only=True, config={
            'threads': 1, 'memory_limit': '256MB', 'enable_external_access': False})
        self.run_id = run_id

    def _query(self, projection, filters, tail=''):
        where, values = _where(filters, ROW_FILTERS)
        return records(self.con.execute(f'SELECT {projection} FROM comparison WHERE run_id=?{where} {tail}', [self.run_id, *values]))

    def rows(self, **filters):
        return self._query(','.join(map(quoted, COMPARISON)), filters, 'ORDER BY APP_ID, PATH, ARRAY_KEY')

    def count(self, **filters):
        return self._query('count(*) AS n', filters)[0]['n']

    def app_ids(self, **filters):
        return [r['APP_ID'] for r in self._query('DISTINCT APP_ID', filters, 'ORDER BY APP_ID')]

    def segments(self):
        return records(self.con.execute('SELECT ' + ','.join(map(quoted, SEGMENTS)) + ' FROM segments WHERE run_id=? ORDER BY APP_ID', [self.run_id]))

    def total_apps(self):
        return self.con.execute('SELECT count(*) FROM segments WHERE run_id=?', [self.run_id]).fetchone()[0]


def execute(request):
    # Import-time code runs here too, inside the same timeout/container boundary.
    namespace = {'__name__': 'user_check', '__file__': request['origin']}
    exec(compile(request['code'], request['origin'], 'exec'), namespace)
    check = namespace.get('CHECK')
    if not isinstance(check, dict):
        raise ValueError('Module must export CHECK dict')
    for key in ['id', 'label']:
        if not isinstance(check.get(key), str) or not check[key]:
            raise ValueError(f'CHECK.{key} must be a non-empty string')
    if not callable(check.get('entrypoint')) or not isinstance(check.get('params', {}), dict):
        raise ValueError('CHECK requires callable entrypoint and dict params')
    params = {**check.get('params', {}), **request['params']}
    mart = ReadOnlyMart(request['database'], request['run_id'])
    try:
        rows = validate_rows(check['entrypoint'](mart, params), params.get('app_ids_limit', 20))
        known_ids = {s['APP_ID'] for s in mart.segments()}
        if any(set(r['APP_IDS']) - known_ids for r in rows):
            raise ValueError('APP_IDS contains applications outside this run')
        return dict(ok=True, check_id=check['id'], label=check['label'], params=params, rows=rows)
    finally:
        mart.con.close()


def main():
    request_path, result_path = sys.argv[1:]
    try:
        request = json.loads(Path(request_path).read_text(encoding='utf8'))
        result = execute(request)
    except BaseException as exc:
        result = dict(ok=False, error_type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()[-16000:])
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf8')


if __name__ == '__main__':
    main()
