"""Minimal demo identity: signed browser session, never a caller-chosen owner header."""
from contextvars import ContextVar
import hashlib
import hmac
import re
import secrets
import threading
import time
import os
from pathlib import Path
from . import config
from .persistence import atomic_write

current_user = ContextVar('csd_user', default=None)
_lock = threading.Lock()
COOKIE = 'csd_session'


def validate_dataset(dataset, owner):
    if dataset == config.DEMO_DATASET:
        return
    path = Path(dataset).resolve()
    private_root = config.WORKSPACE_DIR.resolve()
    own_root = (private_root / owner).resolve()
    if path.is_relative_to(private_root):
        if not path.is_relative_to(own_root):
            raise ValueError('DATASET_DENIED: dataset belongs to another workspace')
        return
    roots = [config.TASK_DIR / 'samples', *config.DATA_DIR.glob('RUN-*')]
    roots.extend(Path(p) for p in os.environ.get('CSD_DATASET_ROOTS', '').split(os.pathsep) if p)
    if not any(path.is_relative_to(root.resolve()) for root in roots if root.is_dir()):
        raise ValueError('DATASET_DENIED: select a registered dataset or a directory in your workspace')


def _key():
    path = config.DATA_DIR / '.session-key'
    with _lock:
        if not path.exists():
            atomic_write(path, secrets.token_hex(32))
        return bytes.fromhex(path.read_text())


def identity(cookie):
    key = _key()
    if cookie:
        try:
            owner, expires, signature = cookie.split('.')
            payload = owner + '.' + expires
            if (re.fullmatch('[a-f0-9]{32}', owner) and int(expires) > time.time()
                    and hmac.compare_digest(signature, hmac.new(key, payload.encode(), hashlib.sha256).hexdigest())):
                return owner, cookie
        except (ValueError, TypeError):
            pass
    owner = secrets.token_hex(16)
    payload = owner + '.' + str(int(time.time()) + 30 * 86400)
    return owner, payload + '.' + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
