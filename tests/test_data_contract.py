"""Actual app integration: vertical facts, eligibility and selected procedures."""
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from api.main import app, manager
from api import config
from api.demo_data import build_demo
from api.facts import fact_result, apps_in_group
from api.mart import SQLiteMart, DuckMart
from pipeline import build_dataset

ROOT = Path(__file__).resolve().parents[1]


def test_demo_has_unique_keys_and_consistent_values():
    for seed in (0, 7, 23):
        rows, segments = build_demo(40, seed)
        keys = [(r['APP_ID'],r['PATH'],r['ARRAY_KEY']) for r in rows]
        assert len(set(keys)) == len(keys)
        assert all(s['PRODUCTS'].split(';') for s in segments)
        assert all(r['DIFF_FLAG']==int(r['CHANGE_TYPE']!='SAME') for r in rows)
        store = SQLiteMart()
        store.load(rows,segments)
        assert store.total_apps()==40
        store.close()


@pytest.mark.parametrize('backend', ['sqlite','duck'])
def test_vertical_facts_exact_membership_and_product_filters(backend):
    data=build_dataset(ROOT/'task/samples')
    with tempfile.TemporaryDirectory(dir=ROOT/'pipeline') as name:
        mart=SQLiteMart(Path(name)/'m.sqlite') if backend=='sqlite' else DuckMart(Path(name)/'m.duckdb')
        mart.load(data['comparison'],data['segments'])
        result=fact_result(mart,['path','source','value_adjusted'],column_dimension='source')
        assert result['totals']['cnt_apps']==4
        assert result['totals']['cnt_rows']==567+581
        assert sum(r['cnt_apps'] for r in result['rows']) > 4
        for row in result['rows']:
            members=apps_in_group(mart,row['dims'])
            assert members['total']==row['cnt_apps']
        # A filtered cohort and its product groups use the same product associations.
        product=data['segments'][0]['PRODUCTS'].split(';')[0]
        result=fact_result(mart,['product','pd_model_after'],segment_filters={'product':product},
                           segment_rows=data['segments'])
        assert result['rows']
        assert {r['dims']['product'] for r in result['rows']}=={product}
        expected={s['APP_ID'] for s in data['segments'] if product in s['PRODUCTS'].split(';')}
        assert result['totals']['cnt_apps']==len(expected)
        selected=apps_in_group(mart,{},segment_filters={'product':product}, segment_rows=data['segments'])
        assert set(selected['app_ids'])==expected
        # A custom fact procedure may select canonical groups. Scope applies to
        # pivot, totals and drill-down, not merely a saved, unused result.
        scope=[f for f in data['facts'] if f['KEY']=='decision']
        result=fact_result(mart,['key','source'],fact_scope=scope)
        assert {r['dims']['key'] for r in result['rows']}=={'decision'}
        assert apps_in_group(mart,{'key':'score_okb'},fact_scope=scope)['total']==0
        mart.close()


def wait(client,rid):
    for _ in range(200):
        run=client.get('/api/runs/'+rid).json()['run']
        if run['status'] in ('done','error'): return run
        time.sleep(.05)
    pytest.fail('run did not finish')


