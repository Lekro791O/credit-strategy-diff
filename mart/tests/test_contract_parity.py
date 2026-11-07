"""The same published filters must work in server MartAPI and snapshot worker."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mart import MartStore
from mart.demo import educational_rows, segment
from checkrunner.worker import ReadOnlyMart


@pytest.fixture
def apis(tmp_path):
    store = MartStore(tmp_path / 'source.duckdb')
    rows = []
    for app in ['0002', '0001']:
        rows.extend(dict(row, APP_ID=app) for row in educational_rows())
    store.save_run('run', rows, [segment('0001'), segment('0002')])
    store.save_run('other', [dict(r, APP_ID='secret') for r in educational_rows()], [segment('secret')])
    snapshot = tmp_path / 'snapshot.duckdb'
    store.snapshot('run', snapshot)
    worker = ReadOnlyMart(str(snapshot), 'run')
    try:
        yield store.for_run('run'), worker, rows
    finally:
        worker.con.close()


@pytest.mark.parametrize('filters', [
    {}, {'path': 'company.decisionResult.decision'},
    {'path': ['company.decisionResult.decision', 'company.limitResult.Limit.limit']},
    {'path': []}, {'path_prefix': 'company.scoreCardResult.'},
    {'path_prefix': ''}, {'path_prefix': '%'}, {'path_prefix': '_'},
    {'key': 'value'}, {'array_key': 'TAKEUP_RATE_IP'},
    {'change_type': 'VALUE_CHANGED'}, {'change_type': ['ATTRIBUTE_NEW', 'ATTRIBUTE_LOST']},
    {'change_type': []}, {'diff_flag': 0}, {'diff_flag': 1},
    {'value_before': 'APPROVE'}, {'value_after': 'VERIFICATION'},
    {'value_before': ''}, {'value_after': "' OR 1=1 --"},
    {'app_ids': ['0002', '0001', '0001']}, {'app_ids': []},
    {'path_prefix': 'company.', 'key': 'value', 'array_key': 'TAKEUP_RATE_IP',
     'change_type': ['SAME', 'VALUE_CHANGED'], 'diff_flag': 1,
     'value_before': '0.42', 'value_after': '0.58', 'app_ids': ['0001']},
])
def test_published_filters_match_independent_oracle(apis, filters):
    def matches(row):
        for key, value in filters.items():
            column = 'APP_ID' if key == 'app_ids' else 'PATH' if key == 'path_prefix' else key.upper()
            if key == 'path_prefix':
                if not row[column].startswith(value): return False
            elif isinstance(value, list):
                if row[column] not in value: return False
            elif row[column] != value:
                return False
        return True
    parent, worker, rows = apis
    expected = sorted((r for r in rows if matches(r)), key=lambda r: (r['APP_ID'], r['PATH'], r['ARRAY_KEY']))
    for api in (parent, worker):
        assert api.rows(**filters) == expected
        assert api.count(**filters) == len(expected)
        assert api.app_ids(**filters) == sorted({r['APP_ID'] for r in expected})


def test_segments_totals_unknown_filters_and_run_only_snapshot(apis):
    parent, worker, _ = apis
    assert parent.segments() == worker.segments()
    assert parent.total_apps() == worker.total_apps() == 2
    assert worker.con.execute('SELECT DISTINCT run_id FROM runs').fetchall() == [('run',)]
    for api in (parent, worker):
        with pytest.raises(ValueError, match='Unknown filter'):
            api.rows(nonexistent='anything')


@pytest.mark.skipif(os.name == 'nt', reason='native fcntl is POSIX-only; Windows stub preserved')
def test_native_fcntl_really_excludes_another_process(tmp_path):
    import fcntl
    from mart import store as module
    assert module.fcntl is fcntl
    store = MartStore(tmp_path / 'locked.duckdb')
    child = '''import fcntl,sys
with open(sys.argv[1], 'a') as f:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('locked')
    else:
        print('available')
'''
    with store.connection():
        result = subprocess.run([sys.executable, '-c', child, str(store.path) + '.lock'],
                                text=True, capture_output=True, check=True, timeout=10)
    assert result.stdout.strip() == 'locked'
    result = subprocess.run([sys.executable, '-c', child, str(store.path) + '.lock'],
                            text=True, capture_output=True, check=True, timeout=10)
    assert result.stdout.strip() == 'available'


def test_windows_fcntl_import_fallback_is_preserved(monkeypatch):
    import builtins
    import runpy
    from mart import store as module
    original = builtins.__import__
    def without_fcntl(name, *args, **kwargs):
        if name == 'fcntl': raise ImportError('simulated Windows')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_fcntl)
    namespace = runpy.run_path(str(Path(module.__file__)))
    assert namespace['fcntl'].flock(None, namespace['fcntl'].LOCK_EX) is None
