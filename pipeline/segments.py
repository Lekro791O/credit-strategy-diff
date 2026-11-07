"""One row per application; product-specific details are retained as an extension."""
import json
from .core import scalar


def segment(before, after, *, app_id):
    before, after = before or {}, after or {}
    row = {'APP_ID': app_id}
    trials_by_source = {}
    for source, doc in [('BEFORE', before), ('AFTER', after)]:
        decision = doc.get('decisionResult', {})
        row['DECISION_' + source] = decision.get('decision') or ''
        offers = decision.get('available_offers', [])
        ranked = sorted(offers, key=lambda o: (o.get('rank') is None, o.get('rank', 0),
                                               str(o.get('product_id', '')), str(o.get('type', ''))))
        row['TOP_OFFER_' + source] = str(ranked[0]['product_id']) if ranked else ''
        trials = decision.get('product_trials', [])
        trials_by_source[source] = trials
        # Preserve product associations; sorting makes array permutations irrelevant.
        details = {str(t['product_id']): {
            'PD_MODEL': t.get('scoring', {}).get('pd', {}).get('modelName', ''),
            'CUTOFF': t.get('cutOff', {}).get('name', ''),
            'REJECT_REASON': t.get('rejectReason') or '',
            'DECISION': t.get('decision', ''),
            'PARTNER_ID': str(t.get('partner_id', '')),
            'PARTNER_NAME': t.get('partner_name', ''),
            'PARTNER_TYPE': t.get('partner_type', ''),
            'ROUTING_METHOD': t.get('routing_method', ''),
        } for t in trials}
        row['PRODUCT_DETAILS_' + source] = details
        for field in ('PD_MODEL', 'CUTOFF', 'REJECT_REASON'):
            values = {p: d[field] for p, d in sorted(details.items())}
            distinct = set(values.values())
            row[field + '_' + source] = (next(iter(distinct)) if len(distinct) == 1 else
                                         json.dumps(values, ensure_ascii=False, sort_keys=True) if values else '')
        score = doc.get('characteristics', {}).get('score_okb')
        row['SCORE_OKB_' + source] = scalar(score) if score is not None else ''
    trials = trials_by_source['BEFORE'] + trials_by_source['AFTER']
    row['PRODUCTS'] = ';'.join(sorted({str(t['product_id']) for t in trials}))
    for out, key in [('PARTNER_TYPE', 'partner_type'), ('ROUTING_METHOD', 'routing_method')]:
        row[out] = ';'.join(sorted({str(t[key]) for t in trials if t.get(key) is not None}))
    row['DECISION_CHANGED'] = int(row['DECISION_BEFORE'] != row['DECISION_AFTER'])
    return row
