import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from pipeline import build_dataset, PipelineError, Handlers, config_from_settings, query_facts, run_pair
from pipeline.core import aggregate, FACT_FIELDS
from pipeline.dataset import run_dataset

ROOT = Path(__file__).resolve().parents[2]


def document(app, **fields):
    return {'appData': {'application_id': app}, 'decisionResult': {'decision': 'APPROVE'}, **fields}


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'pipeline')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'BEFORE').mkdir(); (self.root/'AFTER').mkdir()

    def put(self, app, source, doc=None):
        (self.root/source/(app+'.json')).write_text(json.dumps(doc if doc is not None else document(app)))

    def pair(self, app):
        for source in ('BEFORE', 'AFTER'):
            self.put(app, source)

    def test_missing_corrupt_invalid_and_mismatched_answers_excluded(self):
        self.pair('valid')
        self.put('missing', 'BEFORE')
        self.pair('corrupt'); (self.root/'AFTER/corrupt.json').write_text('{broken')
        self.pair('invalid'); self.put('invalid', 'BEFORE', {})
        self.pair('mismatch'); self.put('mismatch', 'AFTER', document('wrong'))
        r = build_dataset(self.root)
        self.assertEqual(r['quality']['total_apps'], 5)
        self.assertEqual(r['quality']['excluded_apps'], 4)
        self.assertEqual({s['APP_ID'] for s in r['segments']}, {'valid'})
        self.assertEqual({r['APP_ID'] for r in r['comparison']}, {'valid'})
        self.assertEqual(set(r['quality']['issues_by_code']),
                         {'MISSING_RESPONSE', 'INVALID_JSON', 'INVALID_RESPONSE', 'NORMALIZATION_FAILED'})
        self.assertNotIn('broken', json.dumps(r['diagnostics']))

    def test_normalizer_failure_and_duplicate_output(self):
        self.pair('ok'); self.pair('bad')
        base = Handlers()
        def norm(doc, **kw):
            rows = base.normalize(doc, **kw)
            return rows+rows if kw['app_id']=='bad' else rows
        r = run_dataset(self.root, handlers=replace(base, normalize=norm))
        self.assertEqual(r['quality']['eligible_apps'], 1)
        self.assertEqual(r['quality']['excluded_apps'], 1)
        self.assertEqual(len(r['diagnostics']), 2)

    def test_comparison_error_excludes_whole_pair(self):
        self.pair('ok')
        r = run_dataset(self.root, handlers=replace(Handlers(), compare=lambda b, a: []))
        self.assertEqual(r['quality']['eligible_apps'], 0)
        self.assertEqual(r['facts'], [])
        self.assertEqual(r['diagnostics'][0]['CODE'], 'COMPARISON_FAILED')

    def test_missing_side_directory_is_diagnosed(self):
        (self.root/'AFTER').rmdir(); self.put('only', 'BEFORE')
        r = run_dataset(self.root)
        self.assertEqual(r['quality']['excluded_apps'], 1)
        self.assertEqual(r['diagnostics'][0]['SOURCE'], 'AFTER')

    def test_selected_facts_procedure_and_stage_order(self):
        self.pair('ok')
        calls = []
        def facts(rows):
            calls.append('custom-facts')
            return [r for r in aggregate(rows) if r['KEY']=='decision']
        r = build_dataset(self.root, facts_cfg=facts, event=lambda e: calls.append((e['stage'], e['status'])))
        self.assertTrue(r['facts'])
        self.assertTrue(all(f['KEY']=='decision' for f in r['facts']))
        self.assertLess(calls.index(('facts','start')), calls.index('custom-facts'))
        self.assertLess(calls.index('custom-facts'), calls.index(('facts','finish')))
        self.assertLess(calls.index(('normalize','finish')), calls.index(('compare','start')))

    def test_custom_counts_cannot_lie_or_duplicate(self):
        self.pair('ok')
        for fn in [lambda rows: [dict(r, CNT=999) for r in aggregate(rows)],
                   lambda rows: aggregate(rows)*2]:
            with self.assertRaises(PipelineError):
                build_dataset(self.root, facts_cfg=fn)

    def test_samples_and_uniqueness(self):
        r = build_dataset(ROOT/'task/samples')
        self.assertEqual(r['quality']['eligible_apps'], 4)
        self.assertEqual(r['quality']['excluded_apps'], 0)
        self.assertEqual(len(r['comparison']), 595)
        self.assertEqual(len({(x['APP_ID'],x['PATH'],x['ARRAY_KEY']) for x in r['comparison']}),595)


