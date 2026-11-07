# Контракт данных v2: передача нейросетям 2 и 3

Ветка `feat/data-quality-facts`, основана на актуальном `main` (`da89ff9`).
Это дополнение к неизменённым контрактам `task/docs/СХЕМА_ВИТРИНЫ.md` и
`task/checks/README.md`; названия/схемы строк сравнения и проверок сохранены.
Общие изменения API разрешены владельцем проекта в текущей сессии.
Подключённых сессий нейросетей 2 и 3 нет: документ передаёт точный реализованный
контракт для интеграции, но не утверждает, что они подтвердили его.
**По отдельному указанию владельца frontend не изменён.**

## 1. Корректные пары и качество входа

Основной вход приложения:

```python
from pipeline import build_dataset
result = build_dataset('task/samples', normalize_cfg=None,
                       compare_cfg=None, facts_cfg=None, event=None,
                       resolver=None, expected_app_ids=None)
```

Поддерживаются `RUN/RESULTS/BEFORE_MASTER + AFTER_BETA`, сам `RESULTS`,
`BEFORE + AFTER`. Отсутствовать может и целый каталог одного среза.
`result` содержит `normalized_before`, `normalized_after`, `comparison`,
`segments`, `facts`, `quality`, `diagnostics`.

**Только пары с двумя корректными ответами входят во все аналитические списки.**
Нет ответа, повреждён JSON/кодировка, неверная структура, несовпадение APP_ID,
дубль ключа, ошибка нормализатора, невалидный результат сравнения или разрезов —
исключается вся заявка; соседние заявки продолжают обрабатываться. Появление
или исчезновение отдельного атрибута *внутри корректной пары* остаётся
ATTRIBUTE_NEW/ATTRIBUTE_LOST, не является исключением заявки.

Структурная валидация ответов организаторов: непустой объект,
`appData.application_id`, непустое строковое `decisionResult.decision`.
Нормализация должна выдать непустые строки по контракту с уникальными ключами.
`run_dataset(..., validator=...)` позволяет Python-интегратору заменить
структурную валидацию для другого формата. Контракт хранилища проверяется
до загрузки; в том числе противоречивые флаги или неоднозначное строковое
представление изменения JSON-типа исключаются с COMPARISON_FAILED.

`total_apps` качества — union имён JSON-файлов обоих срезов. Если внешний
manifest/загрузчик знает заявки, не вернувшиеся **ни в одном** срезе, передайте
их через `expected_app_ids: list[str]` (также верхнее поле POST /api/runs): они тоже попадут в диагностику.
Без этого списка полностью отсутствующую в обоих каталогах заявку обнаружить
невозможно. Пустой неизвестный набор и неверный путь — ошибка всего запуска.

```json
{
  "quality": {
    "total_apps": 7,
    "eligible_apps": 4,
    "excluded_apps": 3,
    "valid_before_responses": 6,
    "valid_after_responses": 4,
    "issues_by_code": {"MISSING_RESPONSE": 2, "INVALID_JSON": 1}
  },
  "diagnostics": [{
    "APP_ID": "APP-7", "SOURCE": "AFTER", "STAGE": "read",
    "CODE": "MISSING_RESPONSE", "ERROR_TYPE": "", "DETAIL_CODE": "",
    "MESSAGE": "Ответ отсутствует"
  }]
}
```

Значения примера схематичны. Счётчик исключённых заявок считает APP_ID один
раз; issues_by_code считает проблемы ответов, поэтому может быть больше.
valid_* считает корректно нормализованные ответы, включая те, чей парный
ответ плохой или чьё сравнение позже не прошло валидацию.

