"""JSON leaves -> normalized rows -> full outer comparison -> count facts."""
from collections import Counter
import json
import math
from urllib.parse import quote

from .config import default_config

DIMENSIONS = ('PREFIX', 'CLASS', 'SUFFIX', 'ARRAY_KEY', 'KEY')
NORMALIZED_FIELDS = ('APP_ID', 'SOURCE', *DIMENSIONS, 'PATH', 'VALUE', 'ADJUSTED_VALUE', 'VALUE_TYPE')
COMPARISON_FIELDS = ('APP_ID', *DIMENSIONS, 'PATH', 'VALUE_BEFORE', 'VALUE_AFTER',
                     'ADJUSTED_BEFORE', 'ADJUSTED_AFTER', 'DIFF_FLAG', 'CHANGE_TYPE')
FACT_FIELDS = (*DIMENSIONS, 'VALUE_ADJUSTED', 'DIFF_FLAG', 'SOURCE', 'CNT')


class PipelineError(ValueError):
    """Invalid input or handler output; messages contain context, never leaf values."""


def scalar(value):
    if value is None:
        return 'NULL'
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise PipelineError('Non-finite JSON number')
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    raise PipelineError('Expected JSON scalar')


def lookup(obj, path):
    for part in path.split('.'):
        if not isinstance(obj, dict) or part not in obj:
            raise PipelineError(f'Missing key field: {path}')
        obj = obj[part]
    return obj


def encode_key(value):
    # Escape delimiters before composing components. Simple organizer IDs stay readable.
    return quote(scalar(value), safe='@_-').replace('~', '%7E')


def row_key(row):
    try:
        return tuple(row[k] for k in ('APP_ID', 'PATH', 'ARRAY_KEY'))
    except KeyError as exc:
        raise PipelineError(f'Missing row column: {exc.args[0]}') from exc


def index_rows(rows):
    result = {}
    for row in rows:
        key = row_key(row)
        if key in result:
            raise PipelineError(f'Duplicate attribute: app={key[0]} path={key[1]}')
        result[key] = row
    return result


def normalize(document, *, app_id, source, config=None):
    """Normalize one JSON object; no implicit array heuristics or technical exclusions."""
    config = config or default_config()
    if source not in ('BEFORE', 'AFTER'):
        raise PipelineError('SOURCE must be BEFORE or AFTER')
    if not isinstance(document, dict) or not isinstance(app_id, str) or not app_id:
        raise PipelineError('Expected object document and nonempty string APP_ID')
    declared = document.get('appData', {}).get('application_id')
    if declared is not None and str(declared) != app_id:
        raise PipelineError(f'APP_ID differs from filename: app={app_id}')
    rows = []

    def visit(value, raw, output, array_keys, scalar_key=None):
        path = '.'.join(raw)
        if any(path == p or path.startswith(p + '.') for p in config.exclude):
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or not key or '.' in key:
                    raise PipelineError(f'Unsupported JSON field name: app={app_id} path={path}')
                child_raw = raw + [key]
                child_output = output + [key]
                alias = config.aliases.get('.'.join(child_raw))
                if alias:
                    child_output += [alias]
                visit(child, child_raw, child_output, array_keys)
        elif isinstance(value, list):
            rule = config.arrays.get(path)
            if rule is None:
                raise PipelineError(f'Array mode required: app={app_id} path={path}')
            if rule.mode not in ('field', 'value', 'index') or (rule.mode == 'field' and not rule.keys):
                raise PipelineError(f'Invalid array rule: path={path}')
            seen = set()
            for i, item in enumerate(value):
                if rule.mode == 'index':
                    key = str(i)
                elif rule.mode == 'field':
                    try:
                        parts = [lookup(item, k) for k in rule.keys]
                        if any(p is None or isinstance(p, (list, dict)) for p in parts):
                            raise PipelineError('Key field must be a non-null scalar')
                        key = ':'.join(encode_key(p) for p in parts)
                    except PipelineError as exc:
                        raise PipelineError(f'{exc}: app={app_id} path={path} index={i}') from exc
                else:
                    if isinstance(item, (dict, list)):
                        raise PipelineError(f'Value array requires scalars: app={app_id} path={path}')
                    # Distinguish JSON types (true vs "true", null vs "null").
                    key = encode_key(item)
                    if not isinstance(item, str):
                        key = '~' + key
                if key in seen:
                    if rule.mode == 'value':  # value mode has set semantics
                        continue
                    raise PipelineError(f'Duplicate array key: app={app_id} path={path} index={i}')
                seen.add(key)
                dest = output + ([rule.element_name] if rule.element_name else [])
                visit(item, raw, dest, array_keys + [key], rule.scalar_key)
        else:
            val = scalar(value)
            # Scalars in arrays have no field name unless a compatibility profile supplies it.
            key = scalar_key if scalar_key is not None else (output[-1] if output else '')
            components = output if scalar_key is not None else output[:-1]
            if scalar_key:
                leaf_path = '.'.join(output + [scalar_key])
            else:
                leaf_path = '.'.join(output)
            row = dict(APP_ID=app_id, SOURCE=source,
                       PREFIX=output[0] if output else '',
                       CLASS=output[1] if len(output) > 1 else '',
                       SUFFIX='.'.join(components[2:]), ARRAY_KEY='|'.join(array_keys),
                       KEY=key, PATH=leaf_path, VALUE=val,
                       VALUE_TYPE=('null' if value is None else 'boolean' if isinstance(value, bool)
                                   else 'string' if isinstance(value, str) else 'number'),
                       ADJUSTED_VALUE=config.bucket(leaf_path, val))
            if not isinstance(row['ADJUSTED_VALUE'], str):
                raise PipelineError(f'Bucket must return string: path={leaf_path}')
            rows.append(row)

    visit(document, [], [], [])
    index_rows(rows)
    return rows


