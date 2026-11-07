"""Resolver for the unchanged participant-1 PROCEDURE protocol v1."""
import os
from checkrunner import CheckRunner
from pipeline import PipelineError
from . import workspace


def user_runner(*, procedure=False):
    trusted = os.environ.get('CSD_TRUST_LOCAL_CODE') == '1' or (
        procedure and os.environ.get('CSD_TRUST_LOCAL_PROCEDURES') == '1')
    return CheckRunner(backend='subprocess' if trusted else 'docker', trusted=trusted,
                       timeout=float(os.environ.get('CSD_CODE_TIMEOUT', '60')))


def resolve_procedure(spec, stage, default, *, user, settings=None):
    if not spec or (spec.get('kind', 'builtin') == 'builtin' and spec.get('name', 'default') == 'default'):
        return default
    code, origin = workspace.source_code(user, spec)
    runner = user_runner(procedure=True)
    if runner.backend == 'docker':
        import shutil
        if not shutil.which('docker'):
            raise PipelineError('Docker sandbox unavailable; custom procedure was not executed. Configure Docker or CSD_TRUST_LOCAL_CODE=1 for trusted local code only.')
    def execute(*args, **kwargs):
        job = dict(worker_kind='procedure', version=1, stage=stage, code=code,
                   origin=origin, args=args, settings=settings,
                   app_id=kwargs.get('app_id'), source=kwargs.get('source'))
        try:
            result, logs = runner._launch(None, job)
            if not result.get('ok'):
                raise PipelineError(f"{stage}: {result.get('error_type')}: {result.get('message')}")
            return result['rows']
        except (TimeoutError, ValueError, RuntimeError) as exc:
            raise PipelineError(f'Procedure {stage}: {exc}') from exc
    execute.code_snapshot, execute.origin = code, origin
    return execute
