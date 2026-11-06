"""Strict JSON boundary, independent of any executed user module."""
import math

FIELDS = {'LABEL', 'SOURCE', 'ARGUMENT', 'CNT', 'TOTAL', 'METRIC', 'APP_IDS', 'RESULT', 'CONFIDENCE'}


def validate_rows(rows, app_ids_limit=20):
    if type(app_ids_limit) is not int or app_ids_limit < 0:
        raise ValueError('app_ids_limit must be a non-negative integer')
    if not isinstance(rows, list):
        raise ValueError('Check must return list[dict]')
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError(f'Row {i}: expected exactly {sorted(FIELDS)}')
        for key in ['LABEL', 'ARGUMENT', 'SOURCE', 'RESULT', 'CONFIDENCE']:
            if not isinstance(row[key], str):
                raise ValueError(f'Row {i}: {key} must be a string')
        for key, choices in [('SOURCE', ('BEFORE', 'AFTER')), ('RESULT', ('PASS', 'FAIL')), ('CONFIDENCE', ('HIGH', 'LOW'))]:
            if row[key] not in choices:
                raise ValueError(f'Row {i}: invalid {key}: {row[key]}')
        for key in ['CNT', 'TOTAL']:
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError(f'Row {i}: {key} must be a non-negative integer')
        if row['CNT'] > row['TOTAL']:
            raise ValueError(f'Row {i}: CNT exceeds TOTAL')
        if type(row['METRIC']) not in (int, float) or not math.isfinite(row['METRIC']):
            raise ValueError(f'Row {i}: METRIC must be finite numeric')
        ids = row['APP_IDS']
        if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids):
            raise ValueError(f'Row {i}: APP_IDS must be list[str]')
        if len(ids) > app_ids_limit or len(ids) > row['CNT'] or len(ids) != len(set(ids)):
            raise ValueError(f'Row {i}: APP_IDS exceed limit/CNT or contain duplicates')
    return rows
