"""Run worker with user stdout redirected, emit JSON on container stdout."""
import os
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    result = subprocess.run([sys.executable, '-m', 'checkrunner.worker', *sys.argv[1:]], stdout=sys.stderr)
    if result.returncode:
        sys.exit(result.returncode)
    path = Path(sys.argv[2])
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Result exceeds 8 MiB')
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            os.write(sys.stdout.fileno(), chunk)
