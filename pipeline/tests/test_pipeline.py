import csv
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pipeline import (ArrayRule, Config, Handlers, PipelineError, aggregate, compare,
                      default_config, drill_down, example_config, normalize, run_directories,
                      run_pair, segment)
from pipeline.runner import read_document

ROOT = Path(__file__).resolve().parents[2]


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file, delimiter=';'))


def canonical(rows, fields):
    return sorted(tuple(str(row[f]) for f in fields) for row in rows)


class GoldenTests(unittest.TestCase):
    def test_all_example_csv_stages(self):
        for folder, legacy in [('example', True), ('examples', False)]:
            with self.subTest(folder=folder):
                files = sorted((ROOT / 'task' / folder).glob('*.json'))
                pair = run_pair(read_document(files[0]), read_document(files[1]), app_id='1001',
                                config=example_config(legacy))
                stages = [('03', 'normalized_before'), ('04', 'normalized_after')]
                for prefix, key in stages:
                    expected = csv_rows(next((ROOT / 'task' / folder).glob(prefix + '*.csv')))
                    self.assertEqual(canonical(pair[key], expected[0]), canonical(expected, expected[0]))
                union = csv_rows(next((ROOT / 'task' / folder).glob('05*.csv')))
                self.assertEqual(canonical(pair['normalized_before'] + pair['normalized_after'], union[0]),
                                 canonical(union, union[0]))
                expected = csv_rows(next((ROOT / 'task' / folder).glob('06*.csv')))
                fields = [f for f in expected[0] if f != 'COMMENT']
                self.assertEqual(canonical(pair['comparison'], fields), canonical(expected, fields))
                self.assertEqual(sum(r['DIFF_FLAG'] for r in pair['comparison']), 6)

    def test_samples(self):
        result = run_directories(ROOT / 'task/samples/BEFORE', ROOT / 'task/samples/AFTER')
        self.assertEqual(len(result['segments']), 4)
        rows = result['comparison']
        self.assertEqual(len(rows), 595)
        self.assertTrue(all(not r['DIFF_FLAG'] for r in rows if r['APP_ID'] == 'I32554355'))
        self.assertTrue(any(r['CHANGE_TYPE'] == 'ATTRIBUTE_NEW' and
                            r['PATH'].startswith('decisionResult.scoring.')
                            for r in rows if r['APP_ID'] == 'I32554358'))
        self.assertTrue(any(r['PATH'] == 'characteristics.score_okb' and r['DIFF_FLAG']
                            for r in rows if r['APP_ID'] == 'I32554362'))
        self.assertFalse(any(r['PATH'] in default_config().exclude for r in rows))
        self.assertEqual(len({(r['APP_ID'], r['PATH'], r['ARRAY_KEY']) for r in rows}), len(rows))
        # Every aggregate resolves to exactly CNT member attributes and their primary keys.
        for fact in result['facts']:
            members = drill_down(rows, **{k: v for k, v in fact.items() if k != 'CNT'})
            self.assertEqual(len(members), fact['CNT'])
        self.assertEqual(sum(f['CNT'] for f in result['facts']),
                         len(result['normalized_before']) + len(result['normalized_after']))