CODE: `MISSING_RESPONSE`, `INVALID_JSON`, `INVALID_RESPONSE`,
`NORMALIZATION_FAILED`, `COMPARISON_FAILED`.
SOURCE: `BEFORE`/`AFTER`; ошибка этапа пары — `PAIR` **только в диагностике**.
Это не строка CHECK: ограничение SOURCE проверок BEFORE/AFTER не меняется.
DETAIL_CODE уточняет известные случаи: DUPLICATE_ARRAY_KEY,
DUPLICATE_ATTRIBUTE, MISSING_ARRAY_KEY_FIELD, ARRAY_MODE_REQUIRED,
APP_ID_MISMATCH, INVALID_NORMALIZED_ROWS, REQUIRED_RESPONSE_FIELD.
Исходные значения и текст пользовательского кода в диагностику не включаются.

Старый `build_mart(dataset_dir, normalize_cfg, compare_cfg)` сохранён как
совместимый `(comparison, segments)` и тоже исключает плохие пары. Для качества
и выбранного facts обязательно используйте build_dataset.
Старый низкоуровневый run_directories оставлен для строгих CLI/фикстур;
его allow_unpaired **не является** политикой основной аналитической сводной.

## 2. HTTP: качество и настройки формы

`GET /api/runs/{id}/quality` → `{quality, diagnostics}`.
Эти поля также сохранены в объекте `run`. Верхняя summary.total_apps — только
число допущенных заявок; исходный размер набора берите из quality.total_apps.
При нуле допущенных заявок сводная пустая, а причины исключения доступны.
Не подменяйте проблемы загрузки выдуманными строками PASS/FAIL.

`GET /api/normalization/schema` → точная JSON Schema формы
([normalization.schema.json](normalization.schema.json)).
`GET /api/normalization/defaults` → действующие defaults девяти массивов.
Настройки передаются внутри `normalize.settings` при POST /api/runs:

```json
{
  "dataset": "<локальный каталог набора>",
  "normalize": {
    "kind": "builtin", "name": "default",
    "settings": {
      "version": 1,
      "exclude": ["appData.request_id", "appData.createDateTime"],
      "arrays": {
        "decisionResult.available_offers": {"mode": "field", "keys": ["product_id", "type"]},
        "decisionResult.scoring.value": {"mode": "index", "keys": []}
      },
      "buckets": {
        "decisionResult.product_trials.scoring.pd.value": [
          {"lower": 0, "upper": 0.3, "label": "(0 - 0.3]"},
          {"lower": 0.3, "upper": 0.6, "label": "(0.3 - 0.6]"},
          {"lower": 0.6, "upper": null, "label": "(0.6 - inf)"}
        ]
      }
    }
  },
  "compare": {"kind": "builtin", "name": "default"},
  "facts": {"kind": "builtin", "name": "default"},
  "llm_opinion": false
}
```

exclude **заменяет** дефолтный список; arrays переопределяет правила по пути,
оставляя остальные defaults; buckets задаёт точные пути и общую для двух срезов
функцию. Путь без индексов/`[]`. Режимы `field` (непустой список keys), `value`
(скаляры как множество), `index` (вектор). Для value/index keys пустой/отсутствует.
Интервалы `(lower, upper]`; null означает бесконечность. Числа границ — JSON
numbers, не строки. Пересечение/неправильный порядок интервалов запрещены.
Пробелы между интервалами допустимы: вне заданных интервалов VALUE сохраняется.
Неверные настройки отвергаются HTTP 422 **до запуска**. Сравнивается VALUE,
а не бакет; null→false — изменение. Справочники включены по умолчанию.

## 3. HTTP: вертикальная сводная и точные итоги

Основной endpoint формы — `POST /api/runs/{id}/pivot`:

```json
{
  "dimensions": ["path", "suffix", "value_adjusted", "source"],
  "column_dimension": "source",
  "filters": {"diff_flag": 1, "path_prefix": "decisionResult."},
  "segment_filters": {"product": "91741", "pd_model_after": "ML_MICRO_LOAN_V2"},
  "limit": 5000,
  "offset": 0
}
```

Ответ:

