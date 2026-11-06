"""Executable integration example and repeatable query benchmark."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
import time

from mart import MartStore
from mart.store import SEGMENTS
from checkrunner import CheckRunner

ROOT = Path(__file__).resolve().parent.parent


def educational_rows():
    with (ROOT / 'task/example/06_comparison.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    for row in rows:
        row['DIFF_FLAG'] = int(row['DIFF_FLAG'])
    return rows


def segment(app_id='1001', **values):
    row = dict.fromkeys(SEGMENTS, '')
    row.update(APP_ID=app_id, DECISION_BEFORE='APPROVE', DECISION_AFTER='VERIFICATION',
               DECISION_CHANGED=1, PRODUCTS='DEMO', PD_MODEL_BEFORE='model-v1',
               PD_MODEL_AFTER='model-v2', CUTOFF_BEFORE='old', CUTOFF_AFTER='new')
    row.update(values)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database')
    parser.add_argument('--copies', type=int, default=1, help='Replicate the educational 14 attributes with distinct APP_IDs')
    args = parser.parse_args()
    if args.copies < 1:
        parser.error('--copies must be positive')
    with tempfile.TemporaryDirectory() as work:
        store = MartStore(args.database or Path(work) / 'demo.duckdb')
        fixture = educational_rows()
        rows = [dict(row, APP_ID=str(1001 + i)) for i in range(args.copies) for row in fixture]
        segs = [segment(str(1001 + i)) for i in range(args.copies)]
        timings = {}

        def measure(name, call):
            start = time.perf_counter()
            result = call()
            timings[name] = round(time.perf_counter() - start, 6)
            return result

        measure('save_run', lambda: store.save_run('demo', rows, segs))
        mart = store.for_run('demo')
        summary = measure('summary', mart.summary)
        facts = measure('counts', mart.aggregates)
        measure('regroup', lambda: mart.aggregates(['KEY', 'SOURCE', 'DIFF_FLAG']))
        measure('product_filter', lambda: mart.aggregates(['KEY', 'SOURCE'], segment_filters={'product': 'DEMO'}))
        measure('drill_down', lambda: mart.drill_down(filters={'KEY': 'decision', 'SOURCE': 'AFTER (BETA)'}))
        measure('attribute', lambda: mart.application('1001', path='company.decisionResult.decision'))
        report = measure('five_checks_including_snapshot', lambda: CheckRunner(backend='subprocess', trusted=True).run(store, 'demo'))
        measure('reopen_report', lambda: store.get_check_report('demo'))
        print(json.dumps(dict(summary=summary, comparison_rows=len(rows), facts=len(facts),
                              check_status=report['status'], executions=[dict(check_id=e.get('check_id'), status=e['status']) for e in report['executions']],
                              report_rows=len(report['rows']), seconds=timings), ensure_ascii=False, indent=2))
        if report['status'] != 'COMPLETED':
            raise SystemExit(json.dumps(report['executions'], ensure_ascii=False))


if __name__ == '__main__':
    main()
