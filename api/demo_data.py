"""Демо-витрина: синтетические заявки с реальными путями атрибутов.

Нужна, чтобы приложение работало end-to-end до готовности pipeline/
(подписка 1). Пути атрибутов — те, что используют выданные проверки и
документация: decisionResult.product_trials.rules.hit,
decisionResult.scoring.features.score_okb, characteristics.score_okb и т.д.
Числа ничего не значат — это не эталон, а стенд для отладки модулей 3-4.
"""
from __future__ import annotations

import random

RULES = ["AGELOW@21", "NOINCOME", "DOCS_EXPIRED", "BLACKLIST", "DEBTBURDEN_HIGH"]
PRODUCTS = ["CASH_LOAN", "CL_T60_22.9_PROMO", "IP_T30_15.5", "MFO_ZAIMER_2026"]
PARTNER_TYPES = ["CURRENT", "NEW_PARTNER"]
ROUTING = ["referal", "api"]


def _row(app: str, path: str, key: str, before, after, *, prefix="company",
         klass="decisionResult", suffix="", array_key="", bucket=None) -> dict:
    vb = "" if before is None else str(before)
    va = "" if after is None else str(after)
    diff = int(vb != va)
    if vb == "" and va != "":
        ctype = "ATTRIBUTE_NEW"
    elif vb != "" and va == "":
        ctype = "ATTRIBUTE_LOST"
    elif diff:
        ctype = "VALUE_CHANGED"
    else:
        ctype = "SAME"
    return {
        "APP_ID": app, "PREFIX": prefix, "CLASS": klass, "SUFFIX": suffix,
        "ARRAY_KEY": array_key, "KEY": key, "PATH": path,
        "VALUE_BEFORE": vb, "VALUE_AFTER": va,
        "ADJUSTED_BEFORE": bucket(vb) if bucket and vb else vb,
        "ADJUSTED_AFTER": bucket(va) if bucket and va else va,
        "DIFF_FLAG": diff, "CHANGE_TYPE": ctype,
    }


def _bucket_amount(v: str) -> str:
    if not v:
        return v
    x = int(float(v))
    if x <= 50_000:
        return "(0 – 50K]"
    if x <= 100_000:
        return "(50K – 100K]"
    if x <= 250_000:
        return "(100K – 250K]"
    return "(250K+)"


