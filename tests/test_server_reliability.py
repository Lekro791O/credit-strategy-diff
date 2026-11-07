"""Regression checks for the review: use real HTTP boundaries and worker processes."""
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from api.main import app, manager
from api import config, workspace
from api.models import RunConfig, ProcedureSource
from api.runs import RunManager

SAMPLES = str(Path(__file__).resolve().parents[1] / 'task/samples')
PASS = '''
import os
def run(mart, params):
    assert os.getpid() != params['server_pid']
    return [dict(LABEL='custom', SOURCE='AFTER', ARGUMENT=str(params['value']),
        CNT=0, TOTAL=mart.total_apps(), METRIC=0, APP_IDS=[], RESULT='PASS', CONFIDENCE='HIGH')]
CHECK = dict(id='custom', label='custom', params={'value': 7}, entrypoint=run)
'''


def wait(client, rid):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get('/api/runs/' + rid)
        assert response.status_code == 200, response.text
        run = response.json()['run']
        if run['status'] in ('done', 'error'):
            return run
        time.sleep(.03)
    pytest.fail('run timed out')


def launch(client, **kwargs):
    cfg = dict(dataset=SAMPLES, llm_opinion=False, **kwargs)
    response = client.post('/api/runs', json=cfg)
    assert response.status_code == 200, response.text
    return wait(client, response.json()['run']['id'])


@pytest.fixture
def finished():
    client = TestClient(app)
    run = launch(client)
    assert run['status'] == 'done', run['error']
    return client, run


