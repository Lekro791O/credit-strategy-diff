# Pipeline, контракт Python v1

Python 3.10+, только стандартная библиотека. Запуск из корня репозитория:

```sh
python3 -m unittest discover -s pipeline/tests -v
mkdir -p pipeline/artifacts
python3 -m pipeline --before task/samples/BEFORE --after task/samples/AFTER \
  --output pipeline/artifacts/samples-new.json
# Полный датасет (когда получен у организаторов):
python3 -m pipeline \
  --before data/RUN-20260827-0001/RESULTS/BEFORE_MASTER \
  --after data/RUN-20260827-0001/RESULTS/AFTER_BETA \
  --output pipeline/artifacts/full.json
```

Выходной файл должен быть новым и находиться вне входных каталогов. CLI ничего
не перезаписывает. `pipeline/artifacts/` исключён из Git. Зависимости устанавливать
не требуется. JSON-файлы идентифицируются именем `<APP_ID>.json`;
`appData.application_id`, если есть, должен совпадать с именем.

## Вызов из приложения

```python
from pipeline import run_directories, drill_down

result = run_directories(
    'task/samples/BEFORE', 'task/samples/AFTER',
    progress=lambda event: print(event),  # опционально
)
# Передать владельцу mart/:
comparison_rows = result['comparison']
segment_rows = result['segments']
# Передать владельцу api/frontend:
facts = result['facts']
fact = facts[0]
attributes = drill_down(comparison_rows, **{k: v for k, v in fact.items() if k != 'CNT'})
app_ids = sorted({row['APP_ID'] for row in attributes})
# Для конкретного атрибута использовать (APP_ID, PATH, ARRAY_KEY).
```

Возвращается словарь с пятью списками словарей:
`normalized_before`, `normalized_after`, `comparison`, `segments`, `facts`.
`run_pair(before, after, *, app_id, config=None, handlers=None)` обрабатывает одну
пару загруженных JSON-объектов и возвращает первые четыре списка. При отсутствии
целого среза передаётся `None`, а не `{}`. `aggregate(pair['comparison'])`
добавляет факты. Для каталогов отсутствующая пара по умолчанию ошибка;
`allow_unpaired=True` / `--allow-unpaired` явно разрешает такие заявки.

Нормализованная строка: `APP_ID`, `SOURCE`, `PREFIX`, `CLASS`, `SUFFIX`,
`ARRAY_KEY`, `KEY`, `PATH`, `VALUE`, `ADJUSTED_VALUE`, `VALUE_TYPE`.
Значения и бакеты — строки; `VALUE_TYPE` — `string`, `number`, `boolean` либо
`null`, чтобы не склеить число `1` со строкой `"1"`. Это внутренний промежуточный
контракт pipeline, дополнение к CSV примеров. Публичная витрина `comparison`
содержит **ровно** поля `task/docs/СХЕМА_ВИТРИНЫ.md`, без SOURCE и VALUE_TYPE.

`SOURCE` всегда `BEFORE` / `AFTER`. `null` сериализуется как `NULL`, логические
значения как `true` / `false`: это совместимо с выданными проверками. Пустая
строка остаётся пустой. Отсутствие определяется наличием ключа строки, а не
пустым значением. `null -> false` — `VALUE_CHANGED`; исчезновение null или пустой
строки — `ATTRIBUTE_LOST`. Различие определяется исходными значениями и типами,
а не бакетами. Пустые объекты/массивы листьев не создают; их превращение в
непустые отражается появлением листьев.

## Конфигурация массивов и бакетов

`default_config()` описывает все девять массивов из `ФОРМАТ_ОТВЕТА.md`.
Настройки изолированы для каждого вызова. Справочники включены, независимые
повторения скоров на разных этапах сохраняются. Исключены только
`appData.request_id` и `appData.createDateTime`; исключение действует также
на всё поддерево указанного пути. Пути настроек не содержат индексов и `[]`.
Неизвестный массив, в том числе пустой, требует явной настройки.

```python
from pipeline import ArrayRule, default_config, run_pair

config = default_config()
config.arrays['decisionResult.tags'] = ArrayRule('value')
config.exclude += ('product_catalog',)  # осознанная пользовательская настройка
# Одна и та же функция для BEFORE и AFTER; должна возвращать str:
config.bucket = lambda path, value: 'LOW' if path == 'characteristics.score_okb' and float(value) < 600 else value
pair = run_pair(before_json, after_json, app_id='I32554354', config=config)
```

`ArrayRule('field', ('product_id', 'type'))` — объект по составному ключу;
`ArrayRule('value')` — скаляры как множество (повторы одного значения удаляются);
`ArrayRule('index')` — вектор с индексами от 0. В `ARRAY_KEY` уровни разделены `|`,
компоненты составного ключа — `:`. Символы-разделители, `%` и `~` внутри значений
URL-кодируются; обычные идентификаторы `91741|AGELOW@21` остаются читаемыми.
В режиме value нескалярные значения запрещены, а нестроковые ключи имеют
префикс `~`. В режиме field отсутствующий, null-ключ или повтор элемента —
ошибка: строки не затираются. Для индексового вектора KEY — имя исходного массива,
например `value`, а ARRAY_KEY — `ML_MICRO_LOAN_V1|0`.

