"""JSON-serializable normalization settings for server and web form."""
from math import isfinite
from .config import ArrayRule, Config, default_config
from .core import PipelineError


def config_from_settings(settings=None):
    if settings is None:
        return default_config()
    if not isinstance(settings, dict) or set(settings) - {'version', 'exclude', 'arrays', 'buckets'}:
        raise PipelineError('Invalid normalization settings fields')
    if type(settings.get('version', 1)) is not int or settings.get('version', 1) != 1:
        raise PipelineError('Unsupported normalization settings version')
    config = default_config()
    exclude = settings.get('exclude', list(config.exclude))
    if not isinstance(exclude, list) or any(not isinstance(p, str) or not p for p in exclude):
        raise PipelineError('exclude must be a list of nonempty paths')
    config.exclude = tuple(exclude)
    arrays = settings.get('arrays', {})
    if not isinstance(arrays, dict):
        raise PipelineError('arrays must be a path-to-rule object')
    for path, rule in arrays.items():
        if not isinstance(path, str) or not path or not isinstance(rule, dict) or set(rule) - {'mode', 'keys'}:
            raise PipelineError('Invalid array settings')
        mode, keys = rule.get('mode'), rule.get('keys', [])
        if mode not in ('field', 'value', 'index') or not isinstance(keys, list) or any(not isinstance(k, str) or not k for k in keys):
            raise PipelineError(f'Invalid array mode/keys: path={path}')
        if (mode == 'field') != bool(keys):
            raise PipelineError(f'Only field arrays require keys: path={path}')
        config.arrays[path] = ArrayRule(mode, tuple(keys))
    buckets = settings.get('buckets', {})
    if not isinstance(buckets, dict):
        raise PipelineError('buckets must be a path-to-intervals object')
    for path, rules in buckets.items():
        if not isinstance(path, str) or not path or not isinstance(rules, list):
            raise PipelineError('Invalid bucket settings')
        previous = None
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {'lower', 'upper', 'label'} or not isinstance(rule['label'], str):
                raise PipelineError(f'Invalid bucket interval: path={path}')
            lo, hi = rule['lower'], rule['upper']
            for edge in (lo, hi):
                if edge is not None and (type(edge) not in (int, float) or not isfinite(edge)):
                    raise PipelineError(f'Bucket edges must be finite numbers or null: path={path}')
            low, high = float('-inf') if lo is None else lo, float('inf') if hi is None else hi
            if low >= high or (previous is not None and low < previous):
                raise PipelineError(f'Bucket intervals must be ordered and disjoint: path={path}')
            previous = high
    # Capture independent settings; callers cannot change the policy between slices.
    from copy import deepcopy
    buckets = deepcopy(buckets)
    # ключ «suffix:<хвост>» применяется ко всем путям с таким окончанием
    # (напр. suffix:.value — бакетизация скоринговых значений независимо от пути)
    suffix_rules = [(k[len('suffix:'):], v) for k, v in buckets.items()
                    if isinstance(k, str) and k.startswith('suffix:')]
    def bucket(path, value):
        try:
            number = float(value)
        except ValueError:
            return value
        rules = buckets.get(path, [])
        if not rules:
            rules = next((v for suffix, v in suffix_rules if str(path).endswith(suffix)), [])
        for rule in rules:
            if (rule['lower'] is None or number > rule['lower']) and (rule['upper'] is None or number <= rule['upper']):
                return rule['label']
        return value
    config.bucket = bucket
    return config