```json
{
  "rows": [{"dims": {"path": "…", "suffix": "…", "value_adjusted": "…", "source": "AFTER (BETA)"}, "cnt_apps": 2, "cnt_rows": 2}],
  "totals": {"dims": {}, "cnt_apps": 2, "cnt_rows": 12},
  "row_totals": [{"dims": {"path": "…", "suffix": "…", "value_adjusted": "…"}, "cnt_apps": 2, "cnt_rows": 4}],
  "column_totals": [{"dims": {"source": "AFTER (BETA)"}, "cnt_apps": 2, "cnt_rows": 6}],
  "limit": 5000, "offset": 0
}
```

Числа примера схематичны. cnt_apps — COUNT DISTINCT APP_ID; cnt_rows — число
уникальных присутствующих листовых фактов `(APP_ID, PATH, ARRAY_KEY, SOURCE)`.
Существующий пустой атрибут считается, отсутствующая сторона не создаёт факт.
Новый атрибут — только AFTER, потерянный — только BEFORE. Всего фактов на
samples 567 + 581 = 1148, заявок 4; считать 1148 заявками нельзя.

**Итоги строк, колонок и общий итог пересчитываются по исходным memberships.**
Не суммируйте cnt_apps ячеек: одна заявка может лежать в нескольких группах.
Итоги учитывают всю отфильтрованную выборку, даже если rows ограничены limit.
column_dimension либо отсутствует/null, либо входит в dimensions. Без него
row_totals соответствуют dimensions, column_totals пусты.

DuckDB использует существующий `fact_members`, основанный на вертикальном
`datamart`, и SQL GROUP BY counts. Фильтруемые агрегаты не получаются сложением
материализованных CNT; DISTINCT пересчитывается по связям. SQLite fallback
строит эквивалентную вертикальную UNION ALL только присутствующих сторон.
Хранилище и его проверка уникальности **не изменены**.

Доступные dimensions (`GET /api/facts/dims`):
`prefix`, `class`, **`suffix`**, `key`, `path`, `array_key`, **`source`**,
**`value_adjusted`**, `change_type`, `diff_flag`, `value_before`, `value_after`,
`adjusted_before`, `adjusted_after`; `decision_before`, `decision_after`,
`decision_changed`, `product`, `partner_id`, `partner_name`, `partner_type`,
`routing_method`, `pd_model_before`, `pd_model_after`, `cutoff_before`,
`cutoff_after`, `reject_reason_before`, `reject_reason_after`,
`score_okb_before`, `score_okb_after`.

SOURCE нормализованных данных/сохранённого result.facts — BEFORE/AFTER;
SOURCE HTTP-сводной — BEFORE (MASTER)/AFTER (BETA), как у mart/ и демо CSV.
HTTP-фильтр source принимает обе формы. SOURCE строк CHECK не меняется.
value_adjusted выбирается именно из указанного/сгруппированного среза.

`filters`: любое dimension, дополнительно `path_prefix`, `app_ids`.
Скаляр str/int означает точное равенство, список — OR, разные поля — AND.
Пустой список выбирает ничего. path_prefix — буквальный префикс строки,
`%`/`_` не SQL wildcards. Пустая строка — настоящее фильтруемое значение.
Неизвестные dimensions/filters — HTTP 422. limit 1–10000, offset ≥0.

## 4. Разрезы продукта и drill-down

`segment_filters` поддерживает обязательные UPPERCASE-поля разрезов
(DECISION_BEFORE/AFTER, DECISION_CHANGED, PARTNER_TYPE, ROUTING_METHOD,
PD_MODEL_BEFORE/AFTER, CUTOFF_BEFORE/AFTER, REJECT_REASON_BEFORE/AFTER,
SCORE_OKB_BEFORE/AFTER), а также следующие **связанные с продуктом** фильтры:

| Параметр | Смысл |
|---|---|
| product | product_id, точное вхождение в PRODUCTS |
| partner_id, partner_name, partner_type | Партнёр этого продукта, в одном из срезов |
| routing_method | Маршрутизация этого продукта, в одном из срезов |
| pd_model_before, pd_model_after | Модель того же продукта на нужном срезе |
| cutoff_before, cutoff_after | Имя порога того же продукта на нужном срезе |

