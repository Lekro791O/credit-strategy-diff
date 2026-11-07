"""JSON worker protocol v1 for a trusted subprocess or participant-2 sandbox."""
import contextlib
import json
import sys
from .settings import config_from_settings


def main():
    job = json.load(sys.stdin)
    if job.get("version") != 1 or job.get("stage") not in ("normalize", "compare", "aggregate"):
        raise ValueError("Unsupported procedure job version/stage")
    with contextlib.redirect_stdout(sys.stderr):
        namespace = {'__name__': 'user_procedure', '__file__': job.get('origin', '<procedure>')}
        exec(compile(job['code'], '<procedure>', 'exec'), namespace)
        procedure = namespace['PROCEDURE']
        if procedure['stage'] != job['stage'] or not callable(procedure['entrypoint']):
            raise ValueError('PROCEDURE stage/entrypoint mismatch')
        fn = procedure['entrypoint']
        if job['stage'] == 'normalize':
            result = fn(job['args'][0], app_id=job['app_id'], source=job['source'],
                        config=config_from_settings(job.get('settings')))
        else:
            result = fn(*job['args'])
        result = list(result)
    json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False)


if __name__ == '__main__':
    main()
