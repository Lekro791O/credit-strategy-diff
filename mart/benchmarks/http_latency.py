"""Three real-HTTP repetitions on a completed organizer run; no API changes.

Run from repo root: python mart/benchmarks/http_latency.py --phase before
The state dir contains cookies/DBs and stays under gitignored data/.
"""
import argparse
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def server(state, dataset):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, CSD_DATA_DIR=str(state), CSD_DATASET_ROOTS=str(dataset))
    # Never enable server opt-in accidentally through the benchmark shell.
    env.pop('CSD_TRUST_LOCAL_CODE', None)
    env.pop('CSD_TRUST_LOCAL_PROCEDURES', None)
    with (state / 'http-server.log').open('a') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api.main:app',
                                 '--host', '127.0.0.1', '--port', str(port)],
                                cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=60, trust_env=False) as client:
                for _ in range(200):
                    if proc.poll() is not None:
                        raise RuntimeError('HTTP server exited; inspect state/http-server.log')
                    try:
                        if client.get('/api/llm/config').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.05)
                else:
                    raise TimeoutError('Server startup timeout')
                yield client
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


def measured(client, path, *, params=None):
    started = time.perf_counter()
    response = client.get(path, params=params)
    response.raise_for_status()
    payload = response.json()  # Includes transfer and JSON decode, no DOM rendering.
    return time.perf_counter() - started, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True)
    parser.add_argument('--state', type=Path, default=ROOT / 'data/mart-benchmark')
    parser.add_argument('--dataset', type=Path, default=ROOT / 'data/RUN-20260827-0001')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    state, dataset = args.state.resolve(), args.dataset.resolve()
    state.mkdir(parents=True, exist_ok=True)
    credential_file = state / 'benchmark-session.json'
    preparation = None
    if not credential_file.exists():
        with server(state, dataset) as client:
            started = time.perf_counter()
            response = client.post('/api/runs', json=dict(name='Full organizer benchmark', dataset=str(dataset), llm_opinion=False))
            response.raise_for_status()
            rid = response.json()['run']['id']
            for _ in range(1200):
                run = client.get('/api/runs/' + rid).json()['run']
                if run['status'] in ('done', 'error'):
                    break
                time.sleep(.1)
            assert run['status'] == 'done', run['error']
            assert run['summary']['total_apps'] == 2002
            assert run['summary']['apps_with_diffs'] == 502
            assert run['summary']['apps_decision_changed'] == 385
            assert not run['errors'], run['errors']
            preparation = round(time.perf_counter() - started, 6)
            credential_file.write_text(json.dumps(dict(run_id=rid, cookies=dict(client.cookies))), encoding='utf8')
            credential_file.chmod(0o600)
    session = json.loads(credential_file.read_text())
    rid, timings, samples = session['run_id'], {}, []
    variant = state / 'variants' / (args.phase + '.duckdb')
    if variant.exists():
        # Both A/B files are loaded from identical sorted pipeline rows. Copy only
        # while the benchmark server is stopped; never rewrite a user's run.
        target = state / 'mart' / (rid + '.duckdb')
        if target.with_suffix('.duckdb.wal').exists():
            raise RuntimeError('Unexpected WAL: do not replace an active database')
        shutil.copyfile(variant, target)
    for iteration in range(3):
        with server(state, dataset) as client:
            client.cookies.update(session['cookies'])
            prefix = '/api/runs/' + rid
            first_seconds, first = measured(client, prefix + '/facts')
            assert first['totals']['cnt_apps'] == 2002
            assert first['totals']['cnt_rows'] == 572221
            layout_seconds, layout = measured(client, prefix + '/facts', params={'dims':'prefix,class,suffix,key,source,diff_flag'})
            filter_seconds, filtered = measured(client, prefix + '/facts', params={
                'dims':'path,source','path_prefix':'decisionResult.','change_type':'VALUE_CHANGED'})
            assert layout['rows'] and filtered['rows']
            cell = next(r for r in first['rows'] if r['dims'] == {'path':'decisionResult.decision','change_type':'VALUE_CHANGED'})
            apps_seconds, apps = measured(client, prefix + '/apps', params=cell['dims'])
            assert apps['total'] == cell['cnt_apps'] == 385
            app_id = apps['app_ids'][0]
            detail_seconds, detail = measured(client, prefix + '/apps/' + app_id)
            started = time.perf_counter()
            attribute = [r for r in detail['attributes'] if r['PATH'] == cell['dims']['path'] and r['DIFF_FLAG'] == 1]
            attribute_seconds = time.perf_counter() - started
            assert attribute
            reopen_start = time.perf_counter()
            metadata_seconds, run = measured(client, prefix)
            checks_seconds, checks = measured(client, prefix + '/checks')
            facts_reopen_seconds, reopened = measured(client, prefix + '/facts')
            reopen_seconds = time.perf_counter() - reopen_start
            assert run['run']['status'] == 'done' and len(checks['rows']) == 32
            assert reopened == first
            sample = dict(first_facts=first_seconds, layout=layout_seconds, filter=filter_seconds,
                          cell_to_apps=apps_seconds, application=detail_seconds, cached_attribute=attribute_seconds,
                          drill_total=apps_seconds + detail_seconds + attribute_seconds, report_reopen=reopen_seconds,
                          run_metadata_reopen=metadata_seconds, checks_reopen=checks_seconds, facts_reopen=facts_reopen_seconds)
            samples.append(sample)
            print(json.dumps(dict(phase=args.phase,iteration=iteration+1,seconds=sample)),flush=True)
    for key in samples[0]:
        values = [sample[key] for sample in samples]
        median = statistics.median(values)
        timings[key] = dict(seconds=values, median_seconds=median,
                            band='excellent' if median <= .3 else 'good' if median <= 1 else 'minimum' if median <= 5 else 'over_limit')
    import duckdb
    result = dict(phase=args.phase, run_id=rid, base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  mart_store_sha256=hashlib.sha256((ROOT/'mart/store.py').read_bytes()).hexdigest(), duckdb=duckdb.__version__,
                  machine=dict(system=platform.platform(),architecture=platform.machine(),python=platform.python_version()),
                  preparation_seconds=preparation, metrics=timings,
                  database_variant=variant.name if variant.exists() else None,
                  methodology='3 fresh uvicorn processes on same completed 2002-app run, OS cache retained; localhost HTTP through JSON decode; no browser rendering',
                  scope=dict(total_apps=2002,changed_apps=502,decision_changed_apps=385,vertical_rows=572221,check_rows=32))
    output = args.output or ROOT / 'mart/benchmarks' / (args.phase + '-http.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('Saved:',output,flush=True)


if __name__ == '__main__':
    main()