def test_every_run_resource_checks_signed_owner(finished):
    owner, run = finished
    other = TestClient(app)
    rid = run['id']
    routes = [('GET', ''), ('GET','/facts'), ('GET','/apps'), ('GET','/apps/I32554355'),
              ('GET','/quality'), ('GET','/decision_matrix'), ('GET','/checks'),
              ('GET','/checks/export.html'), ('POST','/pivot'), ('POST','/pivot/apps'),
              ('POST','/checks/try'), ('POST','/checks/add_result'), ('POST','/llm'), ('POST','/repeat')]
    for method, suffix in routes:
        response = other.request(method, '/api/runs/' + rid + suffix, json={}, headers={'X-User-Id': run['config']['user']})
        assert response.status_code == 404, (suffix, response.text)
    assert other.get('/api/runs').json()['runs'] == []
    # Knowing owner ID and forging cookie contents is insufficient without signature.
    cookie = owner.cookies.get('csd_session')
    other.cookies.set('csd_session', cookie[:-1] + ('a' if cookie[-1] != 'a' else 'b'))
    assert other.get('/api/runs/' + rid).status_code == 404
    assert owner.get('/api/runs/' + rid, headers={'X-User-Id': 'someone-else'}).status_code == 200
    assert owner.post('/api/runs/' + rid + '/repeat', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_config_save_load_launch_is_distinct_from_repeat():
    a, b = TestClient(app), TestClient(app)
    cfg = dict(name='saved config', dataset=SAMPLES, llm_opinion=False,
               normalize={'settings': {'exclude': ['appData.request_id']}},
               checks=[{'source': {'kind': 'builtin', 'name': 'attribute_new_loss'}, 'params': {'app_ids_limit': 2}}])
    assert a.post('/api/configs', json=cfg).status_code == 200
    saved = a.get('/api/configs/saved config').json()['config']
    assert saved['normalize']['settings'] == cfg['normalize']['settings']
    assert b.get('/api/configs/saved config').status_code == 404
    assert b.post('/api/configs/saved config/run').status_code == 404
    assert a.post('/api/configs', json={'name': 'broken'}).status_code == 422
    first = wait(a, a.post('/api/configs/saved config/run').json()['run']['id'])
    assert first['status'] == 'done', first['error']
    second = wait(a, a.post('/api/runs/' + first['id'] + '/repeat').json()['run']['id'])
    assert second['status'] == 'done'
    assert second['id'] != first['id'] and second['rerun_of'] == first['id']
    assert second['config'] == first['config']
    assert a.get('/api/runs/' + first['id']).json()['run'] == first
    assert a.delete('/api/configs/saved config').status_code == 200
    assert a.get('/api/configs/saved config').status_code == 404


def test_adhoc_file_workspace_params_and_errors_persist(finished, monkeypatch):
    monkeypatch.setenv('CSD_TRUST_LOCAL_CODE', '1')
    client, run = finished
    rid = run['id']
    assert client.put('/api/workspace/check_one', json={'code': PASS}).status_code == 200
    for source in [{'kind': 'code', 'code': PASS}, {'kind': 'workspace', 'name': 'check_one'}, {'kind': 'file', 'name': 'check_one.py'}]:
        response = client.post('/api/runs/' + rid + '/checks/try', json=dict(source=source, params={'server_pid': os.getpid(), 'value': 31}))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['status'] == 'COMPLETED', data
        assert data['persisted'] and data['rows'][0]['ARGUMENT'] == '31'
    result = client.post('/api/runs/' + rid + '/checks/try', json={'code': 'raise RuntimeError("boom")'}).json()
    assert result['errors'][0]['diagnostic']['message'] == 'boom'
    report = client.get('/api/runs/' + rid + '/checks').json()
    assert report['status'] == 'COMPLETED_WITH_ERRORS'
    assert len([r for r in report['rows'] if r.get('_check_id') == 'custom']) == 3
    assert report['errors']
    assert 'boom' in client.get('/api/runs/' + rid + '/checks/export.html').text
    assert client.post('/api/runs/' + rid + '/checks/add_result', json={'rows': [{'RESULT':'PASS'}]}).status_code == 422
    # Exact legacy echo is idempotent, cannot insert client-fabricated rows.
    assert client.post('/api/runs/' + rid + '/checks/add_result', json={'rows': report['rows'][-1:]}).status_code == 200
    assert len(client.get('/api/runs/' + rid + '/checks').json()['rows']) == len(report['rows'])


def test_check_failure_does_not_hide_next_check(monkeypatch):
    monkeypatch.setenv('CSD_TRUST_LOCAL_CODE', '1')
    client = TestClient(app)
    run = launch(client, checks=[
        {'source': {'kind': 'code', 'code': 'import os; os._exit(9)'}},
        {'source': {'kind': 'builtin', 'name': 'external_score_drift'}}])
    assert run['status'] == 'done'
    assert len(run['check_rows']) == 2
    assert run['errors'][0]['error']['error_type'] == 'WorkerExit'
    assert run['check_executions'][1]['status'] == 'COMPLETED'
    assert run['check_executions'][0]['code'] == 'import os; os._exit(9)'


def test_timeout_and_missing_sandbox_are_visible(finished, monkeypatch):
    client, run = finished
    monkeypatch.setenv('CSD_TRUST_LOCAL_CODE', '1')
    monkeypatch.setenv('CSD_CODE_TIMEOUT', '.3')
    response = client.post('/api/runs/' + run['id'] + '/checks/try', json={'code': 'while True: pass'}).json()
    assert response['executions'][0]['status'] == 'TIMEOUT'
    monkeypatch.delenv('CSD_TRUST_LOCAL_CODE')
    monkeypatch.setattr('checkrunner.runner.shutil.which', lambda _: None)
    result = client.post('/api/runs/' + run['id'] + '/checks/try', json={'code': PASS}).json()
    assert result['executions'][0]['status'] == 'ERROR'
    assert 'Docker is unavailable' in result['executions'][0]['diagnostic']['message']


def test_selected_procedures_order_progress_and_source_freeze(monkeypatch):
    monkeypatch.setenv('CSD_TRUST_LOCAL_CODE', '1')
    client = TestClient(app)
    code = "from pipeline import aggregate\nimport time\ndef run(rows):\n    time.sleep(.1)\n    return [f for f in aggregate(rows) if f['KEY']=='decision']\nPROCEDURE={'stage':'aggregate','entrypoint':run}\n"
    assert client.put('/api/workspace/facts', json={'code': code}).status_code == 200
    run = launch(client, facts={'kind': 'workspace', 'name': 'facts'}, stage_order=['normalize','compare','facts','checks'])
    assert run['status'] == 'done', run['error']
    assert all(r['KEY'] == 'decision' for r in run['fact_rows'])
    stages = run['stages']
    for previous, following in zip(stages, stages[1:]):
        assert previous['started'] <= previous['finished'] <= following['started']
    stage = next(s for s in stages if s['name']=='facts')
    assert stage['duration_seconds'] >= .1 and stage['code_snapshot'] == code
    for name in ('normalize', 'compare'):
        item = next(s for s in stages if s['name'] == name)
        assert item['completed'] == item['total'] == 4
    client.put('/api/workspace/facts', json={'code': 'changed later'})
    saved = client.get('/api/runs/' + run['id']).json()['run']
    assert next(s for s in saved['stages'] if s['name']=='facts')['code_snapshot'] == code
    assert client.post('/api/runs', json={'dataset': SAMPLES,'stage_order':['facts','normalize','compare','checks']}).status_code == 422


def test_procedure_timeout_and_invalid_facts(monkeypatch):
    monkeypatch.setenv('CSD_TRUST_LOCAL_CODE', '1')
    monkeypatch.setenv('CSD_CODE_TIMEOUT', '.5')
    client = TestClient(app)
    for code in ['while True: pass', "PROCEDURE={'stage':'aggregate','entrypoint':lambda rows:[{}]}"]:
        run = launch(client, facts={'kind':'code','code':code})
        assert run['status'] == 'error'
        facts = next(s for s in run['stages'] if s['name']=='facts')
        assert facts['status'] == 'error' and facts['finished'] and facts['error']
        assert run['stages'][-1]['status'] == 'skipped'


def test_paths_unknown_runs_and_atomic_state(finished, tmp_path):
    client, run = finished
    assert client.get('/api/runs/abcdefabcdef/facts').status_code == 404
    assert not (config.MART_DIR/'abcdefabcdef.duckdb').exists()
    other = workspace._user_dir('other-owner') / 'private.py'
    other.write_text('secret')
    for path in [str(other), '../other-owner/private.py', '/etc/passwd']:
        result = client.post('/api/runs/' + run['id'] + '/checks/try', json={'kind':'file','name':path}).json()
        assert result['executions'][0]['status'] == 'ERROR'
    assert client.post('/api/runs', json={'dataset': '/etc'}).status_code == 422
    assert client.post('/api/runs', json={'dataset': str(other.parent)}).status_code == 422
    # Atomic publication while readers repeatedly parse the same run.
    model = manager.load(run['id'])
    def writer():
        for i in range(20):
            model.error = str(i)
            manager.save(model)
    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(writer)
        for _ in range(100):
            assert manager.load(run['id']).id == run['id']
        job.result()


def test_restart_and_no_silent_sqlite_fallback(monkeypatch):
    local = RunManager()
    run = local.create(RunConfig(dataset=SAMPLES, llm_opinion=False))
    run.status = 'running'; run.stages[0].status='running';run.stages[0].started=time.time()
    local.save(run)
    local.recover_interrupted()
    recovered = local.load(run.id)
    assert recovered.status == 'error' and 'SERVER_RESTARTED' in recovered.error
    assert recovered.stages[0].finished
    def broken(*args): raise RuntimeError('duckdb unavailable')
    monkeypatch.setattr('api.mart.DuckMart.probe', broken)
    run = local.create(RunConfig(dataset=SAMPLES, llm_opinion=False))
    local._run_guarded(run.id)
    assert 'duckdb unavailable' in local.load(run.id).error
    assert not (config.MART_DIR/(run.id+'.sqlite')).exists()
