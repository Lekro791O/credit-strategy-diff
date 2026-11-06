"""Framework-independent orchestration with replaceable Python callables."""
from dataclasses import dataclass
import importlib
import json
from pathlib import Path

from .config import default_config
from .core import PipelineError, normalize, compare, aggregate
from .segments import segment
from .validation import validate


@dataclass(frozen=True)
class Handlers:
    normalize: object = normalize
    compare: object = compare
    aggregate: object = aggregate
    segment: object = segment


def load_handler(spec):
    """Load trusted installed Python code via module:callable (no sandbox implied)."""
    try:
        module, name = spec.split(':', 1)
        handler = getattr(importlib.import_module(module), name)
        if not callable(handler):
            raise TypeError('Not callable')
        return handler
    except (ValueError, ImportError, AttributeError, TypeError) as exc:
        raise PipelineError(f'Cannot load handler: {spec}') from exc


def run_pair(before, after, *, app_id, config=None, handlers=None):
    h = handlers or Handlers()
    config = config or default_config()
    b = list(h.normalize(before, app_id=app_id, source='BEFORE', config=config)) if before is not None else []
    a = list(h.normalize(after, app_id=app_id, source='AFTER', config=config)) if after is not None else []
    validate(b, 'normalized', app_id=app_id, source='BEFORE')
    validate(a, 'normalized', app_id=app_id, source='AFTER')
    rows = validate(list(h.compare(b, a)), 'comparison', app_id=app_id)
    segments = validate([h.segment(before, after, app_id=app_id)], 'segments', app_id=app_id)
    return {'normalized_before': b, 'normalized_after': a, 'comparison': rows,
            'segments': segments}


def read_document(path):
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise PipelineError('Duplicate JSON object field')
            obj[key] = value
        return obj
    try:
        with path.open(encoding='utf-8-sig') as file:
            return json.load(file, object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(PipelineError('Non-finite JSON number')))
    except (OSError, UnicodeError, ValueError) as exc:
        # Avoid including JSON source snippets or sensitive values in diagnostics.
        raise PipelineError(f'Cannot read JSON: file={path.name} ({type(exc).__name__})') from exc


def run_directories(before_dir, after_dir, *, config=None, handlers=None, allow_unpaired=False, progress=None):
    """Filename stem is APP_ID. Reject unmatched applications by default."""
    h = handlers or Handlers()
    sides = []
    for directory in (before_dir, after_dir):
        directory = Path(directory)
        if not directory.is_dir():
            raise PipelineError(f'Input directory does not exist: {directory}')
        sides.append({p.stem: p for p in sorted(directory.glob('*.json')) if p.is_file()})
    b, a = sides
    if not b and not a:
        raise PipelineError('No JSON applications found')
    if not allow_unpaired and b.keys() != a.keys():
        raise PipelineError(f'Unpaired applications: BEFORE-only={len(b.keys()-a.keys())}, AFTER-only={len(a.keys()-b.keys())}')
    result = {k: [] for k in ('normalized_before', 'normalized_after', 'comparison', 'segments')}
    ids = sorted(b.keys() | a.keys())
    for i, app_id in enumerate(ids, 1):
        try:
            pair = run_pair(read_document(b[app_id]) if app_id in b else None,
                            read_document(a[app_id]) if app_id in a else None,
                            app_id=app_id, config=config, handlers=h)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(f'Handler failed: app={app_id} ({type(exc).__name__})') from exc
        for key in result:
            result[key].extend(pair[key])
        if progress:
            progress({'stage': 'compare', 'completed': i, 'total': len(ids), 'app_id': app_id})
    try:
        result['facts'] = validate(list(h.aggregate(result['comparison'])), 'facts')
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f'Aggregate handler failed ({type(exc).__name__})') from exc
    return result
