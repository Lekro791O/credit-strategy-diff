# -*- coding: utf-8 -*-
"""Сменилась целевая модель, по которой принимается решение по продукту.

Целевую модель выбирает сама стратегия, поэтому её смена — не техническая
деталь, а изменение логики принятия решения. Отдельно выделяются заявки,
где вместе с моделью изменился и применённый порог.
"""

DEFAULTS = {"low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    pairs, with_cutoff = {}, {}
    for s in mart.segments():
        mb, ma = s.get("PD_MODEL_BEFORE"), s.get("PD_MODEL_AFTER")
        if mb == ma:
            continue
        key = f"{mb} -> {ma}"
        pairs.setdefault(key, set()).add(s["APP_ID"])
        if s.get("CUTOFF_BEFORE") != s.get("CUTOFF_AFTER"):
            with_cutoff.setdefault(key, set()).add(s["APP_ID"])

    rows = []
    for key, apps in sorted(pairs.items()):
        cut = len(with_cutoff.get(key, ()))
        rows.append({
            "LABEL": CHECK["label"],
            "SOURCE": "AFTER",
            "ARGUMENT": key,
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


CHECK = {"id": "target_model_changed",
         "label": "Сменилась целевая модель решения",
         "params": DEFAULTS,
         "entrypoint": run}