def test_api_quality_and_custom_fact_procedure():
    config.ensure_dirs()
    user='data-contract-test'
    workspace=config.WORKSPACE_DIR/user
    workspace.mkdir(exist_ok=True)
    path=workspace/'only_decisions.py'
    # The file stays in this test's temporary app workspace, never in Git.
    path.write_text("from pipeline import aggregate\ndef run(rows):\n    return [r for r in aggregate(rows) if r['KEY']=='decision']\nPROCEDURE={'stage':'aggregate','entrypoint':run}\n")
    (workspace/'custom_normalize.py').write_text("from pipeline import normalize\ndef run(document, **kw):\n    return [r for r in normalize(document, **kw) if r['PATH'] != 'characteristics.score_equ']\nPROCEDURE={'stage':'normalize','entrypoint':run}\n")
    (workspace/'custom_compare.py').write_text("from pipeline import compare\ndef run(before, after):\n    assert not any(r['PATH']=='characteristics.score_equ' for r in before+after)\n    return compare(before, after)\nPROCEDURE={'stage':'compare','entrypoint':run}\n")
    client=TestClient(app)
    cfg={'dataset':str(ROOT/'task/samples'),'name':'real JSON quality', 'llm_opinion':False,
         'facts':{'kind':'workspace','name':'only_decisions'},
         'normalize':{'kind':'workspace','name':'custom_normalize'},
         'compare':{'kind':'workspace','name':'custom_compare'},
         'checks':[{'source':{'kind':'builtin','name':'check_attribute_new_loss.py'},'enabled':False}]}
    headers={'X-User-Id':user}
    # API callers cannot enable trusted execution by a request parameter.
    with patch.dict('os.environ',{'CSD_TRUST_LOCAL_PROCEDURES':'0'}):
        rid=client.post('/api/runs',json=cfg,headers=headers).json()['run']['id']
        assert wait(client,rid)['status']=='error'
    with patch.dict('os.environ',{'CSD_TRUST_LOCAL_PROCEDURES':'1'}):
        rid=client.post('/api/runs',json=cfg,headers=headers).json()['run']['id']
        run=wait(client,rid)
        assert run['status']=='done',run['error']
        assert run['quality']['eligible_apps']==4
        attrs=client.get(f'/api/runs/{rid}/apps/I32554355').json()['attributes']
        assert not any(row['PATH']=='characteristics.score_equ' for row in attrs)
        assert run['stages'][1]['code_ref']=='workspace:custom_compare'
        assert all(f['KEY']=='decision' for f in run['fact_rows'])
        pivot=client.post(f'/api/runs/{rid}/pivot',json={
            'dimensions':['key','source'],'column_dimension':'source'}).json()
        assert {r['dims']['key'] for r in pivot['rows']}=={'decision'}
        assert pivot['totals']['cnt_apps']==4
        assert client.get(f'/api/runs/{rid}/quality').json()['quality']['excluded_apps']==0
        assert client.post(f'/api/runs/{rid}/pivot/apps',json={'filters':{'key':'score_okb'}}).json()['total']==0


def test_settings_validation_and_unknown_pivot_filter():
    client=TestClient(app)
    r=client.post('/api/runs',json={'dataset':str(ROOT/'task/samples'),
                                  'normalize':{'settings':{'arrays':{'x':{'mode':'field'}}}}})
    assert r.status_code==422
    assert client.get('/api/normalization/schema').status_code==200


def test_api_exclusions_do_not_pollute_mart():
    with tempfile.TemporaryDirectory(dir=ROOT/'pipeline') as name:
        base=Path(name)
        (base/'BEFORE').mkdir(); (base/'AFTER').mkdir()
        for side in ('BEFORE','AFTER'):
            (base/side/'ok.json').write_text(json.dumps({'appData':{'application_id':'ok'},'decisionResult':{'decision':'APPROVE'}}))
        (base/'BEFORE/absent.json').write_text('{"appData":{"application_id":"absent"},"decisionResult":{"decision":"REJECT"}}')
        (base/'BEFORE/broken.json').write_text('{}')
        (base/'AFTER/broken.json').write_text('{bad')
        client=TestClient(app)
        rid=client.post('/api/runs',json={'dataset':name,'llm_opinion':False,
            'checks':[{'source':{'name':'check_attribute_new_loss.py'},'enabled':False}]}).json()['run']['id']
        run=wait(client,rid)
        assert run['status']=='done',run['error']
        assert run['quality']['total_apps']==3
        assert run['quality']['eligible_apps']==1
        assert run['quality']['excluded_apps']==2
        assert run['summary']['total_apps']==1
        quality=client.get(f'/api/runs/{rid}/quality').json()
        assert len(quality['diagnostics'])==3
        pivot=client.post(f'/api/runs/{rid}/pivot',json={'dimensions':['source','path']}).json()
        assert pivot['totals']['cnt_apps']==1
        assert client.post(f'/api/runs/{rid}/pivot/apps',json={}).json()['app_ids']==['ok']