Несколько продуктовых условий должны выполниться **для одного продукта**.
Нельзя принять заявку с продуктом A и моделью, которая есть только у продукта B.
Группировка по продукту/партнёру/модели использует продуктовые memberships:
заявка может участвовать в нескольких группах, но в общем итоге считается один
раз. Строки/факты также дедуплицируются по полному ключу до подсчёта итогов.
Связи сохраняются в `run.segment_details` через PRODUCT_DETAILS_BEFORE/AFTER;
обязательные поля segments и схема хранилища не изменены.
Для старых прогонов без PRODUCT_DETAILS есть только fallback обязательных
полей; для точной продуктовой детализации старый прогон следует повторить.

`POST /api/runs/{id}/pivot/apps` принимает такой же объект, но dimensions и
column_dimension не влияют на выбор APP_ID. Передайте те же filters и
segment_filters, дополнив filters **всеми dims выбранной ячейки**:

```json
{"filters":{"path":"decisionResult.scoring.value","source":"AFTER (BETA)","value_adjusted":"(0.3 - 0.6]"},"segment_filters":{"product":"91741"},"limit":200,"offset":0}
```

Возврат `{app_ids: ["…"], total: N}`. Затем существующий
`GET /api/runs/{id}/apps/{app_id}` даёт атрибуты. Идентичность атрибута:
`(APP_ID, PATH, ARRAY_KEY)`; для вертикального факта добавляется SOURCE.
Не выбрасывайте suffix/source/bucket/значения BEFORE из фильтра ячейки.
Старые GET /facts и GET /apps оставлены для совместимости. GET /facts теперь
тоже возвращает totals/row_totals/column_totals; для полного набора фильтров
и разрезов форма должна перейти на POST /pivot и /pivot/apps.

## 5. Выбранные процедуры: исполнение, а не декоративный selector

`normalize`, `compare`, `facts` — отдельные ProcedureSource в POST /api/runs.
kind/name: `builtin/default`, `workspace/<имя без .py>`, `file/<путь в workspace
текущего пользователя>`. normalize дополнительно принимает settings из §2.
Выбранный facts теперь вызывается при запуске, результат сохраняется в
`run.fact_rows` **и ограничивает факты, итоги и drill-down сводной**.

Пользовательский файл:

```python
from pipeline import aggregate

def run(comparison):
    return [fact for fact in aggregate(comparison) if fact['KEY'] == 'decision']

PROCEDURE = {'stage': 'aggregate', 'entrypoint': run}
```

Сигнатуры:

```python
normalize(document, *, app_id, source, config) -> Iterable[normalized dict]
compare(before_rows, after_rows) -> Iterable[comparison dict]
aggregate(comparison_rows) -> Iterable[fact dict]
```

stage `facts` в UI соответствует `PROCEDURE.stage = aggregate`.
Контракт normalized: APP_ID, SOURCE, PREFIX, CLASS, SUFFIX, ARRAY_KEY, KEY, PATH,
VALUE, ADJUSTED_VALUE, VALUE_TYPE (`string/number/boolean/null`).
Контракт comparison — неизменная схема ТЗ. Compare обязан сохранить union
ключей и исходные значения/бакеты; четыре состояния и флаги проверяются.
Контракт fact: PREFIX, CLASS, SUFFIX, ARRAY_KEY, KEY, VALUE_ADJUSTED, DIFF_FLAG,
SOURCE (BEFORE/AFTER), CNT (уникальные APP_ID в атомарной группе).
Facts-процедура может отобрать подмножество канонических групп; дубли,
выдуманные группы и неверный CNT отвергаются, чтобы drill-down не противоречил
числам. Новые измерения/другой смысл метрики требуют отдельного контракта.
Это выбранная область сводной, а не изменение исходной витрины для CHECK.

