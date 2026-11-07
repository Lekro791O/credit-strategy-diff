"""Readers see old or new state, never half a JSON file."""
import os
from pathlib import Path
import tempfile
import time


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows: свежий файл может быть на секунду заблокирован антивирусом/
        # индексатором — замена отказом не должна ронять прогон
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_read(path, encoding='utf8'):
    """Чтение с ретраями: антивирус/индексатор Windows может на секунду
    блокировать даже чтение свежезаписанного файла."""
    path = Path(path)
    for attempt in range(8):
        try:
            return path.read_text(encoding=encoding)
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