class SettingsAndTotalsTests(unittest.TestCase):
    def test_json_settings_apply_to_both_slices(self):
        cfg = config_from_settings({'version':1,'exclude':['appData.request_id'],
                                    'arrays':{'tags':{'mode':'value'}},
                                    'buckets':{'x': [{'lower':0,'upper':1,'label':'unit'}]}})
        r = run_pair(document('x', x=.42, tags=['b','a']),document('x',x=.58,tags=['a','b']), app_id='x',config=cfg)
        x = next(row for row in r['comparison'] if row['PATH']=='x')
        self.assertEqual((x['ADJUSTED_BEFORE'],x['ADJUSTED_AFTER'],x['DIFF_FLAG']),('unit','unit',1))

    def test_bad_settings(self):
        for settings in [{'exclude':'field'}, {'arrays':{'x':{'mode':'field'}}},
                         {'arrays':{'x':{'mode':'index','keys':['id']}}},
                         {'buckets':{'x':[{'lower':1,'upper':0,'label':'bad'}]}},
                         {'buckets':{'x':[{'lower':0,'upper':2,'label':'a'}, {'lower':1,'upper':3,'label':'b'}]}}]:
            with self.assertRaises(PipelineError): config_from_settings(settings)

    def test_totals_are_distinct_not_sum_of_groups(self):
        rows, segments = [], []
        for app in ('a','b'):
            r = run_pair(document(app,x=1,y=2),document(app,x=2,y=2), app_id=app)
            rows.extend(r['comparison']); segments.extend(r['segments'])
        r = query_facts(rows,segments,dimensions=['PATH','SOURCE'])
        self.assertEqual(r['total_apps'],2)
        self.assertGreater(sum(x['CNT'] for x in r['rows']),r['total_apps'])
        self.assertEqual(r['total_facts'],len(rows)*2)


class ProductAndPresenceTests(unittest.TestCase):
    def test_product_filter_correlates_model_with_same_product(self):
        from pipeline.pivot import segment_matches
        from pipeline import segment
        def response(models):
            return document('x', decisionResult={'decision':'APPROVE','product_trials':[
                {'product_id':p,'partner_id':'bank-'+p,'scoring':{'pd':{'modelName':m}},
                 'cutOff':{'name':'cut-'+m},'routing_method':'api'} for p,m in models]})
        s=segment(response([('one','A'),('two','B')]),response([('two','A'),('one','B')]),app_id='x')
        self.assertTrue(segment_matches(s,{'product':'one','pd_model_after':'B'}))
        self.assertFalse(segment_matches(s,{'product':'one','pd_model_after':'A'}))
        self.assertTrue(segment_matches(s,{'product':'one','partner_id':'bank-one'}))

    def test_new_and_lost_attributes_only_generate_present_facts(self):
        b=document('x', present_null=None, lost='')
        a=document('x', present_null=None, new='')
        r=run_pair(b,a,app_id='x')
        result=query_facts(r['comparison'],r['segments'],dimensions=['KEY','SOURCE'])
        members=result['members']
        self.assertEqual({m['SOURCE'] for m in members if m['KEY']=='lost'},{'BEFORE (MASTER)'})
        self.assertEqual({m['SOURCE'] for m in members if m['KEY']=='new'},{'AFTER (BETA)'})
        self.assertEqual(len([m for m in members if m['KEY']=='present_null']),2)

    def test_both_answers_missing_when_expected_ids_supplied(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'pipeline') as name:
            root=Path(name)
            (root/'BEFORE').mkdir(); (root/'AFTER').mkdir()
            r=build_dataset(root,expected_app_ids=['not-returned'])
            self.assertEqual(r['quality']['total_apps'],1)
            self.assertEqual(r['quality']['excluded_apps'],1)
            self.assertEqual(r['quality']['issues_by_code']['MISSING_RESPONSE'],2)
