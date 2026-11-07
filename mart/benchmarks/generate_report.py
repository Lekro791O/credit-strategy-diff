"""Render the engineering note directly from the retained raw measurements."""
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
machine=dict(cpu='Apple M4',logical_cpus=10,memory_bytes=25769803776,memory_gib=24,
             macOS='27.0.1',architecture='arm64',python='3.13.4',duckdb='2.0.0',duckdb_threads=2,
             measured_on='2026-11-07')
(HERE/'machine.json').write_text(json.dumps(machine,ensure_ascii=False,indent=2)+'\n')
before=json.loads((HERE/'before-http.json').read_text())
after=json.loads((HERE/'after-http.json').read_text())
indexes=json.loads((HERE/'index-experiment.json').read_text())
metrics=[('first_facts','Первый GET /facts после старта сервера'),
         ('layout','Смена раскладки /facts'),('filter','Фильтр /facts'),
         ('cell_to_apps','Ячейка → список заявок'),('application','Список → карточка заявки'),
         ('cached_attribute','Карточка → атрибут (локальный поиск)'),('drill_total','Полная цепочка drill'),
         ('checks_reopen','Повторное чтение отчёта проверок /checks'),
         ('report_reopen','Полное повторное открытие: run + checks + facts')]
band={'excellent':'отлично','good':'хорошо','minimum':'минимум','over_limit':'выше 5 с'}
def med(data,key):return data['metrics'][key]['median_seconds']
def samples(data,key):
    digits=6 if key=='cached_attribute' else 3
    return ' / '.join(f'{v:.{digits}f}' for v in data['metrics'][key]['seconds'])
lines=['# Замеры mart/checkrunner для инженерной записки','',
'''Ветка: `local/integration-test`. Исходная интеграционная версия: `2aa905a`.
Изменения ограничены mart/ и checkrunner/, публичные методы и схемы таблиц/представлений сохранены.''','',
'## Машина и методика','',
'''7 ноября 2026: **Apple M4, 10 логических CPU, 24 GiB RAM, macOS 27.0.1 arm64,
Python 3.13.4, DuckDB 2.0.0**. DuckDB использует 2 потока, uvicorn — один процесс.
Модель CPU/RAM/число CPU прочитаны через sysctl, версия ОС — sw_vers.''','',
'''Полный выданный RUN-20260827-0001: **2002 заявки, 289533 строки сравнения,
572221 вертикальная строка, 502 заявки с различиями, 385 смен решений**.
Пять проверок завершились без ошибок исполнения и дали 32 строки отчёта.''','',
'''Для каждой операции — **3 повтора и медиана**, настоящий uvicorn на localhost,
HTTP + передача ответа + JSON decode. Первый /facts измерен на трёх свежих
процессах сервера; файловый кэш ОС не очищался. Это не холодный диск и не
замер отрисовки DOM браузера. Авторизация cookie включена, проверки владельца
и десериализация состояния прогона входят в HTTP-время. Подготовка прогона,
запуск сервера и ожидание готовности не входят в время отдельного запроса.''','',
'''A/B использует две свежие БД, загруженные из **одинаково отсортированных строк**
pipeline. BEFORE содержит прежние attr_path/attr_change, AFTER дополнительно
attr_app_lookup. Это исключает влияние истории DELETE/INSERT и порядка строк.
Итоговая серия выполнена без параллельного pytest/других benchmark-процессов.
Сырые три значения и SHA256 mart/store.py сохранены в before-http.json/after-http.json.''','',
'Планки для сравнения в этой записке: минимум ≤5 с, хорошо ≤1 с, отлично ≤0,3 с.','',
'## HTTP: итоговые замеры','',
'Все числа — секунды.','',
'| Операция | До: три повтора | Медиана до | После: три повтора | Медиана после | Планка после |',
'|---|---|---:|---|---:|---|']
for key,label in metrics:
    digits=6 if key=='cached_attribute' else 3
    lines.append(f'| {label} | {samples(before,key)} | {med(before,key):.{digits}f} | {samples(after,key)} | {med(after,key):.{digits}f} | {band[after["metrics"][key]["band"]]} |')
