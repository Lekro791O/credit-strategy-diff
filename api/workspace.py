"""User-owned Python source, read as text only; never imported by the server."""
from pathlib import Path
import re
from . import config


def _user_dir(user):
    if not re.fullmatch(r'[\w-]{1,128}', user):
        raise ValueError('Invalid workspace owner')
    d = config.WORKSPACE_DIR / user
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(user, name):
    if not re.fullmatch(r'[\w-]{1,100}', name):
        raise ValueError('Имя файла: буквы, цифры, дефис, подчёркивание')
    root = _user_dir(user).resolve()
    path = (root / (name + '.py')).resolve()
    if not path.is_relative_to(root):
        raise ValueError('File is outside the user workspace')
    return path


def list_workspace(user):
    root = _user_dir(user).resolve()
    return [{'name': f.stem, 'size': f.stat().st_size} for f in sorted(root.glob('*.py'))
            if f.resolve().is_relative_to(root)]


def read_workspace(user, name):
    return _path(user, name).read_text(encoding='utf-8-sig')


def save_workspace(user, name, code):
    if not isinstance(code, str) or len(code.encode()) > 1024 * 1024:
        raise ValueError('Code must be a string of at most 1 MiB')
    from .persistence import atomic_write
    atomic_write(_path(user, name), code)


def delete_workspace(user, name):
    _path(user, name).unlink(missing_ok=True)


def source_code(user, source):
    kind, name = source.get('kind'), source.get('name', '')
    if kind == 'code':
        code = source.get('code')
    elif kind == 'workspace':
        code = read_workspace(user, name)
    elif kind == 'file':
        root = _user_dir(user).resolve()
        path = Path(name)
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root) or path.suffix != '.py':
            raise ValueError('Selected .py file must be inside your workspace')
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('Source exceeds 1 MiB')
        code = path.read_text(encoding='utf-8-sig')
    else:
        raise ValueError('Unsupported source kind')
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > 1024 * 1024:
        raise ValueError('Expected non-empty Python source, at most 1 MiB')
    return code, f'{kind}:{name or "inline"}'