Нормализация завершает обе стороны до начала compare, затем aggregate
выполняется один раз для всех допущенных пар. События `event`:
`{stage: normalize|compare|facts, status: start|progress|finish, ...}`;
начало содержит code_ref/code_snapshot, progress — completed/total.
API сохраняет действительные этапы и снимок исполняемой процедуры.

### Нейросети 2: граница исполнителя

`build_dataset(..., resolver=resolver)` вызывает
`resolver(spec: dict|None, stage: str, default: callable) -> callable`.
Это точка подключения песочницы; схемы данных не зависят от способа исполнения.
`api/procedures.py` — текущая реализация. По умолчанию пользовательские
workspace/file-процедуры запрещены до настройки песочницы. Для доверенного
локального кода владелец сервера может установить
`CSD_TRUST_LOCAL_PROCEDURES=1`. Клиентский JSON не может включить этот режим.
Это отдельный процесс с тайм-аутом 60 секунд, **не защита машины от кода**;
не выдавать его за Docker-изоляцию. Механизм CHECK в checkrunner не менялся.

JSON-протокол worker v1 (`pipeline/procedure_worker.py`):

```json
{"version":1,"stage":"aggregate","code":"<снимок Python>","origin":"workspace:my_facts","args":[["<comparison rows>"]],"settings":null,"app_id":null,"source":null}
```

Для normalize args=[document], app_id/source заданы, config строится из settings;
для compare args=[before_rows, after_rows]; для aggregate args=[comparison_rows].
Ответ stdout — JSON array строк, без pickle; print пользователя идёт в stderr.
Родитель валидирует схему результата, уникальность и смысл counts. Процедура
считывается один раз в начале прогона: редактирование файла не меняет код
между BEFORE и AFTER. Передаются снимок кода и данные, не production DB/ключи.
Встроенные процедуры продолжают работать без opt-in.
Для публичного запуска нужен подключённый нейросетью 2 sandbox backend с
лимитами памяти/вывода и закрытой сетью; автоматического разрешения exec нет.

## 6. Точное поручение нейросети 3 (frontend не изменён)

1. Форма настроек строится по /api/normalization/schema и defaults; сохраняет
   настройки в normalize.settings, не меняет контракт строк витрины.
2. Селекторы передают kind/name соответствующих процедур. Для пользовательских
   файлов учитывать серверный запрет, не устанавливать trust-флаг из браузера.
3. Для сводной использовать POST /pivot, передавать column_dimension явно.
4. В frontend/static/app.js:loadPivot заменить локальные colTotals/rowTotals/grand
   суммирования на серверные column_totals/row_totals/totals. Ключи totals
   сопоставлять по dims, а не по позиции строки (особенно после сортировки).
5. CSV-экспорт использует те же серверные итоги. В подписи основной меры —
   «уникальные заявки»; cnt_rows при необходимости отдельная мера «факты».
6. Клик по ячейке — POST /pivot/apps с полным фильтром и тем же segment_filters.
7. Показать eligible_apps/excluded_apps и диагностический список /quality;
   не складывать проблемы ответов с числом исключённых заявок.
8. Подключить фильтры product/partner/routing/model/cutoff из §4.
9. Смена решения и cutoff — два наблюдаемых изменения. Не писать
   «решение изменилось из-за cutoff» без отдельного доказательства причины.

## Проверка и пределы

```sh
python -m pytest tests/ pipeline/tests mart/tests checkrunner/tests -q
```

Проверяются старые эталоны CSV, реальные task/samples, уникальность демо,
непарные/повреждённые/невалидные ответы, обе отсутствующие стороны с expected_ids,
ошибки обработчиков, одинаковый бакет при разных VALUE, выбор facts через API,
ограничение его результата в сводной, totals/drill-down на SQLite и DuckDB,
продуктовая связь фильтров и запуск всего демо до готового отчёта.
Полный организаторский архив в текущей папке не найден и не проверен.
Датасет, workspace и сгенерированные отчёты в Git не входят.
