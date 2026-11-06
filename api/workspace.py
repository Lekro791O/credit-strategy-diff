"""Рабочая область пользователя: код процедур и проверок.

Файлы лежат в data/workspace/<user>/<name>.py — пользователь правит их
в редакторе приложения или кладёт готовый .py и выбирает в конфигурации
прогона. Версионирование по ТЗ не требуется.
"""
from __future__ import annotations

from .config import WORKSPACE_DIR


def _user_dir(user: str):
    d = WORKSPACE_DIR / user
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_workspace(user: str) -> list[dict]:
    d = WORKSPACE_DIR / user
    if not d.exists():
        return []
    return [{"name": f.stem, "size": f.stat().st_size}
            for f in sorted(d.glob("*.py"))]


def read_workspace(user: str, name: str) -> str:
    path = _user_dir(user) / f"{name}.py"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_workspace(user: str, name: str, code: str) -> None:
    if not name.replace("_", "").isalnum():
        raise ValueError("имя файла: только буквы, цифры и подчёркивания")
    (_user_dir(user) / f"{name}.py").write_text(code, encoding="utf-8")


def delete_workspace(user: str, name: str) -> None:
    path = _user_dir(user) / f"{name}.py"
    if path.exists():
        path.unlink()
