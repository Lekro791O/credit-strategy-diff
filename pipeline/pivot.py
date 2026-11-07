"""Vertical fact queries with exact unique-application totals and memberships."""
import json
from .core import DIMENSIONS, PipelineError, fact_members, index_rows

SOURCE_LABELS = {'BEFORE': 'BEFORE (MASTER)', 'AFTER': 'AFTER (BETA)'}
FACT_DIMS = (*DIMENSIONS, 'PATH', 'CHANGE_TYPE', 'VALUE_ADJUSTED', 'DIFF_FLAG', 'SOURCE')
SEGMENT_DIMS = ('DECISION_BEFORE', 'DECISION_AFTER', 'DECISION_CHANGED', 'PARTNER_TYPE',
                'ROUTING_METHOD', 'PD_MODEL_BEFORE', 'PD_MODEL_AFTER', 'CUTOFF_BEFORE', 'CUTOFF_AFTER',
                'REJECT_REASON_BEFORE', 'REJECT_REASON_AFTER', 'SCORE_OKB_BEFORE', 'SCORE_OKB_AFTER')
SEGMENT_FILTERS = (*SEGMENT_DIMS, 'product', 'partner_id', 'partner_name', 'partner_type',
                   'routing_method', 'pd_model_before', 'pd_model_after', 'cutoff_before', 'cutoff_after')


def _matches(value, expected):
    options = expected if isinstance(expected, list) else [expected]
    return value in options


def segment_matches(segment, filters):
    """Product filters preserve product associations; all product predicates use one product."""
    aliases = {'partner_id': 'PARTNER_ID', 'partner_name': 'PARTNER_NAME',
               'partner_type': 'PARTNER_TYPE', 'routing_method': 'ROUTING_METHOD',
               'pd_model_before': 'PD_MODEL_BEFORE', 'pd_model_after': 'PD_MODEL_AFTER',
               'cutoff_before': 'CUTOFF_BEFORE', 'cutoff_after': 'CUTOFF_AFTER'}
    if set(filters)-set(SEGMENT_FILTERS):
        raise PipelineError('Unknown segment filter')
    for key, expected in filters.items():
        if key in aliases or key == 'product':
            continue
        value = segment.get(key, '')
        # Multivalued mandatory fields keep canonical JSON product maps.
        if isinstance(value, str) and value.startswith('{'):
            values = list(json.loads(value).values())
        else:
            values = value.split(';') if isinstance(value, str) else [value]
        if not any(_matches(v, expected) for v in values):
            return False
    product_filters = {k: v for k, v in filters.items() if k in aliases or k == 'product'}
    if not product_filters:
        return True
    b, a = segment.get('PRODUCT_DETAILS_BEFORE', {}), segment.get('PRODUCT_DETAILS_AFTER', {})
    products = set(b) | set(a) | set(filter(None, segment.get('PRODUCTS', '').split(';')))
    for product in products:
        if 'product' in filters and not _matches(product, filters['product']):
            continue
        matched = True
        for key, expected in product_filters.items():
            if key == 'product':
                continue
            field = aliases[key]
            if field.endswith('_BEFORE') or field.endswith('_AFTER'):
                name, source = field.rsplit('_', 1)
                values = [(b if source=='BEFORE' else a).get(product, {}).get(name, '')]
            else:
                values = [d.get(product, {}).get(field, '') for d in (b, a)]
            if not any(_matches(v, expected) for v in values):
                matched = False; break
        if matched:
            return True
    return False


def query_facts(comparison, segments, *, dimensions=None, filters=None, segment_filters=None):
    """Return counts, correct DISTINCT totals, and exact leaf membership.

    CNT = unique APP_ID per group; FACT_COUNT = present side/attribute rows.
    Totals are recalculated from membership, never summed across groups.
    """
    dimensions = list(dimensions) if dimensions is not None else [*DIMENSIONS, 'VALUE_ADJUSTED', 'DIFF_FLAG', 'SOURCE']
    filters, segment_filters = filters or {}, segment_filters or {}
    allowed = set(FACT_DIMS) | set(SEGMENT_DIMS)
    if not dimensions or len(set(dimensions)) != len(dimensions) or set(dimensions)-allowed:
        raise PipelineError('Unknown, empty or duplicate fact dimensions')
    if set(filters)-set(FACT_DIMS)-{'APP_ID'}:
        raise PipelineError('Unknown fact filter')
    comparison = list(comparison)
    index = index_rows(comparison)
    segment_index = {s['APP_ID']: s for s in segments}
    selected = {app for app, s in segment_index.items() if segment_matches(s, segment_filters)}
    groups, memberships, all_apps = {}, [], set()
    for member in fact_members(comparison):
        app = member['APP_ID']
        if app not in selected:
            continue
        member['SOURCE'] = SOURCE_LABELS[member['SOURCE']]
        member['CHANGE_TYPE'] = index[app, member['PATH'], member['ARRAY_KEY']]['CHANGE_TYPE']
        if not all(_matches(member[k], v) for k, v in filters.items()):
            continue
        full = {**segment_index[app], **member}
        key = tuple(full[d] for d in dimensions)
        entry = groups.setdefault(key, {'apps': set(), 'facts': 0})
        entry['apps'].add(app); entry['facts'] += 1
        all_apps.add(app)
        memberships.append(member)
    rows = [dict(zip(dimensions, key), CNT=len(value['apps']), FACT_COUNT=value['facts'])
            for key, value in sorted(groups.items(), key=lambda item: tuple(str(v) for v in item[0]))]
    return {'rows': rows, 'total_apps': len(all_apps), 'total_facts': len(memberships),
            'members': memberships}