class EdgeTests(unittest.TestCase):
    def norm(self, doc, config=None, source='BEFORE'):
        return normalize(doc, app_id='x', source=source, config=config)

    def test_four_states_and_null_false_empty(self):
        b = self.norm({'x': {'same': 1, 'changed': None, 'lost': '', 'empty': ''}})
        a = self.norm({'x': {'same': 1, 'changed': False, 'new': None, 'empty': ''}}, source='AFTER')
        rows = {r['KEY']: r for r in compare(b, a)}
        self.assertEqual({k: r['CHANGE_TYPE'] for k, r in rows.items()},
                         dict(same='SAME', changed='VALUE_CHANGED', lost='ATTRIBUTE_LOST',
                              new='ATTRIBUTE_NEW', empty='SAME'))
        self.assertEqual(rows['changed']['VALUE_BEFORE'], 'NULL')
        self.assertEqual(rows['changed']['VALUE_AFTER'], 'false')
        facts = aggregate(list(rows.values()))
        self.assertEqual(sum(f['CNT'] for f in facts), len(b) + len(a))

    def test_arrays_field_composite_nested_and_index(self):
        d = {'decisionResult': {
            'available_offers': [{'product_id': 1, 'type': 'manual', 'rank': 2},
                                 {'product_id': 1, 'type': 'standard', 'rank': 1}],
            'scoring': [{'modelName': 'A', 'value': [.2, .3], 'features': {'age': 40}},
                        {'modelName': 'B', 'value': [.3, .2], 'features': {'age': 50}}]}}
        a = json.loads(json.dumps(d))
        a['decisionResult']['available_offers'].reverse()
        a['decisionResult']['scoring'].reverse()
        self.assertTrue(all(r['CHANGE_TYPE'] == 'SAME' for r in compare(self.norm(d), self.norm(a))))
        a['decisionResult']['scoring'][0]['value'].reverse()
        changes = [r for r in compare(self.norm(d), self.norm(a)) if r['DIFF_FLAG']]
        self.assertEqual(len(changes), 2)
        self.assertEqual({r['ARRAY_KEY'] for r in changes}, {'B|0', 'B|1'})
        self.assertTrue(all(r['KEY'] == 'value' for r in changes))

    def test_value_arrays_set_semantics_and_escaping(self):
        config = Config(arrays={'x': ArrayRule('value')})
        b = self.norm({'x': ['a|b', 'a:b', None, '~NULL', 'NULL', 'x', 'x']}, config)
        a = self.norm({'x': ['NULL', '~NULL', None, 'a:b', 'a|b', 'x']}, config)
        self.assertEqual(len(b), 6)
        self.assertTrue(all(not r['DIFF_FLAG'] for r in compare(b, a)))

    def test_scalar_types_are_not_conflated(self):
        for left, right in [(None, 'NULL'), (True, 'true'), (1, '1')]:
            row = compare(self.norm({'x': left}), self.norm({'x': right}))[0]
            self.assertEqual(row['CHANGE_TYPE'], 'VALUE_CHANGED')

    def test_invalid_inputs_and_duplicates(self):
        cases = [({'unknown': []}, None),
                 ({'decisionResult': {'product_trials': [{'product_id': 1}, {'product_id': 1}]}}, None),
                 ({'decisionResult': {'product_trials': [{}]}}, None),
                 ({'appData': {'application_id': 'other'}}, None),
                 ({'bad.field': 1}, None), ({'x': float('nan')}, None)]
        for document, config in cases:
            with self.subTest(document=document), self.assertRaises(PipelineError):
                self.norm(document, config)
        row = self.norm({'x': 1})
        with self.assertRaises(PipelineError):
            compare(row + row, [])

    def test_exclusion_and_bucket_policy(self):
        config = Config(exclude=('ignore',), bucket=lambda path, value: 'one bucket')
        b = self.norm({'x': .42, 'ignore': {'arr': [1]}}, config)
        a = self.norm({'x': .58}, config)
        self.assertEqual(len(b), 1)
        row = compare(b, a)[0]
        self.assertEqual(row['ADJUSTED_BEFORE'], row['ADJUSTED_AFTER'])
        self.assertEqual(row['DIFF_FLAG'], 1)

    def test_segment_associations_and_order(self):
        def doc(models):
            return {'decisionResult': {'decision': 'APPROVE', 'available_offers': [
                {'product_id': 2, 'rank': 2}, {'product_id': 1, 'rank': 1}],
                'product_trials': [{'product_id': p, 'scoring': {'pd': {'modelName': m}},
                                    'partner_type': 'CURRENT', 'routing_method': 'api'}
                                   for p, m in models]}}
        b, a = doc([(1, 'A'), (2, 'B')]), doc([(2, 'B'), (1, 'A')])
        row = segment(b, a, app_id='x')
        self.assertEqual(row['PD_MODEL_BEFORE'], row['PD_MODEL_AFTER'])
        a = doc([(1, 'B'), (2, 'A')])
        row = segment(b, a, app_id='x')
        self.assertNotEqual(row['PD_MODEL_BEFORE'], row['PD_MODEL_AFTER'])
        self.assertEqual(row['TOP_OFFER_AFTER'], '1')
        self.assertEqual(row['PRODUCTS'], '1;2')

    def test_handlers_replaced_and_progress(self):
        calls = []
        def custom(doc, **kwargs):
            calls.append(kwargs['source'])
            return normalize(doc, **kwargs)
        h = replace(Handlers(), normalize=custom)
        progress = []
        result = run_directories(ROOT / 'task/samples/BEFORE', ROOT / 'task/samples/AFTER',
                                 handlers=h, progress=progress.append)
        self.assertEqual(len(calls), 8)
        self.assertEqual(progress[-1]['completed'], 4)
        self.assertEqual(len(result['segments']), 4)

    def test_io_unpaired_bad_json_and_cli(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'pipeline') as temp:
            base = Path(temp)
            b, a = base / 'b', base / 'a'
            b.mkdir(); a.mkdir()
            (b / 'x.json').write_text('{"x":null}')
            with self.assertRaises(PipelineError):
                run_directories(b, a)
            result = run_directories(b, a, allow_unpaired=True)
            self.assertEqual(result['comparison'][0]['CHANGE_TYPE'], 'ATTRIBUTE_LOST')
            (a / 'x.json').write_text('{"x":false}')
            output = base / 'out.json'
            args = [sys.executable, '-m', 'pipeline', '--before', str(b), '--after', str(a),
                    '--output', str(output)]
            completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            repeated = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(repeated.returncode, 2)
            self.assertIn('already exists', repeated.stderr)
            (a / 'x.json').write_text('{"x":1,"x":2}')
            with self.assertRaises(PipelineError):
                run_directories(b, a)