def build_demo(n_apps: int = 300, seed: int = 42) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    rows, segments = [], []
    for i in range(n_apps):
        app = f"D{i:05d}"
        decision_before = rng.choices(["APPROVE", "REJECT"], weights=[55, 45])[0]

        # итоговое решение и его составляющие
        rows.append(_row(app, "decisionResult.decision", "decision",
                         decision_before, None))
        rows[-1]["VALUE_AFTER"] = ""  # заполним ниже
        reject_before = reject_after = ""

        # продукты: 1-3 прогона
        app_products = rng.sample(PRODUCTS, k=rng.randint(1, 3))
        partner_type = rng.choice(PARTNER_TYPES)
        routing = rng.choice(ROUTING)
        pd_before, pd_after = "PD_RETAIL", "PD_RETAIL"
        cutoff_before, cutoff_after = "cutOff PD_RETAIL", "cutOff PD_RETAIL"
        if i % 17 == 0:  # перекат модели у части заявок
            pd_after = "PD_V4"
            cutoff_after = "cutOff PD_V4"
        if i % 23 == 0:  # порог сдвинули
            cutoff_after = "cutOff PD_RETAIL RAISED"

        offers_before, offers_after = [], []
        for rank, prod in enumerate(app_products):
            trial_key = prod
            # стоп-факторы по продукту
            prod_rules = rng.sample(RULES, k=rng.randint(1, 3))
            for rule in prod_rules:
                hit_b = rng.random() < 0.25
                hit_a = hit_b
                r = rng.random()
                if r < 0.08:      # правило пропало из стратегии
                    hit_a = None
                elif r < 0.16:    # перестало срабатывать
                    hit_a = False
                path = "decisionResult.product_trials.rules.hit"
                rows.append(_row(app, path, "hit", hit_b if hit_a is not None else True,
                                 hit_a, klass="decisionResult",
                                 suffix="product_trials.rules",
                                 array_key=f"{trial_key}|{rule}"))
                rows.append(_row(app,
                                 "decisionResult.product_trials.rules.name",
                                 "name", rule, rule if hit_a is not None else None,
                                 suffix="product_trials.rules",
                                 array_key=f"{trial_key}|{rule}"))
                rows.append(_row(app,
                                 "decisionResult.product_trials.decision",
                                 "decision",
                                 "REJECT" if hit_b else "APPROVE",
                                 None if hit_a is None else ("REJECT" if hit_a else "APPROVE"),
                                 suffix="product_trials", array_key=trial_key))
                rows.append(_row(app,
                                 "decisionResult.product_trials.partner_type",
                                 "partner_type", partner_type, partner_type,
                                 suffix="product_trials", array_key=trial_key))
                rows.append(_row(app,
                                 "decisionResult.product_trials.routing_method",
                                 "routing_method", routing, routing,
                                 suffix="product_trials", array_key=trial_key))
                rows.append(_row(app,
                                 "decisionResult.product_trials.pd.modelName",
                                 "modelName", pd_before, pd_after,
                                 suffix="product_trials.pd", array_key=trial_key))
                rows.append(_row(app,
                                 "decisionResult.product_trials.cutOff.name",
                                 "name", cutoff_before, cutoff_after,
                                 suffix="product_trials.cutOff", array_key=trial_key))
            approved_product = not any(
                r["CHANGE_TYPE"] == "VALUE_CHANGED" and r["VALUE_AFTER"] == "REJECT"
                for r in rows[-7 * len(prod_rules):]
            )
            if approved_product:
                offers_before.append(prod)
                if rng.random() > 0.1:
                    offers_after.append(prod)

        # новый продукт в офферах у части заявок
        if i % 29 == 0:
            offers_after.append("CL_T60_22.9_PROMO")

        decision_after = "APPROVE" if offers_after else "REJECT"
        if decision_before == "APPROVE" and not offers_before:
            decision_before = "REJECT"
        rows_by_path = [r for r in rows if r["APP_ID"] == app and r["PATH"] == "decisionResult.decision"]
        rows_by_path[0]["VALUE_AFTER"] = decision_after
        rows_by_path[0]["DIFF_FLAG"] = int(decision_before != decision_after)
        rows_by_path[0]["CHANGE_TYPE"] = (
            "SAME" if decision_before == decision_after else "VALUE_CHANGED")
        rows_by_path[0]["ADJUSTED_AFTER"] = decision_after

        # скоринг
        okb_b = rng.randint(500, 800)
        okb_a = okb_b + rng.choice([-30, -10, 0, 0, 15])
        rows.append(_row(app, "characteristics.score_okb", "score_okb",
                         okb_b, okb_a))
        rows.append(_row(app, "characteristics.score_equ", "score_equ",
                         rng.randint(500, 800), None))
        rows[-1]["VALUE_AFTER"] = str(rng.randint(500, 800))
        rows[-1]["DIFF_FLAG"] = int(rows[-1]["VALUE_BEFORE"] != rows[-1]["VALUE_AFTER"])
        rows[-1]["CHANGE_TYPE"] = "VALUE_CHANGED" if rows[-1]["DIFF_FLAG"] else "SAME"
        rows.append(_row(app, "decisionResult.scoring.features.score_okb",
                         "score_okb", okb_b, okb_a,
                         suffix="scoring.features", array_key="PD_RETAIL"))
        rows.append(_row(app, "decisionResult.scoring.modelName", "modelName",
                         "PD_RETAIL", pd_after, suffix="scoring",
                         array_key="PD_RETAIL"))
        rows.append(_row(app, "decisionResult.scoring.value", "value",
                         round(rng.uniform(0.3, 0.9), 3), round(rng.uniform(0.3, 0.9), 3),
                         suffix="scoring", array_key="PD_RETAIL"))

        # суммы офферов с бакетизацией
        if offers_before:
            amt_b = rng.choice([30_000, 55_000, 117_325, 300_000])
            amt_a = amt_b if rng.random() < 0.6 else rng.choice([30_000, 55_000, 117_325, 300_000])
            rows.append(_row(app, "decisionResult.available_offers.amount",
                             "amount", amt_b, amt_a,
                             suffix="available_offers",
                             array_key=f"{offers_before[0]}|standard",
                             bucket=_bucket_amount))

        segments.append({
            "APP_ID": app,
            "DECISION_BEFORE": decision_before,
            "DECISION_AFTER": decision_after,
            "DECISION_CHANGED": int(decision_before != decision_after),
            "PRODUCTS": ",".join(sorted(set(app_products))),
            "TOP_OFFER_BEFORE": offers_before[0] if offers_before else "",
            "TOP_OFFER_AFTER": offers_after[0] if offers_after else "",
            "PARTNER_TYPE": partner_type,
            "ROUTING_METHOD": routing,
            "PD_MODEL_BEFORE": pd_before,
            "PD_MODEL_AFTER": pd_after,
            "CUTOFF_BEFORE": cutoff_before,
            "CUTOFF_AFTER": cutoff_after,
            "REJECT_REASON_BEFORE": reject_before,
            "REJECT_REASON_AFTER": reject_after,
            "SCORE_OKB_BEFORE": str(okb_b),
            "SCORE_OKB_AFTER": str(okb_a),
        })
    return rows, segments
