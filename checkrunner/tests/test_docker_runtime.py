"""Opt-in real Docker smoke test. Never claims a container was tested if unavailable."""
import os
import shutil
import subprocess
import time
from pathlib import Path
import pytest
from checkrunner import CheckRunner, CheckSpec
from mart import MartStore
from mart.demo import educational_rows, segment

pytestmark = pytest.mark.skipif(os.environ.get('CSD_TEST_DOCKER') != '1' or not shutil.which('docker'),
                                reason='requires Docker daemon and CSD_TEST_DOCKER=1')
ROOT = Path(__file__).resolve().parents[2]


def test_real_container_workers_and_app(tmp_path):
    subprocess.run(['docker','build','-f','checkrunner/Dockerfile','-t','credit-strategy-checkrunner:local','.'], cwd=ROOT, check=True, timeout=300)
    store = MartStore(tmp_path/'run.duckdb')
    store.save_run('demo',educational_rows(),[segment()])
    code = '''
import socket, os
assert not os.path.exists('/app/data')
def run(mart, params):
    try:
        socket.create_connection(('1.1.1.1', 443), timeout=.2)
    except OSError:
        pass
    else:
        raise AssertionError('network should be disabled')
    return [dict(LABEL='container', SOURCE='AFTER', ARGUMENT='', CNT=0,
        TOTAL=mart.total_apps(), METRIC=0, APP_IDS=[], RESULT='PASS', CONFIDENCE='HIGH')]
CHECK=dict(id='container', label='container', params={}, entrypoint=run)
'''
    assert CheckRunner().run(store,'demo',[CheckSpec(code=code)])['status']=='COMPLETED'
    result, _ = CheckRunner()._launch(None, dict(worker_kind='procedure',version=1,stage='aggregate',origin='test',
        code="PROCEDURE={'stage':'aggregate','entrypoint':lambda rows: []}",args=[[]]))
    assert result == {'ok':True,'rows':[]}
    subprocess.run(['docker','build','-t','credit-strategy-app:verify','.'],cwd=ROOT,check=True,timeout=300)
    name='csd-runtime-test-'+str(os.getpid())
    subprocess.run(['docker','run','--detach','--rm','--name',name,'-p','127.0.0.1::8000','credit-strategy-app:verify'],check=True)
    try:
        port=subprocess.check_output(['docker','port',name,'8000/tcp'],text=True).strip().split(':')[-1]
        import httpx
        with httpx.Client(base_url='http://127.0.0.1:'+port) as client:
            for _ in range(100):
                try:
                    if client.get('/api/datasets').status_code==200: break
                except httpx.HTTPError: pass
                time.sleep(.2)
            response=client.post('/api/runs',json={'dataset':'task/samples','llm_opinion':False})
            response.raise_for_status();rid=response.json()['run']['id']
            for _ in range(200):
                run=client.get('/api/runs/'+rid).json()['run']
                if run['status'] in ('done','error'): break
                time.sleep(.1)
            assert run['status']=='done',run['error']
            assert run['summary']['total_apps']==4
    finally:
        subprocess.run(['docker','rm','-f',name],check=False)
