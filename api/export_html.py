"""Экспорт отчёта проверок в самодостаточный HTML.

Документ открывается по ссылке и скачивается; из него — ссылка на
сводный отчёт приложения (маршрут фронта #/runs/<id>).
"""
from __future__ import annotations

import html
from typing import Any

COLS = ["LABEL", "SOURCE", "ARGUMENT", "CNT", "TOTAL", "METRIC",
        "RESULT", "CONFIDENCE"]
COL_TITLES = {"LABEL": "проверка", "SOURCE": "срез", "ARGUMENT": "что именно",
              "CNT": "числа", "TOTAL": "из", "METRIC": "метрика",
              "RESULT": "итог", "CONFIDENCE": "уверенность"}


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def render_checks_html(run: dict[str, Any], app_url: str) -> str:
    summary = run.get("summary") or {}
    rows = run.get("check_rows") or []

    def row_cls(r: dict[str, Any]) -> str:
        if r.get("RESULT") == "FAIL":
            return "fail low" if r.get("CONFIDENCE") == "LOW" else "fail"
        return ""

    body = "\n".join(
        f"<tr class='{row_cls(r)}'><td>{_esc(r.get('_check_id'))}</td>"
        + "".join(f"<td>{_esc(r.get(c))}</td>" for c in COLS)
        + f"<td>{_esc(', '.join((r.get('APP_IDS') or [])[:20]))}</td></tr>"
        for r in rows
    )
    if not body:
        body = "<tr><td colspan='11'>Нет строк — проверки не выполнялись</td></tr>"

    cards = "".join(
        f"<div class='card'><div class='num'>{v}</div><div class='lbl'>{k}</div></div>"
        for k, v in (
            ("заявок в прогоне", summary.get("total_apps", 0)),
            ("с различиями", summary.get("apps_with_diffs", 0)),
            ("смена решения", summary.get("apps_decision_changed", 0)),
        )
    )
    fails = sum(1 for r in rows if r.get("RESULT") == "FAIL")
    diagnostics = ''.join('<li>' + _esc(e.get('check', '?')) + ': ' + _esc(e.get('error', e)) + '</li>'
                          for e in run.get('errors', []))

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>Отчёт проверок — {_esc(run.get('config', {}).get('name', 'прогон'))}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1c2430}}
h1{{font-size:20px}} h2{{font-size:15px;color:#5a6b7d}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:12px}}
th,td{{border:1px solid #d5dde5;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#f0f4f8}}
tr.fail td{{background:#fdecea}}
tr.fail.low td{{background:#fdf6ec;color:#7a6a4f}}
.cards{{display:flex;gap:12px;margin:12px 0}}
.card{{border:1px solid #d5dde5;border-radius:8px;padding:10px 16px}}
.num{{font-size:22px;font-weight:600}} .lbl{{color:#5a6b7d;font-size:12px}}
.meta{{color:#5a6b7d;font-size:12px;margin-top:18px}}
</style></head><body>
<h1>Отчёт автоматических проверок</h1>
<h2>{_esc(run.get('config', {}).get('name', ''))} · прогон {_esc(run.get('id', ''))}</h2>
<div class="cards">{cards}<div class="card"><div class="num">{fails}</div>
<div class="lbl">проверок FAIL</div></div></div>
<p><a href="{html.escape(app_url, quote=True)}">Сводный отчёт приложения →</a></p>
<table><thead><tr><th>проверка</th>{''.join(f'<th>{COL_TITLES[c]}</th>' for c in COLS)}<th>заявки</th></tr></thead>
<tbody>{body}</tbody></table>
<h2>Диагностика исполнения</h2><ul>{diagnostics}</ul>
<div class="meta">Сформировано приложением credit-strategy-diff. CONFIDENCE=LOW —
срабатываний мало, это сигнал для проверки, а не подтверждённая поломка.</div>
</body></html>"""
