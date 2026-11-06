# -*- coding: utf-8 -*-
"""Различие вызвано входными данными, а не стратегией.

Если внешний источник вернул другой скор, решение может измениться
без всякого изменения стратегии. Для аналитика это ложная тревога,
и такие заявки надо отделять от настоящих эффектов изменения.

Дополнительно сверяет, что предиктор модели совпадает со значением,
пришедшим от источника: расхождение означает, что модель считалась
не на тех данных.
"""

SCORE_PATH = "characteristics.score_okb"
FEATURE_PATH = "decisionResult.scoring.features.score_okb"
DEFAULTS = {"low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()

    drifted = {r["APP_ID"] for r in mart.rows(path=SCORE_PATH, diff_flag=1)}
    decision_changed = {s["APP_ID"] for s in mart.segments() if s.get("DECISION_CHANGED")}
    false_alarm = drifted & decision_changed

    mismatch = set()
    feat = {}
    for r in mart.rows(path=FEATURE_PATH):
        feat[r["APP_ID"]] = (r.get("VALUE_BEFORE"), r.get("VALUE_AFTER"))
    for r in mart.rows(path=SCORE_PATH):
        f = feat.get(r["APP_ID"])
        if f and f != (r.get("VALUE_BEFORE"), r.get("VALUE_AFTER")):
            mismatch.add(r["APP_ID"])

    rows = [{
        "LABEL": CHECK["label"],
        "SOURCE": "AFTER",
        "ARGUMENT": SCORE_PATH,
        "CNT": len(false_alarm),
        "TOTAL": total,
        "METRIC": round(len(false_alarm) / total, 4) if total else 0.0,
        "APP_IDS": sorted(false_alarm)[:p["app_ids_limit"]],
        "RESULT": "FAIL" if false_alarm else "PASS",
        "CONFIDENCE": "LOW" if 0 < len(false_alarm) < p["low_confidence_cnt"] else "HIGH",
    }, {
        "LABEL": CHECK["label"],
        "SOURCE": "AFTER",
        "ARGUMENT": "предиктор != источник",
        "CNT": len(mismatch),
        "TOTAL": total,
        "METRIC": round(len(mismatch) / total, 4) if total else 0.0,
        "APP_IDS": sorted(mismatch)[:p["app_ids_limit"]],
        "RESULT": "FAIL" if mismatch else "PASS",
        "CONFIDENCE": "LOW" if 0 < len(mismatch) < p["low_confidence_cnt"] else "HIGH",
    }]
    return rows


CHECK = {"id": "external_score_drift",
         "label": "Различие вызвано входными данными",
         "params": DEFAULTS,
         "entrypoint": run}