По умолчанию бакетизация тождественная: произвольные числа не группируются
без решения аналитика. Можно заменить `config.bucket(path, value)` или весь
нормализатор. Для эталонов есть `example_config(legacy=True)` (`task/example/`)
и `example_config()` (`task/examples/`): виртуальные сегменты `scoreCard`, `Limit`,
`reason`, исторический KEY `code` либо пустой KEY, интервалы из эталонов.
Эти правила не применяются к реальному датасету автоматически.

## Заменяемые Python-обработчики

Все этапы вызываются через `Handlers`; хранилище и веб-приложение не требуются.

```python
from dataclasses import replace
from pipeline import Handlers, load_handler, run_directories

handlers = replace(Handlers(), normalize=load_handler('my_processing:normalize'))
result = run_directories('task/samples/BEFORE', 'task/samples/AFTER', handlers=handlers)
```

Сигнатуры:

```python
normalize(document: dict, *, app_id: str, source: str, config: Config) -> Iterable[dict]
compare(before: list[dict], after: list[dict]) -> Iterable[dict]
segment(before: dict | None, after: dict | None, *, app_id: str) -> dict
aggregate(comparison: list[dict]) -> Iterable[dict]
```

`compare` получает нормализованные строки одного APP_ID; `aggregate` — весь
прогон, чтобы CNT не считался отдельно по файлам. Пользовательский нормализатор
должен выдавать VALUE_TYPE. Схема, типы, уникальность ключей нормализованных
строк/сравнения и согласованность DIFF_FLAG проверяются на границе этапов.
Семантика пользовательского кода остаётся ответственностью автора процедуры.
CLI поддерживает `--normalize module:callable`, `--compare`, `--segment`,
`--aggregate`, `--config module:factory` (фабрика без аргументов возвращает Config).
Импортируется доверенный Python-код из окружения; это не песочница для
непроверенных загрузок из браузера.

`progress(event)` после заявки получает
`{'stage': 'compare', 'completed': 1, 'total': 4, 'app_id': 'I32554354'}`.
Приложение может оборачивать каждый callable для таймингов и просмотра исходников.
Ни один этап не зависит от HTTP, DuckDB или UI.

## Ошибки и ограничения

`PipelineError(ValueError)` — ожидаемые ошибки ввода/контракта. CLI выводит
`pipeline: <сообщение>` в stderr и завершается с кодом 2; успешный запуск — 0.
Примеры сообщений:

- `Array mode required: app=... path=...` — добавьте ArrayRule.
- `Missing key field: ...: app=... path=... index=...` — нарушен ключ массива.
- `Duplicate array key: app=... path=... index=...` / `Duplicate attribute: ...`.
- `APP_ID differs from filename: app=...` — перепутаны документы.
- `Unpaired applications: BEFORE-only=N, AFTER-only=M` — неполная пара каталогов.
- `Cannot read JSON: file=... (JSONDecodeError)` — неверный JSON/кодировка/доступ;
  повторяющиеся имена полей и NaN/Infinity также запрещены.
- `Invalid ... handler output: ...` — пользовательская функция нарушила контракт.
- `Handler failed: app=... (ExceptionType)` — исключение в процедуре; исходное
  исключение доступно приложению как `__cause__`, не для показа публичному UI.

Значения атрибутов не включаются в диагностические сообщения. Литералы точек
и пустые имена JSON-полей отвергаются, так как точка — разделитель PATH.
Входные документы не изменяются. Все результаты одного прогона сейчас в памяти;
для значительно больших наборов можно вызывать run_pair потоково, отдавать
сравнение хранилищу и заменить агрегатор SQL-процедурой.

См. [HANDOFF.md](HANDOFF.md) для точных правил counts и разрезов, статуса
согласования с участниками 2 и 3 и результатов проверки.

## Подключение к приложению из ветки app

Приложение вызывает `from pipeline import build_mart`. Этот вход теперь
экспортирован и совместим с `api/runs.py` без правок API:

```python
from pipeline import build_mart
rows, segments = build_mart('task/samples',
    {'kind': 'builtin', 'name': 'default'},
    {'kind': 'builtin', 'name': 'default'})
```

Для полного набора передайте каталог `data/RUN-...` с
`RESULTS/BEFORE_MASTER` и `RESULTS/AFTER_BETA`; также поддерживаются сам
`RESULTS` и пара каталогов `BEFORE/AFTER`, как в samples.
`normalize_cfg` и `compare_cfg` принимают `builtin/default`, callable либо
`{'kind': 'module', 'name': 'my_module:function'}`. Источники workspace/file
этим адаптером пока не разрешаются и вызывают понятный PipelineError.

Интеграция проверена локально с веткой app: исходные JSON task/samples →
595 строк витрины → SQLiteMart приложения → сводная таблица и проверки.
4 заявки, 3 с различиями, 2 со сменой решения. Это реальные выданные samples,
а не demo://synthetic. Тестов теперь 16, включая контракт build_mart.

## Актуальный контракт обработки и сводной

См. [INTEGRATION_V2.md](INTEGRATION_V2.md): build_dataset с диагностикой
исключений, серверная JSON-конфигурация, отдельные обработчики трёх этапов,
вертикальная сводная с DISTINCT-итогами и точные инструкции участникам 2/3.
Этот документ уточняет политику отсутствующих ответов и подключения API,
описанную выше. Основная сводная использует только корректные пары.
