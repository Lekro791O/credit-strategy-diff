"""Public pipeline API, contract version 1. Python 3.10+, standard library only."""
from .config import ArrayRule, Config, default_config, example_config
from .core import (PipelineError, normalize, compare, aggregate, drill_down, fact_members,
                   NORMALIZED_FIELDS, COMPARISON_FIELDS, FACT_FIELDS)
from .segments import segment
from .runner import Handlers, load_handler, run_pair, run_directories

__all__ = ['ArrayRule', 'Config', 'default_config', 'example_config', 'PipelineError',
           'normalize', 'compare', 'aggregate', 'drill_down', 'fact_members', 'segment',
           'Handlers', 'load_handler', 'run_pair', 'run_directories',
           'NORMALIZED_FIELDS', 'COMPARISON_FIELDS', 'FACT_FIELDS']