class HandoffTests(unittest.TestCase):
    def test_organizer_checks_execute_unchanged(self):
        result = run_directories(ROOT / 'task/samples/BEFORE', ROOT / 'task/samples/AFTER')

        class FixtureMart:
            # Only a test adapter. Production indexed MartAPI belongs to participant 2.
            def rows(self, **filters):
                for row in result['comparison']:
                    passed = True
                    for key, value in filters.items():
                        if key == 'path_prefix':
                            passed &= row['PATH'].startswith(value)
                        else:
                            column = {'app_ids': 'APP_ID'}.get(key, key.upper())
                            passed &= (row[column] in value if isinstance(value, (list, tuple, set))
                                       else row[column] == value)
                    if passed:
                        yield row

            def count(self, **filters):
                return sum(1 for _ in self.rows(**filters))

            def app_ids(self, **filters):
                return sorted({r['APP_ID'] for r in self.rows(**filters)})

            def segments(self):
                return iter(result['segments'])

            def total_apps(self):
                return len(result['segments'])

        for path in (ROOT / 'task/checks').glob('check_*.py'):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = module.CHECK['entrypoint'](FixtureMart(), module.CHECK['params'])
            self.assertIsInstance(output, list)
            for row in output:
                self.assertIn(row['RESULT'], ('PASS', 'FAIL'))
                self.assertIn(row['SOURCE'], ('BEFORE', 'AFTER'))
                self.assertTrue(set(row['APP_IDS']) <= {s['APP_ID'] for s in result['segments']})

    def test_all_stages_replaceable_and_validated(self):
        calls = []
        def wrapped(name, function):
            def call(*args, **kwargs):
                calls.append(name)
                return function(*args, **kwargs)
            return call
        h = Handlers(**{name: wrapped(name, getattr(Handlers(), name))
                        for name in ('normalize', 'compare', 'segment', 'aggregate')})
        run_directories(ROOT / 'task/samples/BEFORE', ROOT / 'task/samples/AFTER', handlers=h)
        self.assertEqual(set(calls), {'normalize', 'compare', 'segment', 'aggregate'})
        with self.assertRaises(PipelineError):
            run_pair({}, {}, app_id='x', handlers=replace(Handlers(), compare=lambda b, a: [{}]))

    def test_null_turning_rule_off_compatible_with_organizer_check(self):
        def doc(hit):
            return {'decisionResult': {'product_trials': [
                {'product_id': 1, 'rules': [{'name': 'STOP', 'hit': hit}]}]}}
        pair = run_pair(doc(True), doc(None), app_id='x')
        row = next(r for r in pair['comparison'] if r['KEY'] == 'hit')
        self.assertEqual((row['VALUE_BEFORE'], row['VALUE_AFTER']), ('true', 'NULL'))

    def test_demo_fact_schema(self):
        from pipeline import FACT_FIELDS
        rows = csv_rows(ROOT / 'task/examples/07_pivot_facts_demo.csv')
        self.assertEqual(tuple(rows[0]), FACT_FIELDS)
        result = run_directories(ROOT / 'task/samples/BEFORE', ROOT / 'task/samples/AFTER')
        self.assertTrue(all(tuple(f) == FACT_FIELDS for f in result['facts']))


if __name__ == '__main__':
    unittest.main()
