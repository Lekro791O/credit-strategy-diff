"""Run worker with user stdout redirected, emit JSON on container stdout."""
import os
import json
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text())
    module = 'checkrunner.procedure_worker' if request.get('worker_kind') == 'procedure' else 'checkrunner.worker'
    result = subprocess.run([sys.executable, '-m', module, *sys.argv[1:]], stdout=sys.stderr)
    if result.returncode:
        sys.exit(result.returncode)
    path = Path(sys.argv[2])
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Result exceeds 8 MiB')
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            os.write(sys.stdout.fileno(), chunk)
