import csv
from concurrent.futures import ThreadPoolExecutor
import pytest

from mart import MartStore
from mart.demo import educational_rows, segment, ROOT
from mart.store import FACT_DIMS


@pytest.fixture
def store(tmp_path):
    store = MartStore(tmp_path / 'mart.duckdb')
    store.save_run('one', educational_rows(), [segment()])
    return store


def test_educational_contract_and_vertical_storage(store):
    mart = store.for_run('one')
    assert mart.count() == 14
    assert mart.count(diff_flag=1) == 6
    assert mart.rows() == sorted(educational_rows(), key=lambda r: (r['APP_ID'], r['PATH'], r['ARRAY_KEY']))
    assert mart.total_apps() == 1
    assert mart.summary() == dict(total_apps=1, changed_apps=1, decision_changed_apps=1)
    with store.connection() as con:
        assert con.execute('SELECT count(*) FROM datamart').fetchone()[0] == 26
        assert con.execute("SELECT table_type FROM information_schema.tables WHERE table_name='comparison'").fetchone()[0] == 'VIEW'
    # No synthetic BEFORE fact for an attribute that only exists AFTER.
    assert mart.aggregates(['SOURCE'], filters={'PATH': 'company.decisionResult.riskSegment'}) == [{'SOURCE': 'AFTER (BETA)', 'CNT': 1}]
    assert sum(r['CNT'] for r in mart.aggregates()) == 26
    assert mart.aggregates(['SOURCE']) == [{'SOURCE': 'AFTER (BETA)', 'CNT': 1}, {'SOURCE': 'BEFORE (MASTER)', 'CNT': 1}]


@pytest.mark.parametrize('filters', [
    {'path': 'company.decisionResult.decision'},
    {'path': ['company.decisionResult.decision', 'company.decisionResult.businessDecision']},
    {'path_prefix': 'company.decisionResult.'}, {'key': 'decision'},
    {'array_key': 'TAKEUP_RATE_IP'}, {'diff_flag': 0},
    {'change_type': ['ATTRIBUTE_NEW', 'ATTRIBUTE_LOST']},
    {'value_before': 'APPROVE', 'value_after': 'VERIFICATION'},
    {'app_ids': ['1001']}, {'app_ids': []}, {'path': []},
    {'value_before': "' OR 1=1 --"}, {'path_prefix': '%'},
])
def test_filters_match_python_oracle(store, filters):
    def match(row):
        for key, value in filters.items():
            col = 'APP_ID' if key == 'app_ids' else 'PATH' if key == 'path_prefix' else key.upper()
            if key == 'path_prefix':
                if not row[col].startswith(value):
                    return False
            elif isinstance(value, list):
                if row[col] not in value:
                    return False
            elif row[col] != value:
                return False
        return True
    expected = [r for r in educational_rows() if match(r)]
    mart = store.for_run('one')
    assert mart.count(**filters) == len(expected)
    assert mart.app_ids(**filters) == sorted({r['APP_ID'] for r in expected})
    assert len(mart.rows(**filters)) == len(expected)


def test_fact_members_and_segment_filters(store):
    mart = store.for_run('one')
    for fact in mart.aggregates():
        members = mart.drill_down(filters={k: fact[k] for k in FACT_DIMS})
        assert len({m['APP_ID'] for m in members}) == fact['CNT']
        assert all(mart.application(m['APP_ID'], path=m['PATH'], array_key=m['ARRAY_KEY']) for m in members)
    assert mart.aggregates(['SOURCE'], segment_filters={'product': 'DEM'}) == []
    assert mart.aggregates(['SOURCE'], segment_filters={'product': 'DEMO', 'DECISION_CHANGED': 1})
    assert mart.aggregates(['PRODUCTS', 'SOURCE'])[0]['PRODUCTS'] == 'DEMO'
    with pytest.raises(ValueError):
        mart.rows(unknown='foo')
    with pytest.raises(ValueError):
        mart.aggregates(['KEY); DROP TABLE runs;--'])
    assert mart.page(limit=2, offset=2) == mart.rows()[2:4]


def test_atomic_validation_isolation_and_stale_reports(store):
    original_revision = store.run_info('one')['revision']
    rows = educational_rows()
    with pytest.raises(ValueError, match='Duplicate'):
        store.save_run('one', rows + rows, [segment()])
    assert store.for_run('one').count() == 14
    rows[0]['DIFF_FLAG'] = 0
    with pytest.raises(ValueError, match='DIFF_FLAG'):
        store.save_run('one', rows, [segment()])
    store.save_run('two', [], [segment('empty', DECISION_BEFORE='', DECISION_AFTER='', DECISION_CHANGED=0)])
    assert store.for_run('two').total_apps() == 1
    assert store.for_run('two').count() == 0
    store.save_check_report('one', original_revision, {'rows': []})
    store.save_run('one', educational_rows(), [segment()])
    assert store.get_check_report('one') is None
    with pytest.raises(ValueError, match='changed'):
        store.save_check_report('one', original_revision, {'rows': []})


def test_normalized_input_validation(store):
    comparison = educational_rows()
    # The old educational UNION omits PATH; the integration boundary requires it
    # explicitly, because arbitrary scalar-array paths cannot be reconstructed.
    with (ROOT / 'task/example/05_union.csv').open(encoding='utf-8-sig') as f:
        normalized = list(csv.DictReader(f, delimiter=';'))
    for row in normalized:
        row['PATH'] = next(c['PATH'] for c in comparison if all(c[k] == row[k] for k in ['PREFIX', 'CLASS', 'SUFFIX', 'ARRAY_KEY', 'KEY']))
    store.save_run('one', comparison, [segment()], normalized_rows=normalized)
    normalized[0]['VALUE'] = 'wrong'
    with pytest.raises(ValueError, match='disagree'):
        store.save_run('one', comparison, [segment()], normalized_rows=normalized)


def test_concurrent_writes_and_reopen(store):
    def save(i):
        other = MartStore(store.path)
        other.save_run(str(i), educational_rows(), [segment()])
        return other.for_run(str(i)).count()
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(save, range(4))) == [14] * 4
    assert MartStore(store.path).for_run('one').count() == 14


def test_sql_failure_rolls_back_entire_run(store, monkeypatch):
    revision = store.run_info('one')['revision']
    def fail(*args):
        raise RuntimeError('simulated import failure')
    monkeypatch.setattr(store, '_insert', fail)
    with pytest.raises(RuntimeError):
        store.save_run('one', educational_rows(), [segment()])
    assert store.run_info('one')['revision'] == revision
    assert store.for_run('one').count() == 14
