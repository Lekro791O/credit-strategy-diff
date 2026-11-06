# -*- coding: utf-8 -*-
"""Изменился состав атрибутов ответа: появились новые или пропали существующие.

Считается по PATH, а не по конкретной заявке: важно, что атрибут появился
или исчез как таковой, и у какой доли заявок это произошло.
"""

DEFAULTS = {"min_share": 0.001, "low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    total = mart.total_apps()
    agg = {}
    for change in ("ATTRIBUTE_NEW", "ATTRIBUTE_LOST"):
        for r in mart.rows(change_type=change):
            agg.setdefault((change, r["PATH"]), set()).add(r["APP_ID"])

    rows = []
    for (change, path), apps in sorted(agg.items()):
        share = len(apps) / total if total else 0.0
        if share < p["min_share"]:
            continue
        rows.append({
            "LABEL": CHECK["label"],
            "SOURCE": "AFTER" if change == "ATTRIBUTE_NEW" else "BEFORE",
            "ARGUMENT": path,
            "CNT": len(apps),
            "TOTAL": total,
            "METRIC": round(share, 4),
            "APP_IDS": sorted(apps)[:p["app_ids_limit"]],
            "RESULT": "FAIL",
            "CONFIDENCE": "LOW" if len(apps) < p["low_confidence_cnt"] else "HIGH",
        })
    if not rows:
        rows.append({"LABEL": CHECK["label"], "SOURCE": "AFTER", "ARGUMENT": "",
                     "CNT": 0, "TOTAL": total, "METRIC": 0.0, "APP_IDS": [],
                     "RESULT": "PASS", "CONFIDENCE": "HIGH"})
    return rows


CHECK = {"id": "attribute_new_loss",
         "label": "Изменился состав атрибутов",
         "params": DEFAULTS,
         "entrypoint": run}
