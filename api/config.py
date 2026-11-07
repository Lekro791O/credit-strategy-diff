"""Пути и настройки приложения.

Хранение — в data/ (не в git): workspace-код пользователей, состояния прогонов,
sqlite-витрины. Настройки LLM — через переменные окружения (см. README).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CSD_DATA_DIR", REPO_ROOT / "data"))
WORKSPACE_DIR = DATA_DIR / "workspace"
RUNS_DIR = DATA_DIR / "runs"
MART_DIR = DATA_DIR / "mart"
TASK_DIR = REPO_ROOT / "task"
BUILTIN_CHECKS_DIR = TASK_DIR / "checks"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Датасет по умолчанию: сюда кладётся RUN-20260827-0001 от организаторов
DEFAULT_DATASET_DIR = DATA_DIR

DEMO_DATASET = "demo://synthetic"


def ensure_dirs() -> None:
    for d in (DATA_DIR, WORKSPACE_DIR, RUNS_DIR, MART_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- LLM (опционально, см. README) ---

def _load_env_file() -> None:
    """Локальные настройки (не в Git): data/llm.env, строки KEY=VALUE."""
    path = DATA_DIR / 'llm.env'
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    except OSError:
        pass


_load_env_file()


def llm_settings() -> dict:
    import json
    try:
        saved = json.loads((DATA_DIR / "gigachat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if saved.get("key"):
        return {"provider": "gigachat", "api_key": saved["key"], "model": saved.get("model", "GigaChat"), "scope": saved.get("scope", "GIGACHAT_API_PERS"), "base_url": "https://api.giga.chat/v1"}
    return {
        "base_url": os.environ.get("CSD_LLM_BASE_URL", ""),
        "api_key": os.environ.get("CSD_LLM_API_KEY", ""),
        "model": os.environ.get("CSD_LLM_MODEL", ""),
    }


def llm_configured() -> bool:
    s = llm_settings()
    return bool(s["base_url"] and s["api_key"] and s["model"])
