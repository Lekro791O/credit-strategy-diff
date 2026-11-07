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


def build_dataset(dataset_dir, normalize_cfg=None, compare_cfg=None, facts_cfg=None, *,
                  event=None, resolver=None, expected_app_ids=None):
    """Rich run result: eligible pairs, vertical counts, diagnostics, quality.

    resolver(spec, stage, default) can route user code to an isolated executor.
    Without a resolver, only explicit trusted Python module/callable handlers work.
    """
    from .settings import config_from_settings
    from .dataset import run_dataset
    defaults = Handlers()
    def resolve(spec, stage):
        default = getattr(defaults, stage)
        return resolver(spec, stage, default) if resolver else _procedure(spec, default)
    settings = normalize_cfg.get('settings') if isinstance(normalize_cfg, dict) else None
    config = normalize_cfg.get('config') if isinstance(normalize_cfg, dict) else None
    if config is not None and not isinstance(config, Config):
        raise PipelineError('normalize_cfg.config must be a pipeline.Config instance')
    return run_dataset(dataset_dir, config=config or config_from_settings(settings),
                       handlers=Handlers(normalize=resolve(normalize_cfg, 'normalize'),
                                         compare=resolve(compare_cfg, 'compare'),
                                         aggregate=resolve(facts_cfg, 'aggregate')),
                       event=event, expected_app_ids=expected_app_ids)


def build_mart(dataset_dir, normalize_cfg=None, compare_cfg=None):
    """Backward compatible tuple; uses only paired successfully processed replies.

    For exclusions/counters use build_dataset; do not infer ingestion quality from
    the two lists returned by this legacy adapter.
    """
    result = build_dataset(dataset_dir, normalize_cfg, compare_cfg)
    return result['comparison'], result['segments']
