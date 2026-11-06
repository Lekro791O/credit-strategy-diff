"""Entry point consumed by api.runs.RunManager for real JSON datasets."""
from pathlib import Path

from .config import Config, default_config
from .core import PipelineError
from .runner import Handlers, load_handler, run_directories


def _procedure(spec, default):
    if spec is None:
        return default
    if callable(spec):
        return spec
    if not isinstance(spec, dict):
        raise PipelineError('Procedure must be callable or a procedure configuration')
    if spec.get('kind', 'builtin') == 'builtin' and spec.get('name', 'default') == 'default':
        return default
    if spec.get('kind') == 'module':
        return load_handler(spec['name'])
    raise PipelineError('Unsupported pipeline procedure source; use builtin/default, module: name, or a Python callable')


def build_mart(dataset_dir, normalize_cfg=None, compare_cfg=None):
    """Return (comparison rows, application segments) using the app's existing call.

    Accept an organizer run directory (RESULTS/BEFORE_MASTER, AFTER_BETA),
    RESULTS itself, or a sample directory containing BEFORE and AFTER.
    Procedure configs accept the app's builtin/default or kind=module/name=module:callable.
    normalize_cfg may additionally contain config=Config for parsing policies.
    """
    root = Path(dataset_dir)
    candidates = [(root / 'RESULTS' / 'BEFORE_MASTER', root / 'RESULTS' / 'AFTER_BETA'),
                  (root / 'BEFORE_MASTER', root / 'AFTER_BETA'),
                  (root / 'BEFORE', root / 'AFTER')]
    pair = next(((b, a) for b, a in candidates if b.is_dir() and a.is_dir()), None)
    if pair is None:
        raise PipelineError('Dataset must contain RESULTS/BEFORE_MASTER + AFTER_BETA or BEFORE + AFTER')
    defaults = Handlers()
    handlers = Handlers(normalize=_procedure(normalize_cfg, defaults.normalize),
                        compare=_procedure(compare_cfg, defaults.compare))
    config = normalize_cfg.get('config') if isinstance(normalize_cfg, dict) else None
    if config is not None and not isinstance(config, Config):
        raise PipelineError('normalize_cfg.config must be a pipeline.Config instance')
    result = run_directories(*pair, config=config or default_config(), handlers=handlers)
    return result['comparison'], result['segments']
