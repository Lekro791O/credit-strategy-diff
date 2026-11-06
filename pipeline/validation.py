"""Validate the handoff boundary, including output from user handlers."""
from .core import (COMPARISON_FIELDS, NORMALIZED_FIELDS, FACT_FIELDS, PipelineError,
                   index_rows)

SEGMENT_FIELDS = ('APP_ID', 'DECISION_BEFORE', 'DECISION_AFTER', 'DECISION_CHANGED',
                  'PRODUCTS', 'TOP_OFFER_BEFORE', 'TOP_OFFER_AFTER', 'PARTNER_TYPE',
                  'ROUTING_METHOD', 'PD_MODEL_BEFORE', 'PD_MODEL_AFTER',
                  'CUTOFF_BEFORE', 'CUTOFF_AFTER', 'REJECT_REASON_BEFORE',
                  'REJECT_REASON_AFTER', 'SCORE_OKB_BEFORE', 'SCORE_OKB_AFTER')


def validate(rows, kind, *, app_id=None, source=None):
    fields = {'normalized': NORMALIZED_FIELDS, 'comparison': COMPARISON_FIELDS,
              'facts': FACT_FIELDS, 'segments': SEGMENT_FIELDS}[kind]
    integers = {'DIFF_FLAG', 'CNT', 'DECISION_CHANGED'}
    for row in rows:
        if not isinstance(row, dict) or any(f not in row for f in fields):
            raise PipelineError(f'Invalid {kind} handler output: missing required columns')
        if any(not isinstance(row[f], str) for f in fields if f not in integers):
            raise PipelineError(f'Invalid {kind} handler output: expected string columns')
        for f in integers.intersection(fields):
            if type(row[f]) is not int or row[f] < 0 or (f != 'CNT' and row[f] not in (0, 1)):
                raise PipelineError(f'Invalid {kind} handler output: {f}')
        if app_id is not None and row['APP_ID'] != app_id:
            raise PipelineError(f'Invalid {kind} handler output: APP_ID')
        if source is not None and row['SOURCE'] != source:
            raise PipelineError(f'Invalid {kind} handler output: SOURCE')
        if kind == 'facts' and row['SOURCE'] not in ('BEFORE', 'AFTER'):
            raise PipelineError('Invalid facts handler output: SOURCE')
        if kind == 'comparison':
            if row['CHANGE_TYPE'] not in ('SAME', 'VALUE_CHANGED', 'ATTRIBUTE_NEW', 'ATTRIBUTE_LOST'):
                raise PipelineError('Invalid comparison handler output: CHANGE_TYPE')
            if row['DIFF_FLAG'] != int(row['CHANGE_TYPE'] != 'SAME'):
                raise PipelineError('Invalid comparison handler output: DIFF_FLAG')
    if kind in ('normalized', 'comparison'):
        index_rows(rows)
    return rows