def compare(before, after):
    b, a = index_rows(before), index_rows(after)
    result = []
    for key in sorted(b.keys() | a.keys()):
        left, right = b.get(key), a.get(key)
        template = left if left is not None else right
        if left is None:
            change = 'ATTRIBUTE_NEW'
        elif right is None:
            change = 'ATTRIBUTE_LOST'
        elif (left['VALUE'] == right['VALUE'] and
              left.get('VALUE_TYPE') == right.get('VALUE_TYPE')):
            change = 'SAME'
        else:
            change = 'VALUE_CHANGED'
        row = {k: template[k] for k in ('APP_ID', *DIMENSIONS, 'PATH')}
        row.update(VALUE_BEFORE=left['VALUE'] if left is not None else '',
                   VALUE_AFTER=right['VALUE'] if right is not None else '',
                   ADJUSTED_BEFORE=left['ADJUSTED_VALUE'] if left is not None else '',
                   ADJUSTED_AFTER=right['ADJUSTED_VALUE'] if right is not None else '',
                   DIFF_FLAG=int(change != 'SAME'), CHANGE_TYPE=change)
        result.append(row)
    return result


def fact_members(rows):
    """Yield only present sides. Retain primary key for aggregate -> app -> leaf."""
    for row in rows:
        for source, absent in [('BEFORE', 'ATTRIBUTE_NEW'), ('AFTER', 'ATTRIBUTE_LOST')]:
            if row['CHANGE_TYPE'] == absent:
                continue
            yield {**{k: row[k] for k in ('APP_ID', 'PATH', *DIMENSIONS, 'DIFF_FLAG')},
                   'SOURCE': source, 'VALUE_ADJUSTED': row['ADJUSTED_' + source]}


def aggregate(rows):
    """Counts of attributes per exact organizer fact grain (not global app count)."""
    fields = FACT_FIELDS[:-1]
    counts = Counter(tuple(m[k] for k in fields) for m in fact_members(rows))
    return [dict(zip(fields, key), CNT=count) for key, count in sorted(counts.items())]


def drill_down(rows, **filters):
    allowed = set(FACT_FIELDS) - {'CNT'}
    if set(filters) - allowed:
        raise PipelineError('Unsupported fact drill-down filter')
    return [m for m in fact_members(rows) if all(m[k] == v for k, v in filters.items())]
