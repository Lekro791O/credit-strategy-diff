import json
from pathlib import Path
import pytest

from mart import MartStore
from mart.demo import educational_rows, segment
from checkrunner import CheckRunner, CheckSpec, provided_checks


@pytest.fixture
def store(tmp_path):
    store = MartStore(tmp_path / 'runs.duckdb')
    store.save_run('demo', educational_rows(), [segment()])
    return store


PASS_CODE = '''
def run(mart, params):
    return [dict(LABEL="custom", SOURCE="AFTER", ARGUMENT="", CNT=0,
                 TOTAL=mart.total_apps(), METRIC=0.0, APP_IDS=[], RESULT="PASS", CONFIDENCE="HIGH")]
CHECK = dict(id="custom", label="custom", params={"app_ids_limit": 20}, entrypoint=run)
'''


def local(**kwargs):
    return CheckRunner(backend='subprocess', trusted=True, **kwargs)


def test_all_five_unchanged_checks(store):
    events = []
    result = local().run(store, 'demo', on_event=events.append)
    assert result['status'] == 'COMPLETED', result
    assert len(result['executions']) == 5
    assert len(result['rows']) == 7
    assert {e['check_id'] for e in result['executions']} == {
        'ar_drop_by_product', 'attribute_new_loss', 'external_score_drift',
        'stop_factor_disappeared', 'target_model_changed'}
    assert store.get_check_report('demo') == result
    assert len(events) == 10
    assert result['verdict'] == 'FAIL'
    assert store.get_check_results('demo', check_id='ar_drop_by_product')[0]['ARGUMENT'] == 'DEMO'
    assert all(r['RESULT'] == 'FAIL' for r in store.get_check_results('demo', result='FAIL', app_id='1001'))


def test_code_file_and_params(store, tmp_path):
    path = tmp_path / 'custom.py'
    path.write_text(PASS_CODE.replace('custom', 'file_check'))
    result = local().run(store, 'demo', [CheckSpec(code=PASS_CODE, params={'app_ids_limit': 3}), CheckSpec(path=path)])
    assert result['status'] == 'COMPLETED'
    assert result['verdict'] == 'PASS'
    assert result['executions'][0]['params']['app_ids_limit'] == 3
    assert result['executions'][0]['code'] == PASS_CODE
    assert len(result['rows']) == 2


@pytest.mark.parametrize('bad', [
    'raise RuntimeError("bad import")', 'this is invalid syntax!',
    'import os; os._exit(7)',
    PASS_CODE.replace('METRIC=0.0', 'METRIC=float("nan")'),
    PASS_CODE.replace('RESULT="PASS"', 'RESULT="MAYBE"'),
    PASS_CODE.replace('CNT=0', 'CNT=1').replace('APP_IDS=[]', 'APP_IDS=["other-run"]'),
    'CHECK = {}',
])
def test_error_does_not_abort_remaining_checks(store, bad):
    result = local().run(store, 'demo', [CheckSpec(code=bad), CheckSpec(code=PASS_CODE)])
    assert result['status'] == 'COMPLETED_WITH_ERRORS'
    assert result['executions'][0]['status'] == 'ERROR'
    assert result['executions'][0]['diagnostic']
    assert result['executions'][1]['status'] == 'COMPLETED'
    assert len(result['rows']) == 1
    assert result['verdict'] is None


def test_timeout_and_duplicate(store):
    result = local(timeout=1).run(store, 'demo', [CheckSpec(code='while True: pass'), CheckSpec(code=PASS_CODE), CheckSpec(code=PASS_CODE)])
    assert result['executions'][0]['status'] == 'TIMEOUT'
    assert result['executions'][1]['status'] == 'COMPLETED'
    assert result['executions'][2]['status'] == 'ERROR'
    assert 'Duplicate' in result['executions'][2]['diagnostic']['message']


def test_output_limit(store):
    result = local(max_output_bytes=10000).run(store, 'demo', [CheckSpec(code='while True: print("x"*10000, flush=True)')])
    assert result['executions'][0]['status'] == 'ERROR'
    assert 'output' in result['executions'][0]['diagnostic']['message']


def test_sandbox_fails_closed_and_local_requires_trust(store, monkeypatch):
    with pytest.raises(ValueError, match='not a sandbox'):
        CheckRunner(backend='subprocess')
    monkeypatch.setattr('checkrunner.runner.shutil.which', lambda _: None)
    result = CheckRunner().run(store, 'demo', [CheckSpec(code=PASS_CODE)])
    assert result['executions'][0]['status'] == 'ERROR'
    assert 'Docker is unavailable' in result['executions'][0]['diagnostic']['message']


def test_snapshot_excludes_other_runs(store):
    store.save_run('secret', [], [segment('secret-id')])
    code = PASS_CODE.replace('return [dict', '''assert mart.con.execute("SELECT DISTINCT run_id FROM runs").fetchall() == [("demo",)]
    assert mart.con.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
    return [dict''')
    assert local().run(store, 'demo', [CheckSpec(code=code)])['status'] == 'COMPLETED'


def test_five_checks_signal_scenarios(store):
    def row(path, before, after, change='VALUE_CHANGED', array_key=''):
        return dict(APP_ID='1001', PREFIX='', CLASS='', SUFFIX='', ARRAY_KEY=array_key, KEY=path.split('.')[-1],
                    PATH=path, VALUE_BEFORE=before, VALUE_AFTER=after, ADJUSTED_BEFORE=before,
                    ADJUSTED_AFTER=after, DIFF_FLAG=int(change != 'SAME'), CHANGE_TYPE=change)
    rows = [row('characteristics.score_okb', '600', '700'),
            row('decisionResult.scoring.features.score_okb', '600', '650'),
            row('decisionResult.product_trials.rules.hit', 'true', '', 'ATTRIBUTE_LOST', 'P|deleted'),
            row('decisionResult.product_trials.rules.hit', 'true', 'false', array_key='P|off'),
            row('new', '', 'value', 'ATTRIBUTE_NEW')]
    store.save_run('demo', rows, [segment()])
    report = local().run(store, 'demo')
    assert report['status'] == 'COMPLETED'
    assert all(r['RESULT'] == 'FAIL' for r in report['rows'])
    assert all(r['APP_IDS'] == ['1001'] for r in report['rows'])
    assert {'deleted', 'off', 'предиктор != источник', 'model-v1 -> model-v2'} <= {r['ARGUMENT'] for r in report['rows']}


def test_html_export_escapes_user_content(store):
    from checkrunner.html_report import render_html
    report = local().run(store, 'demo', [CheckSpec(code=PASS_CODE)])
    report['rows'][0]['LABEL'] = '<script>alert(1)</script>'
    html = render_html(report, report_url='https://example.org/report?run=demo',
                       application_url=lambda app: 'https://example.org/app/' + app)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'https://example.org/report?run=demo' in html
    with pytest.raises(ValueError, match='HTTP'):
        render_html(report, report_url='javascript:alert(1)', application_url=lambda app: app)
