# -*- coding: utf-8 -*-
"""Стоп-фактор перестал работать: правило пропало из ответа либо hit true -> false.

Разделяет два случая, потому что в отчёте они читаются по-разному:
пропавший атрибут — это изменение набора правил, смена hit — изменение поведения.
"""

RULE_HIT_PATH = "decisionResult.product_trials.rules.hit"
DEFAULTS = {"low_confidence_cnt": 5, "app_ids_limit": 20}


def _rule_name(array_key):
    # ARRAY_KEY здесь имеет вид "<product_id>|<rule_name>"
    parts = (array_key or "").split("|")
    return parts[-1] if parts else ""


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    lost, turned_off = {}, {}

    for r in mart.rows(path=RULE_HIT_PATH, change_type="ATTRIBUTE_LOST"):
        if r.get("VALUE_BEFORE") == "true":
            lost.setdefault(_rule_name(r["ARRAY_KEY"]), set()).add(r["APP_ID"])

    for r in mart.rows(path=RULE_HIT_PATH, change_type="VALUE_CHANGED"):
        if r.get("VALUE_BEFORE") == "true" and r.get("VALUE_AFTER") in ("false", "NULL"):
            turned_off.setdefault(_rule_name(r["ARRAY_KEY"]), set()).add(r["APP_ID"])

    total = mart.total_apps()
    rows = []
    for title, bucket in (("правило удалено из стратегии", lost),
                          ("правило перестало срабатывать", turned_off)):
        for rule, apps in sorted(bucket.items()):
            rows.append({
                "LABEL": CHECK["label"],
                "SOURCE": "AFTER",
                "ARGUMENT": rule,
                "CNT": len(apps),
                "TOTAL": total,
                "METRIC": round(len(apps) / total, 4) if total else 0.0,
                "APP_IDS": sorted(apps)[:p["app_ids_limit"]],
                "RESULT": "FAIL",
                "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH",
            })
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "",
                     "CNT": 0, "TOTAL": total, "METRIC": 0.0, "APP_IDS": [],
                     "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "stop_factor_disappeared",
         "label": "Стоп-фактор перестал работать",
         "params": DEFAULTS,
         "entrypoint": run}
