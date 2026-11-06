"""Synthetic JSON responses processed by the same pipeline as organizer data.

No comparison rows are assembled by hand: product attributes occur once per
product, nested rules once per rule, and all row keys are checked by pipeline.
"""
from copy import deepcopy
import random
from pipeline import run_pair
from pipeline.config import default_config

RULES = ['AGELOW@21', 'NOINCOME', 'DOCS_EXPIRED', 'BLACKLIST', 'DEBTBURDEN_HIGH']
PRODUCTS = ['CASH_LOAN', 'CL_T60_22.9_PROMO', 'IP_T30_15.5', 'MFO_ZAIMER_2026']
PARTNER_TYPES = ['CURRENT', 'NEW_PARTNER']
ROUTING = ['referal', 'api']


def demo_documents(n_apps=300, seed=None):
    """Yield (APP_ID, BEFORE object, AFTER object), never synthetic mart rows."""
    rng = random.Random(seed)
    for i in range(n_apps):
        app = f'D{i:05d}'
        score = rng.randint(500, 800)
        trials = []
        for product in rng.sample(PRODUCTS, rng.randint(1, 3)):
            trials.append({'product_id': product, 'partner_id': 'PARTNER_'+product,
                           'partner_name': 'Демо '+product, 'partner_type': rng.choice(PARTNER_TYPES),
                           'routing_method': rng.choice(ROUTING),
                           'rules': [{'name': rule, 'hit': rng.random() < .25}
                                     for rule in rng.sample(RULES, rng.randint(1, 3))],
                           'scoring': {'pd': {'modelName': 'PD_RETAIL', 'value': rng.choice([.35, .55, .75])}},
                           'cutOff': {'name': 'CUTOFF_RETAIL', 'value': .5}})
        before = {'appData': {'application_id': app, 'request_id': 'before-'+app},
                  'characteristics': {'score_okb': score, 'score_equ': score+10},
                  'decisionResult': {'product_trials': trials,
                                     'scoring': [{'modelName': 'PD_RETAIL', 'features': {'score_okb': score},
                                                  'value': [.3, .42]}]}}
        after = deepcopy(before)
        after['appData']['request_id'] = 'after-'+app
        if i % 17 == 0:
            after['decisionResult']['scoring'][0]['modelName'] = 'PD_V4'
        after['characteristics']['score_okb'] += rng.choice([-30, -10, 0, 0, 15])
        after['decisionResult']['scoring'][0]['features']['score_okb'] = after['characteristics']['score_okb']
        for trial in after['decisionResult']['product_trials']:
            if i % 17 == 0:
                trial['scoring']['pd']['modelName'] = 'PD_V4'
                trial['cutOff']['name'] = 'CUTOFF_V4'
            if i % 23 == 0:
                trial['cutOff']['value'] = .6
            if i % 7 == 0:
                trial['rules'].pop()
            for rule in trial['rules']:
                if rng.random() < .12:
                    rule['hit'] = not rule['hit']
        # Each product's decision is derived once from its own rules and cutoff.
        for doc in (before, after):
            offers = []
            for trial in doc['decisionResult']['product_trials']:
                hit = any(rule['hit'] is True for rule in trial['rules'])
                trial['decision'] = 'REJECT' if hit or trial['scoring']['pd']['value'] < trial['cutOff']['value'] else 'APPROVE'
                trial['rejectReason'] = 'HARDCHECK' if hit else 'CUTOFF' if trial['decision']=='REJECT' else None
                if trial['decision'] == 'APPROVE':
                    offers.append({'product_id': trial['product_id'], 'type': 'standard',
                                   'rank': len(offers)+1, 'amount': 117325})
            doc['decisionResult']['available_offers'] = offers
            doc['decisionResult']['decision'] = 'APPROVE' if offers else 'REJECT'
        yield app, before, after


def build_demo(n_apps=300, seed=None):
    rows, segments = [], []
    for app, before, after in demo_documents(n_apps, seed):
        result = run_pair(before, after, app_id=app, config=default_config())
        rows.extend(result['comparison']); segments.extend(result['segments'])
    return rows, segments
