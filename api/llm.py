"""Альтернативное мнение ИИ — опциональный блок задания.

Витрину целиком в модель не отправить: берём только компактную сводку —
итоги миграции решений, топ изменившихся атрибутов, строки FAIL из
отчёта проверок. Подключение через переменные окружения
(CSD_LLM_BASE_URL / CSD_LLM_API_KEY / CSD_LLM_MODEL, OpenAI-совместимый
чат-интерфейс). Модель недоступна — статус "unavailable" явно, остальное
приложение работает.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from . import config
from .mart import SQLiteMart

_PROMPT = """Ты — опытный риск-аналитик, проверяющий изменение стратегии СПР
перед выкатом на прод. Ниже компактная сводка различий двух прогонов
(BEFORE = прод, AFTER = бета с изменением) и результаты автоматических проверок.

Скажи коротко (5-8 пунктов):
1. что выглядит подозрительно и требует внимания аналитика;
2. что, скорее всего, является ожидаемым следствием правки, а не поломкой;
3. на что посмотреть в первую очередь.

Только по данным сводки, не выдумывай числа.

СВОДКА:
"""


def build_summary(mart: SQLiteMart, check_rows: list[dict[str, Any]]) -> dict[str, Any]:
    stats = mart.decision_stats()
    top_attrs = mart.conn.execute(
        "SELECT PATH, COUNT(DISTINCT APP_ID) c FROM mart WHERE DIFF_FLAG = 1 "
        "GROUP BY PATH ORDER BY c DESC LIMIT 10"
    ).fetchall()
    fails = [r for r in check_rows if r.get("RESULT") == "FAIL"][:20]
    return {
        "total_apps": stats["total_apps"],
        "apps_with_diffs": stats["apps_with_diffs"],
        "decision_before": stats["decision_before"],
        "decision_after": stats["decision_after"],
        "top_changed_attributes": [
            {"path": p, "apps": c} for p, c in top_attrs],
        "failed_checks": [
            {k: r.get(k) for k in
             ("LABEL", "SOURCE", "ARGUMENT", "CNT", "TOTAL", "METRIC")}
            for r in fails],
    }


def ask_llm(summary: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    if not config.llm_configured():
        return {"status": "unavailable",
                "reason": "LLM не настроен: задайте CSD_LLM_BASE_URL, "
                          "CSD_LLM_API_KEY, CSD_LLM_MODEL (см. README)"}
    s = config.llm_settings()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{s['base_url'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {s['api_key']}"},
                json={
                    "model": s["model"],
                    "messages": [
                        {"role": "system", "content": _PROMPT},
                        {"role": "user",
                         "content": json.dumps(summary, ensure_ascii=False)},
                    ],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return {"status": "ok", "model": s["model"], "opinion": content,
                    "summary": summary}
    except Exception as e:
        return {"status": "unavailable",
                "reason": f"ошибка обращения к модели: {e}", "summary": summary}
