"""Импорт датасета из zip-архива, загруженного через веб-интерфейс.

Поддерживаемые layout'ы (как у выданного датасета и samples):
    <корень>/RESULTS/BEFORE_MASTER/*.json + <корень>/RESULTS/AFTER_BETA/*.json
    <корень>/BEFORE/*.json + <корень>/AFTER/*.json

Архив распаковывается в data/RUN-upload-<имя>-<метка>/ — его сразу видно
в списке датасетов и его можно выбрать для прогона.
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

from . import config

MAX_ARCHIVE_BYTES = 300 * 1024 * 1024     # сжатый архив
MAX_TOTAL_BYTES = 800 * 1024 * 1024       # сумма после распаковки
MAX_FILES = 50000

LAYOUTS = (("RESULTS/BEFORE_MASTER", "RESULTS/AFTER_BETA"), ("BEFORE", "AFTER"))


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip()).strip("-").lower()
    return (slug or "dataset")[:40]


def import_zip(data: bytes, name: str = "") -> dict:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("файл не является zip-архивом")

    entries: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        normalized = info.filename.replace("\\", "/").lstrip("/")
        if ".." in normalized.split("/"):
            raise ValueError(f"небезопасный путь в архиве: {info.filename}")
        if not normalized.endswith(".json"):
            continue  # документация и прочее не нужны
        total += info.file_size
        if total > MAX_TOTAL_BYTES or len(entries) >= MAX_FILES:
            raise ValueError("архив слишком большой после распаковки")
        entries[normalized] = archive.read(info)

    if not entries:
        raise ValueError("в архиве нет JSON-файлов с ответами")

    # ищем поддерживаемый layout (возможна обёртка из одной корневой папки)
    roots = {""}
    for path in entries:
        parts = path.split("/")
        if len(parts) > 2:
            roots.add(parts[0] + "/")
    before_dir = after_dir = root_used = None
    for root in sorted(roots, key=len):
        for before_name, after_name in LAYOUTS:
            b = root + before_name + "/"
            a = root + after_name + "/"
            if any(p.startswith(b) for p in entries) and any(p.startswith(a) for p in entries):
                before_dir, after_dir, root_used = b, a, root
                break
        if before_dir:
            break
    if not before_dir:
        raise ValueError("не найдена структура датасета: ожидаются папки "
                         "RESULTS/BEFORE_MASTER + RESULTS/AFTER_BETA или BEFORE + AFTER")

    before_files = sorted(p for p in entries if p.startswith(before_dir))
    after_files = sorted(p for p in entries if p.startswith(after_dir))
    # валидация JSON до записи на диск
    for path in before_files + after_files:
        try:
            json.loads(entries[path])
        except ValueError:
            raise ValueError(f"не JSON или битый файл: {path}")

    target = config.DATA_DIR / f"RUN-upload-{_slug(name)}-{time.strftime('%H%M%S')}"
    (target / before_dir.replace(root_used, "")).mkdir(parents=True, exist_ok=True)
    (target / after_dir.replace(root_used, "")).mkdir(parents=True, exist_ok=True)
    for path in before_files + after_files:
        rel = path.replace(root_used, "")
        (target / rel).write_bytes(entries[path])

    return {"id": str(target), "apps": len(before_files),
            "requires_pipeline": True,
            "after_apps": len(after_files)}
