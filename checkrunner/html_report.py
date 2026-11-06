"""Standalone HTML report; the application supplies its own authorized URLs."""
from html import escape
from urllib.parse import urlsplit


def render_html(report, *, report_url, application_url):
    """application_url(app_id) -> absolute HTTP(S) URL. No assumed frontend routes."""
    def href(url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Report links must be absolute HTTP(S) URLs')
        return escape(url, quote=True)

    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8">',
             '<title>Отчёт проверок</title><style>body{font:15px system-ui;margin:2rem}',
             'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5rem;text-align:left}',
             '.fail{background:#ffe0e0}.low{background:#f7f3ee;color:#655e56}',
             'pre{white-space:pre-wrap}</style><h1>Отчёт проверок</h1>',
             f'<p>Прогон: {escape(str(report["run_id"]))}. Статус: {escape(report["status"])}</p>',
             f'<p><a href="{href(report_url)}">Открыть сводный отчёт приложения</a></p>',
             '<table><thead><tr><th>Проверка</th><th>Срез</th><th>Аргумент</th><th>CNT / TOTAL</th>',
             '<th>Метрика</th><th>Вердикт</th><th>Уверенность</th><th>Заявки</th></tr></thead><tbody>']
    for row in report['rows']:
        style = 'low' if row['CONFIDENCE'] == 'LOW' else 'fail' if row['RESULT'] == 'FAIL' else ''
        cells = [row['LABEL'], row['SOURCE'], row['ARGUMENT'], f'{row["CNT"]} / {row["TOTAL"]}',
                 row['METRIC'], row['RESULT'], row['CONFIDENCE']]
        parts.append(f'<tr class="{style}">' + ''.join(f'<td>{escape(str(v))}</td>' for v in cells))
        links = [f'<a href="{href(application_url(app))}">{escape(app)}</a>' for app in row['APP_IDS']]
        parts.append('<td>' + ', '.join(links) + '</td></tr>')
    parts.append('</tbody></table><h2>Исполнение и диагностика</h2>')
    for execution in report['executions']:
        parts.append('<p>' + escape(str(execution.get('check_id', execution.get('origin', execution['index']))))
                     + ': ' + escape(execution['status']) + '</p>')
        if execution.get('diagnostic'):
            parts.append('<pre>' + escape(str(execution['diagnostic'])) + '</pre>')
    parts.append('</html>')
    return ''.join(parts)
