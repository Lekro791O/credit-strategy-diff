"""Explicit, replaceable parsing policies. Paths never contain array indices."""
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class ArrayRule:
    mode: str  # field | value | index
    keys: tuple[str, ...] = ()
    element_name: str = ''  # optional virtual path component for legacy examples
    scalar_key: str | None = None


def identity_bucket(path, value):
    return value


@dataclass
class Config:
    arrays: dict[str, ArrayRule] = field(default_factory=dict)
    exclude: tuple[str, ...] = ('appData.request_id', 'appData.createDateTime')
    aliases: dict[str, str] = field(default_factory=dict)
    bucket: Callable[[str, str], str] = identity_bucket


def default_config():
    return Config(arrays={
        'decisionResult.available_offers': ArrayRule('field', ('product_id', 'type')),
        'decisionResult.product_trials': ArrayRule('field', ('product_id',)),
        'decisionResult.product_trials.rules': ArrayRule('field', ('name',)),
        'decisionResult.product_trials.scoring.scoreModels': ArrayRule('field', ('modelName',)),
        'decisionResult.scoring': ArrayRule('field', ('modelName',)),
        'decisionResult.scoring.value': ArrayRule('index'),
        'ext_data': ArrayRule('field', ('body.action',)),
        'product_catalog': ArrayRule('field', ('mfo_id', 'product_id')),
        'tariff_matrix': ArrayRule('field', ('mfo_id', 'product_id')),
    })


def example_bucket(path, value):
    if not (path.endswith('.value') or path.endswith('.limit')):
        return value
    try:
        n = float(value)
    except ValueError:
        return value
    if path.endswith('.limit'):
        for lo, hi, label in [(0, 100000, '(0 - 100K]'), (100000, 250000, '(100K - 250K]'),
                              (250000, 1000000, '(250K - 1M]')]:
            if lo < n <= hi:
                return label
    else:
        for lo, hi, label in [(0, .3, '(0 - 0.3]'), (.3, .6, '(0.3 - 0.6]'),
                              (.6, .9, '(0.6 - 0.9]'), (.9, 1, '(0.9 - 1.0]')]:
            if lo < n <= hi:
                return label
    return value


def example_config(legacy=False):
    return Config(
        arrays={
            'company.scoreCardResult': ArrayRule('field', ('name',), 'scoreCard'),
            'company.rejectReasons': ArrayRule('value', element_name='reason',
                                              scalar_key='code' if legacy else ''),
        },
        exclude=('company.decisionResult.calculatedAt',),
        aliases={'company.limitResult': 'Limit'}, bucket=example_bucket,
    )
