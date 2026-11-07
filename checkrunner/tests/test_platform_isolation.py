"""No implicit local exec; preserve the Windows kill fallback on macOS too."""
import pytest

from checkrunner import CheckRunner, CheckSpec
from mart import MartStore
from mart.demo import educational_rows, segment


def test_default_docker_refusal_does_not_execute_user_code(tmp_path, monkeypatch):
    from api.procedures import user_runner
    monkeypatch.delenv('CSD_TRUST_LOCAL_CODE', raising=False)
    monkeypatch.delenv('CSD_TRUST_LOCAL_PROCEDURES', raising=False)
    assert CheckRunner().backend == user_runner().backend == user_runner(procedure=True).backend == 'docker'
    store = MartStore(tmp_path / 'run.duckdb')
    store.save_run('run', educational_rows(), [segment()])
    marker = tmp_path / 'untrusted-was-executed'
    code = f'from pathlib import Path\nPath({str(marker)!r}).write_text("unsafe")\n'
    monkeypatch.setattr('checkrunner.runner.shutil.which', lambda _: None)
    def forbidden_process(*args, **kwargs):
        pytest.fail('missing Docker must never invoke subprocess/Python')
    monkeypatch.setattr('checkrunner.runner.subprocess.Popen', forbidden_process)
    report = CheckRunner().run(store, 'run', [CheckSpec(code=code)])
    execution = report['executions'][0]
    assert execution['status'] == 'ERROR'
    assert 'untrusted code was not executed' in execution['diagnostic']['message']
    assert not marker.exists()
    assert store.get_check_report('run')['status'] == 'COMPLETED_WITH_ERRORS'


def test_windows_proc_kill_fallback_survives_timeout(tmp_path, monkeypatch):
    import checkrunner.runner as module
    store = MartStore(tmp_path / 'run.duckdb')
    store.save_run('run', educational_rows(), [segment()])
    monkeypatch.delattr(module.os, 'killpg', raising=False)
    original = module.subprocess.Popen
    killed = []
    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        kill = process.kill
        def call():
            killed.append(process.pid)
            return kill()
        process.kill = call
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', tracked)
    report = CheckRunner(backend='subprocess', trusted=True, timeout=.3).run(
        store, 'run', [CheckSpec(code='while True: pass')])
    assert report['executions'][0]['status'] == 'TIMEOUT'
    assert killed


def test_new_jury_file_uses_full_martapi_in_real_worker(tmp_path):
    store = MartStore(tmp_path / 'run.duckdb')
    store.save_run('run', educational_rows(), [segment()])
    path = tmp_path / 'jury_check.py'
    path.write_text('''
def run(mart, params):
    assert mart.total_apps() == 1
    assert [s['APP_ID'] for s in mart.segments()] == ['1001']
    for filters, expected in params['cases']:
        rows = list(mart.rows(**filters))
        assert mart.count(**filters) == len(rows) == expected
        assert mart.app_ids(**filters) == sorted({r['APP_ID'] for r in rows})
    return [dict(LABEL='Jury contract', SOURCE='AFTER', ARGUMENT='',
        CNT=1, TOTAL=1, METRIC=1.0, APP_IDS=['1001'], RESULT='PASS', CONFIDENCE='HIGH')]
CHECK = dict(id='jury_contract', label='Jury contract', params={}, entrypoint=run)
''', encoding='utf8')
    cases = [({'path': ['company.decisionResult.decision'], 'diff_flag': 1,
               'change_type': ['VALUE_CHANGED'], 'value_before': 'APPROVE',
               'value_after': 'VERIFICATION', 'key': 'decision', 'array_key': '',
               'app_ids': ['1001']}, 1),
             ({'path_prefix': '%'}, 0), ({'app_ids': []}, 0),
             ({'key': 'value', 'array_key': 'TAKEUP_RATE_IP'}, 1)]
    report = CheckRunner(backend='subprocess', trusted=True).run(
        store, 'run', [CheckSpec(path=path, params={'cases': cases})])
    assert report['status'] == 'COMPLETED', report['executions']
    assert report['rows'][0]['RESULT'] == 'PASS'
