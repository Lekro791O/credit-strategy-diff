"""Procedure v1 executed inside the same bounded process/container as CHECK."""
import json
from pathlib import Path
import sys
import traceback


def main():
    request_path, result_path = sys.argv[1:]
    try:
        job = json.loads(Path(request_path).read_text(encoding='utf8'))
        if job.get('version') != 1 or job['stage'] not in ('normalize', 'compare', 'aggregate'):
            raise ValueError('Unsupported procedure protocol/stage')
        ns = {'__name__': 'user_procedure', '__file__': job['origin']}
        exec(compile(job['code'], job['origin'], 'exec'), ns)
        procedure = ns.get('PROCEDURE')
        if not isinstance(procedure, dict) or procedure.get('stage') != job['stage'] or not callable(procedure.get('entrypoint')):
            raise ValueError('PROCEDURE requires matching stage and callable entrypoint')
        kwargs = {}
        if job['stage'] == 'normalize':
            from pipeline.settings import config_from_settings
            kwargs = dict(app_id=job['app_id'], source=job['source'], config=config_from_settings(job.get('settings')))
        rows = list(procedure['entrypoint'](*job['args'], **kwargs))
        result = dict(ok=True, rows=rows)
        json.dumps(result, allow_nan=False)
    except BaseException as exc:
        result = dict(ok=False, error_type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()[-16000:])
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf8')


if __name__ == '__main__':
    main()