lines+=['',
'''Запрос раскладки: dims=prefix,class,suffix,key,source,diff_flag. Фильтр:
dims=path,source + path_prefix=decisionResult. + change_type=VALUE_CHANGED.
Для drill выбрана ячейка PATH=decisionResult.decision, CHANGE_TYPE=VALUE_CHANGED:
**385 заявок**, список ограничен первой страницей (200 ID), карточка открыта по
первому ID. Равенство размера ячейки и total списка проверено assert.''','',
'''У текущего API нет отдельного HTTP endpoint атрибута: он уже передан в
attributes карточки. Последний шаг — поиск PATH/ARRAY_KEY в этом ответе.
Его микросекунды измерены в Python-клиенте и **не выдаются за время браузерной
отрисовки или отдельного серверного запроса**. SQL-выборка конкретного атрибута
измерена отдельно ниже. Полная цепочка drill — сумма двух HTTP-запросов и
локального поиска.''','',
'''Повторный отчёт имеет два явно разделённых замера: /checks — собственно
сохранённые проверки; run + checks + facts — повторное чтение всего экрана.
Подробные времена частей есть в JSON (run_metadata_reopen/facts_reopen).''','',
'''**Все операции укладываются в минимум ≤5 с.** Для первой сводной, широкой
раскладки и полного повторного открытия планка ≤1 с не достигнута.
Добавление ART-индекса не дало убедительного общего ускорения HTTP; небольшие
колебания до/после не следует представлять как выигрыш всей системы.''','',
'## План запросов и индексы','',
'''Использован EXPLAIN ANALYZE на копиях полного DuckDB-файла. Схемы данных не
менялись. Все запросы и три исходных времени есть в sql_plans.py и
index-experiment.json, физические планы — в plans/.''','',
'| SQL (без HTTP) | До, медиана мс | С attributes(APP_ID), мс | Index Scan после |',
'|---|---:|---:|---|']
for key,title in [('path_change_segments','PATH + CHANGE_TYPE + JOIN segments'),('application_drill','APP_ID → 141 атрибут заявки'),('attribute','APP_ID + PATH → 1 атрибут')]:
    b=indexes['baseline']['queries'][key];a=indexes['attr_app_only']['queries'][key]
    lines.append(f'| {title} | {b["median_seconds"]*1000:.3f} | {a["median_seconds"]*1000:.3f} | {"да" if a["index_scan"] else "нет"} |')
