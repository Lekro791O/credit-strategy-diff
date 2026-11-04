# CONTRACT — зафиксированные схемы и API

Все трое пишем код против этого файла. Любое изменение — только после синхронизации
в команде. Схемы — источник правды для `schemas/`.

## 1. Application (заявка)

```json
{
  "application_id": "a1b2c3d4",
  "age": 34,
  "income_monthly": 85000,
  "employment_months": 18,
  "debt_load": 0.42,
  "credit_history_months": 36,
  "delinquencies_12m": 0,
  "requested_amount": 300000,
  "region": "MOW",
  "has_mortgage": false
}
```

Поля фиксированы. Генератор (`data/`) создаёт заявки строго по этой схеме.

## 2. Strategy (версия стратегии)

```json
{
  "version": "v1.0",
  "rules": [
    {
      "id": "AGE_MIN",
      "when": {"field": "age", "op": "lt", "value": 21},
      "effect": "decline",
      "reason_code": "AGE_MIN"
    },
    {
      "id": "DEBT_LOAD_MAX",
      "when": {"field": "debt_load", "op": "gt", "value": 0.5},
      "effect": "decline",
      "reason_code": "DEBT_LOAD_HIGH"
    },
    {
      "id": "LIMIT_BY_INCOME",
      "when": {"field": "income_monthly", "op": "gte", "value": 50000},
      "effect": {"limit": 250000},
      "reason_code": "LIMIT_250K"
    }
  ],
  "score_model": {"weights": {"debt_load": -0.8, "delinquencies_12m": -1.5, "credit_history_months": 0.01, "income_monthly": 0.00001}, "bias": 0.5}
}
```

- `op`: `lt | lte | gt | gte | eq | neq | in`
- `effect`: `decline` (отказ) или объект с `limit` (одобрение с лимитом)
- Правила применяются по порядку; первый `decline` останавливает прогон,
  все сработавшие правила пишутся в `matched_rules`
- `score` считается всегда, по весам `score_model`

## 3. Decision (результат прогона одной заявки)

Выход движка. Одна запись на заявку на версию стратегии.

```json
{
  "application_id": "a1b2c3d4",
  "strategy_version": "v1.0",
  "approved": true,
  "score": 0.71,
  "limit": 250000,
  "matched_rules": ["AGE_OK"],
  "reason_codes": ["PASS"]
}
```

- при одобрении `reason_codes: ["PASS"]`
- при отказе — коды сработавших `decline`-правил

## 4. DiffRecord (сравнение двух решений)

Выход аналитики. Одна запись на заявку.

```json
{
  "application_id": "a1b2c3d4",
  "old": {"approved": true,  "score": 0.71, "limit": 250000, "reason_codes": ["PASS"]},
  "new": {"approved": false, "score": 0.55, "limit": 0,      "reason_codes": ["DEBT_LOAD_HIGH"]},
  "changed": true,
  "change_type": "APPROVED_TO_DECLINED",
  "change_reason": ["DEBT_LOAD_HIGH"]
}
```

`change_type` (enum): `NO_CHANGE | APPROVED_TO_DECLINED | DECLINED_TO_APPROVED | LIMIT_CHANGED | SCORE_CHANGED`.
Приоритет классификации: смена вердикта → смена лимита → смена скора.
`change_reason` = reason_codes из `new`, которых не было в `old`.

## 5. API (FastAPI, порт 8000)

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| POST | `/run` | `{old_strategy, new_strategy, applications[]}` | `{job_id, total, changed_count}` |
| GET | `/report/summary` | `job_id` | `{total, changed, approval_rate_old, approval_rate_new, delta_pp, top_reasons[]}` |
| GET | `/report/matrix` | `job_id` | миграции: `{approved_approved, approved_declined, declined_approved, declined_declined}` |
| GET | `/report/affected` | `job_id`, опц. `change_type`, `reason`, сегменты (`age_from`, `income_from`...) | список `DiffRecord` |
| GET | `/export/csv` | `job_id` | CSV со всеми DiffRecord |

Хранение результатов прогона — в памяти (`job_id` → список DiffRecord). Для хакатона достаточно.

## 6. Разделение зон

- **Codex 1**: разделы 1, 2, 3 — движок в `engine/`, генератор в `data/`. Не знает про HTTP.
- **Codex 2**: разделы 4, 5 — `analytics/` + `api/`. Не трогает рантайм правил.
- **Kimi**: раздел 5 как потребитель + всё в `frontend/`. Не меняет логику движка.
