# -*- coding: utf-8 -*-
"""Approval Rate по продукту: FAIL, если относительное падение больше порога."""

DEFAULTS = {"max_drop_rel": 0.10, "low_confidence_cnt": 5, "app_ids_limit": 20}


def run(mart, params=None):
    p = {**DEFAULTS, **(params or {})}
    by_product = {}
    for s in mart.segments():
        for product in (s.get("PRODUCTS") or "").split(";"):
            if not product:
                continue
            d = by_product.setdefault(product, {"total": 0, "ap_b": 0, "ap_a": 0, "lost": []})
            d["total"] += 1
            if s.get("DECISION_BEFORE") == "APPROVE":
                d["ap_b"] += 1
            if s.get("DECISION_AFTER") == "APPROVE":
                d["ap_a"] += 1
            if s.get("DECISION_BEFORE") == "APPROVE" and s.get("DECISION_AFTER") != "APPROVE":
                d["lost"].append(s["APP_ID"])

    rows = []
    for product, d in sorted(by_product.items()):
        ar_b = d["ap_b"] / d["total"] if d["total"] else 0.0
        ar_a = d["ap_a"] / d["total"] if d["total"] else 0.0
        drop_rel = (ar_b - ar_a) / ar_b if ar_b else 0.0
        failed = drop_rel > p["max_drop_rel"]
        rows.append({
            "LABEL": CHECK["label"],
            "SOURCE": "AFTER",
            "ARGUMENT": product,
            "CNT": len(d["lost"]),
            "TOTAL": d["total"],
            "METRIC": round(drop_rel, 4),
            "APP_IDS": sorted(d["lost"])[:p["app_ids_limit"]],
            "RESULT": "FAIL" if failed else "PASS",
            "CONFIDENCE": "LOW" if len(d["lost"]) < p["low_confidence_cnt"] else "HIGH",
        })
    return rows


CHECK = {"id": "ar_drop_by_product",
         "label": "Approval Rate по продукту упал",
         "params": DEFAULTS,
         "entrypoint": run}