selected=indexes['attr_app_only']
lines+=['',
'''Добавлен только `CREATE INDEX IF NOT EXISTS attr_app_lookup ON attributes(APP_ID)`.
Для drill план реально переключает attributes на **Index Scan**; datamart при
JOIN остаётся Sequential Scan. Устойчивого выигрыша SQL в итоговой серии
не получено: времена сопоставимы. Трёх повторов недостаточно для
статистического заявления об ускорении на доли миллисекунды.''','',
f'Построение выбранного индекса: {selected["build_seconds"]:.4f} с; прирост файла копии после CHECKPOINT: {selected["extra_file_bytes"]} байт.',
'',
'''Дополнительно проверены datamart(APP_ID), attributes(PATH),
attributes(PATH, CHANGE_TYPE) и пара APP_ID-индексов. Дополнительные индексы
не используются рассматриваемыми планами либо не дают отдельного выигрыша;
они **не включены в production-код**. Для широких выборок DuckDB оставляет
векторный Sequential Scan и Hash Join/Group By. Прежние составные индексы и
все PRIMARY KEY сохранены.''','',
'''Создание вторичных индексов перенесено в MartStore.save_run **после bulk-load**
и counts, внутри той же транзакции. На новой БД они строятся после загрузки;
при повторном сохранении уже существующие индексы поддерживаются штатно,
CREATE IF NOT EXISTS идемпотентен. Старый готовый файл получает новый индекс
при следующем save_run/повторе прогона; чтение не создаёт его скрыто.''','',
'## Узкое место и выбор DuckDB','',
'''DuckDB подходит для локальной витрины: столбцовое хранение, SQL GROUP BY /
COUNT DISTINCT и JOIN, работа в процессе без отдельного сервера, один переносимый
файл. PRIMARY KEY фиксируют идентичность атрибута, а counts и membership-слой
сохраняют точный переход к APP_ID. Материализованные counts не суммируются
безусловно при смене раскладки: уникальные заявки пересчитываются по membership.''','',
'''Профиль реального обработчика /facts находится в facts-profile.txt. Основная
стоимость — **три вызова _group/query** для строк, общего итога и итогов строк:
каждый заново исполняет JOIN и агрегирование вертикальной выборки.
Дополнительно RunManager.load повторно десериализует большой run JSON.
Точечные SQL-фильтры занимают миллисекунды, а не основную секунду HTTP-запроса.''','',
'''Следующий рычаг ускорения для владельца API — объединение расчёта итогов в
один проход/запрос либо безопасный кэш по revision + раскладке + фильтрам,
а также исключение повторного чтения полного run JSON в одном запросе.
В этой задаче api/ не редактировался. При 3 повторах и тёплом кэше результаты
не являются SLA и не заменяют проверку под конкурентной нагрузкой.''','',
'## Полнота MartAPI и сторонняя проверка','',
'Все опубликованные фильтры поддержаны **без изменения контракта**:','',
'| Фильтр | Семантика / проверка |','|---|---|',
'| path | точный путь и список путей, пустой список → пустая выборка |',
'| path_prefix | буквальный префикс; % и _ не wildcards |',
'| key | точное имя атрибута |','| array_key | точный ключ, включая пустой |',
'| change_type | enum или список; пустой список → пустая выборка |',
'| diff_flag | 0 и 1 |','| value_before / value_after | точное значение, пустая строка, SQL-special characters |',
'| app_ids | список, дубли, пустой список, ведущие нули в строковых ID |','',
'''Фильтры комбинируются через AND и передаются SQL-параметрами.
rows/count/app_ids сверены с независимым Python-oracle на одинаковых данных
в серверном MartAPI и ReadOnlyMart worker. Проверены segments/total_apps,
сортировка уникальных APP_ID и run-only snapshot без соседнего прогона.
Новая временная .py-проверка через настоящий subprocess использует все пять
методов и сложную комбинацию фильтров; возвращает PASS. Пробелов в опубликованном
списке фильтров не обнаружено, изменений схем/фильтров не потребовалось.''','',
'## macOS, Windows-совместимость и изоляция','',
'''- Стуб fcntl при ImportError **сохранён**. На macOS используется настоящий
  модуль fcntl: тест удерживает блокировку и проверяет, что другой процесс
  не может её получить, а после освобождения — может.''',
'''- Windows-импорт без fcntl проверен имитацией ImportError. Windows-ветка
  рассчитана на один серверный процесс; фактической Windows-машины в этом
  замере не было.''',
'''- Фолбэк os.killpg → proc.kill **не изменён**. Проверен тайм-аут при имитации
  отсутствующего killpg. На обычной macOS работает настоящий killpg.''',
'''- CheckRunner и API user_runner по умолчанию выбирают Docker; серверные
  trust opt-in в тесте явно удалены. При отсутствии Docker сохранён ERROR
  с сообщением **untrusted code was not executed**.''',
'''- Тест запрещает любой Popen и подаёт код с файловым побочным эффектом:
  процесс не создаётся, маркер не появляется, ошибка сохраняется в отчёт.''',
'''- `test_timeout_and_missing_sandbox_are_visible` проходит. Docker на машине
  отсутствует, поэтому реальное исполнение контейнера **не заявляется**;
  опциональный Docker integration test пропущен.''',
'',
'## Тесты и воспроизведение','',
'''До: **91 passed + 8 subtests**, 1 skipped (Docker). После: **119 passed +
8 subtests**, 1 skipped (тот же Docker). Предупреждение Starlette/httpx —
существующая deprecation зависимостей, не ошибка теста.''','',
'```bash',
'python -m pip install -r api/requirements.txt',
'python -m pytest tests/ pipeline/tests/ mart/tests/ checkrunner/tests/ -q',
'python mart/benchmarks/http_latency.py --phase initial',
'python mart/benchmarks/prepare_variants.py',
'python mart/benchmarks/http_latency.py --phase before',
'python mart/benchmarks/http_latency.py --phase after',
'python mart/benchmarks/sql_plans.py',
'python mart/benchmarks/profile_facts.py',
'python mart/benchmarks/generate_report.py',
'```','',
'''Первая команда http_latency создаёт полный прогон, если benchmark-session ещё
не существует. prepare_variants использует исходную версию store из git-объекта
2aa905a и текущую реализацию; нужен полный git history. Для HTTP требуются права
на localhost bind. Временно запускаемые uvicorn-процессы завершаются в finally.
БД, cookie и состояние живут в gitignored data/mart-benchmark и в Git не входят.
Выполнять benchmark последовательно, без параллельного pytest. Числа после
повторного запуска могут измениться; raw JSON сохраняет конкретную серию.''','',
'## Готовый абзац для записки','',
f'«Использовали DuckDB для локальной столбцовой витрины 2002 заявок (289533 строки сравнения). '
f'На Apple M4 / 24 GiB RAM / macOS 27.0.1, Python 3.13.4, DuckDB 2.0.0 медиана трёх HTTP-замеров: '
f'первое открытие сводной {med(after,"first_facts"):.3f} с, смена раскладки {med(after,"layout"):.3f} с, '
f'фильтр {med(after,"filter"):.3f} с, drill от ячейки до атрибута {med(after,"drill_total"):.3f} с, '
f'повторное чтение проверок {med(after,"checks_reopen"):.3f} с, полное повторное открытие экрана {med(after,"report_reopen"):.3f} с. '
'Все операции укладываются в 5 с. Индекс attributes(APP_ID) включает Index Scan для заявки, '
'но заметного ускорения полного HTTP-отчёта не подтвердил. Узкое место — повторные SQL-агрегации '
'и чтение состояния run JSON; следующим шагом оптимизируем эти обращения на уровне API. '
'Схемы и контракты проверок сохранены; без Docker недоверенный код не запускается».','']
(HERE/'REPORT.md').write_text('\n'.join(lines),encoding='utf8')
print('Saved',HERE/'REPORT.md')
