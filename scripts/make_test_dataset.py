"""Генератор синтетического тест-датасета: 5000 заявок в двух срезах.

Сценарии заданы точными квотами — файл truth.csv в папке датасета
является эталоном: по нему можно сверить числа прогона
(apps_with_diffs, decision_changed, распределения решений).

Запуск из корня репозитория:
    python scripts/make_test_dataset.py
"""
from __future__ import annotations

import csv
import json
import random
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "data" / "RUN-TEST5000"

N = 5000
SCENARIOS = [  # (сценарий, сколько заявок)
    ("IDENTICAL", 3500),       # документы совпадают
    ("VALUE_CHANGED", 750),    # значения едут, решение то же
    ("DECISION_FLIP", 400),    # скор пересекает порог, решение меняется
    ("ATTRIBUTE_NEW", 200),    # в AFTER появляется businessDecision
    ("ATTRIBUTE_LOST", 150),   # из AFTER пропадает characteristics.score_equ
]
THRESHOLD = 550  # score_okb >= 550 -> APPROVE


def make_doc(app_id: str, score_okb: int, score_equ: float, epr: int,
             model: str, cutoff_name: str, cutoff_value: float,
             hit_age: bool) -> dict:
    decision = "APPROVE" if score_okb >= THRESHOLD else "REJECT"
    return {
        "appData": {"application_id": app_id, "request_id": f"req-{app_id}",
                    "createDateTime": "2024-03-01T10:00:00Z"},
        "decisionResult": {
            "decision": decision,
            "available_offers": [
                {"product_id": 15210, "type": "standard", "rank": 1, "limit": 100000},
                {"product_id": 51, "type": "manual", "rank": 2, "limit": 50000},
            ],
            "product_trials": [{
                "product_id": 15210, "product_name": "MFO_ZAIMER", "tariff": 190,
                "partner_id": 1450, "partner_name": "MKK_BYSTRO", "partner_type": "CURRENT",
                "routing_method": "api",
                "rules": [
                    {"name": "AGELOW@21", "description": "младше 21", "hit": hit_age},
                    {"name": "RESTRICTED_REGIONS", "description": "регион", "hit": False},
                ],
                "decision": decision,
                "rejectReason": "HARDCHECK" if (hit_age and decision == "REJECT") else None,
                "rejectReasonCode": "AGELOW@21" if (hit_age and decision == "REJECT") else None,
                "scoring": {
                    "scoreModels": [
                        {"modelName": model, "value": round(score_okb / 1000, 3), "type": "APPROVAL_PROBABILITY"},
                        {"modelName": "ML_MICRO_LOAN_V2", "value": round(score_equ, 3), "type": "APPROVAL_PROBABILITY"},
                    ],
                    "pd": {"modelName": model, "value": round(score_okb / 1000, 3)},
                },
                "cutOff": {"name": cutoff_name, "value": cutoff_value},
                "epr": epr,
            }],
            "scoring": [{
                "modelName": model,
                "features": {"score_okb": score_okb, "age": 34, "income": 45000},
                "value": [0.4, 0.6, round(score_okb / 1000, 3)],
            }],
        },
        "characteristics": {"score_okb": score_okb, "score_equ": round(score_equ, 3)},
        "ext_data": [
            {"source": "nbki", "status": 200,
             "body": {"action": "score_okb", "params": {"data": score_okb, "ok": True}}},
        ],
        "product_catalog": [{"mfo_id": 1, "product_id": 15210, "name": "MFO_ZAIMER"}],
        "tariff_matrix": [{"mfo_id": 1, "product_id": 15210, "tariff": 190}],
    }


def main() -> None:
    rng = random.Random(20261107)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    (TARGET / "RESULTS" / "BEFORE_MASTER").mkdir(parents=True)
    (TARGET / "RESULTS" / "AFTER_BETA").mkdir(parents=True)

    plan = [s for s, n in SCENARIOS for _ in range(n)]
    rng.shuffle(plan)

    truth_rows = []
    stats = {s: 0 for s, _ in SCENARIOS}
    for i, scenario in enumerate(plan):
        app_id = f"T{100000 + i}"
        score_okb = rng.randint(400, 900)
        score_equ = round(rng.uniform(0.2, 0.95), 3)
        epr = rng.choice([990, 1380, 1750])
        hit_age = rng.random() < 0.1
        before = make_doc(app_id, score_okb, score_equ, epr,
                          "ML_MICRO_LOAN_V1", "CUTOFF_BASE", 0.55, hit_age)
        after = json.loads(json.dumps(before))
        changed = scenario != "IDENTICAL"
        if scenario == "VALUE_CHANGED":
            after["decisionResult"]["product_trials"][0]["epr"] = epr + 100
            after["characteristics"]["score_equ"] = round(min(0.99, score_equ + 0.05), 3)
        elif scenario == "DECISION_FLIP":
            new_okb = rng.randint(400, THRESHOLD - 1) if score_okb >= THRESHOLD \
                else rng.randint(THRESHOLD, 900)
            after["characteristics"]["score_okb"] = new_okb
            after["ext_data"][0]["body"]["params"]["data"] = new_okb
            t = after["decisionResult"]["product_trials"][0]
            t["scoring"]["pd"]["value"] = round(new_okb / 1000, 3)
            t["scoring"]["scoreModels"][0]["value"] = round(new_okb / 1000, 3)
            t["decision"] = after["decisionResult"]["decision"] = \
                "APPROVE" if new_okb >= THRESHOLD else "REJECT"
        elif scenario == "ATTRIBUTE_NEW":
            after["decisionResult"]["businessDecision"] = "OFFER"
        elif scenario == "ATTRIBUTE_LOST":
            del after["characteristics"]["score_equ"]
        dec_before = before["decisionResult"]["decision"]
        dec_after = after["decisionResult"]["decision"]
        (TARGET / "RESULTS" / "BEFORE_MASTER" / f"{app_id}.json").write_text(
            json.dumps(before, ensure_ascii=False), encoding="utf-8")
        (TARGET / "RESULTS" / "AFTER_BETA" / f"{app_id}.json").write_text(
            json.dumps(after, ensure_ascii=False), encoding="utf-8")
        truth_rows.append((app_id, scenario, int(changed),
                           dec_before, dec_after, int(dec_before != dec_after)))
        stats[scenario] += 1

    with (TARGET / "truth.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["APP_ID", "SCENARIO", "CHANGED",
                    "DECISION_BEFORE", "DECISION_AFTER", "DECISION_CHANGED"])
        w.writerows(truth_rows)

    changed = sum(1 for r in truth_rows if r[2])
    flipped = sum(1 for r in truth_rows if r[5])
    db = sum(1 for r in truth_rows if r[3] == "APPROVE")
    da = sum(1 for r in truth_rows if r[4] == "APPROVE")
    print(f"датасет: {TARGET}")
    print(f"сценарии: {stats}")
    print(f"ЭТАЛОН: всего={len(truth_rows)} с_изменениями={changed} "
          f"смена_решения={flipped} approve_before={db} approve_after={da}")


if __name__ == "__main__":
    sys.exit(main())
