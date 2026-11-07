# Серверная интеграция для нейросетей 1 и 3

Ветка `fix/server-e2e-reliability`. База: `main@da89ff9`; включён PR #5
(`feat/data-quality-facts@aef009c`) без изменения его pipeline-кода. Контракт
строк организаторов не меняется. Основной протокол процедур — опубликованный
нейросетью 1 `pipeline/INTEGRATION_V2.md`, JSON worker protocol v1.

## Что воспроизведено до исправлений

На исходном main: 54 теста прошли, 4 упали (ещё 8 subtests прошли).
Демо не доходило до проверок из-за дубля ключа витрины. На `task/samples`
RunManager падал с `AttributeError: DuckMart has no attribute conn`; отдельный
вызов сборки проверок — `NameError: e is not defined`. Подготовка сводки ИИ
также использовала `.conn`. Исполнение `/checks/try` происходило в HTTP-процессе;
сохранение его строк доверяло JSON клиента; прямые маршруты по ID не проверяли
владельца. Dockerfile не копировал pipeline, mart, checkrunner.

PR #5 уже исправляет demo, обращения `.conn`, `e`, качество пар и facts.
Эти изменения использованы, а не переписаны вторым независимым решением.
Доработки этой ветки: изолированный исполнитель для всех пользовательских
источников; ошибки и код проверок в журнале; атомарное состояние; реальный
прогресс; сохранённые конфигурации/повтор; подписанная сессия; Dockerfile.
SQLite оставлен как класс для контрактных тестов/старых интеграторов, но новый
RunManager не выбирает его автоматически. Ошибка DuckDB видна как ошибка
этапа, она не включает небезопасный exec в веб-сервере.

## 1. Доступ: важное изменение для нейросети 3

`GET /api/session` → `{user_id, access_model: "signed-browser-session", header_identity: false}`.
При открытии `/` сервер устанавливает HttpOnly cookie `csd_session` (SameSite=Strict,
Secure при HTTPS, 30 дней). ID случайный, cookie подписана HMAC ключом из
`CSD_DATA_DIR/.session-key`. Ключ создаётся один раз и сохраняется между запусками.
Нельзя получать его или токен cookie через пользовательский код: по умолчанию
такой код требует контейнер, которому каталог приложения/данных не передаётся.

* **X-User-Id не удостоверяет владельца.** Можно оставить имя в UI как подпись,
  но смена текста не переключает учётную запись. Несколько пользователей демо —
  отдельные браузеры/профили. Одинаковое имя в двух браузерах не объединяет доступ.
* Все `/api/runs/{id}` и вложенные маршруты проверяют владельца, включая
  quality, pivot, apps, checks, try, add_result, export, llm, repeat.
  Чужой/неизвестный ID → 404, пустая БД при этом не создаётся.
* Конфигурации и workspace принадлежат этой же сессии. Переданный `config.user`
  заменяется серверным ID. Cookie хранится в браузере; для своего HTTP-клиента
  нужно сохранить cookie jar, сначала вызвать /api/session.
* Cross-origin запись с несовпадающим Origin → 403. Для отдельного frontend-origin
  пока нет CORS: поддерживается frontend на том же origin.
* Это минимальная модель для живого демо, не регистрация/пароли/OIDC. Нет
  восстановления потерянной сессии, общего доступа, переноса между устройствами.
  Старые прогоны, принадлежавшие произвольным именам X-User-Id, автоматически
  никому не присваиваются: потребуется явная администраторская миграция либо новый прогон.
* Не раздавайте снимки/файлы data через static. Экспорт HTML содержит ссылку на
  текущий origin, она открывает отчёт только в авторизованной сессии владельца.

## 2. Сохранённая конфигурация и повтор — разные операции

| Метод/путь | Назначение |
|---|---|
| GET /api/configs | `{configs: [RunConfig, ...]}` только текущего владельца |
| POST /api/configs | Полный валидный RunConfig; upsert по name; `{ok: true}` |
| GET /api/configs/{name} | `{config: RunConfig}` |
| DELETE /api/configs/{name} | Удаление своей конфигурации |
| POST /api/configs/{name}/run | Новый ID и фоновый запуск сохранённого RunConfig |
| POST /api/runs | Новый ID из присланного RunConfig, сразу запуск |
| POST /api/runs/{id}/repeat | Новый ID, копия конфигурации старого прогона, `rerun_of: old_id` |

Повтор не перезаписывает данные/отчёт предыдущего прогона. Он использует текущий
код workspace/file по сохранённым ссылкам; исходный исполненный код остаётся
в журнале старого прогона. Это не побитовое воспроизведение старой версии кода.
Пустые checks сохраняют прежнее поведение POST /runs: выбираются пять встроенных.
Для явного отсутствия проверок передайте отключённые specs (`enabled: false`).

RunConfig допускает `stage_order`. Зависимости обязательны:
`normalize → compare → facts`, после них `checks` и включённый `llm` в выбранном
порядке. Дубли/пропуски/перестановки с нарушением зависимостей → 422, а не
молчаливый запуск другого порядка. В журнале дополнительно есть внутренние
этапы `prepare` и `store`. Вызовы normalize/compare/facts идут по контракту v1;
объявление произвольного DAG или нескольких повторов одной стадии не поддержано.

Нормализация передаётся **normalize.settings** по JSON schema из PR #5.
Альтернативное поле `normalization` из PR #4 не принимается: возвращается 422,
вместо молчаливого игнорирования пользовательских настроек. Нужно согласовать
форму PR #4 с `/api/normalization/schema` и `/api/normalization/defaults`.

## 3. Код процедур и проверок

Источник ProcedureSource: `kind=builtin|workspace|file|code`, `name`,
для code — строка `code`, для нормализации — `settings`.

* builtin/default для normalize/compare/facts использует pipeline.
* workspace/name читает `<own workspace>/<name>.py` один раз до обработки.
* file/name — существующий `.py` **в workspace владельца**. Можно передать
  относительное `saved.py` или абсолютный путь внутри этого каталога.
  Traversal/symlink в чужую папку/за пределы workspace запрещён.
* code — текст прямо из редактора. Размер до 1 MiB.
* Код не импортируется для чтения метаданных в HTTP-процессе. Метаданные пяти
  builtin CHECK читаются через AST, пользовательский CHECK исполняется только worker.

Чтобы показать/редактировать исходник:
`GET /api/builtin/checks/{id_or_filename}` возвращает `code` и метаданные;
`GET /api/builtin/procedures/{normalize|compare|facts}` возвращает редактируемый
самостоятельный шаблон `code`, исходник встроенной функции `builtin_code` и code_ref.
Workspace CRUD остаётся `/api/workspace[/name]`. Сохранение — атомарно.

PROCEDURE контракты **не изменены**:

```python
normalize(document, *, app_id, source, config) -> Iterable[normalized]
compare(before_rows, after_rows) -> Iterable[comparison]
aggregate(comparison_rows) -> Iterable[facts]
PROCEDURE = {'stage': 'normalize|compare|aggregate', 'entrypoint': run}
```

UI facts соответствует aggregate. Выбранный aggregate реально вызывается один
раз, его результат валидируется и ограничивает pivot/totals/drill-down по PR #5.
Параметры CHECK передаются `CheckSpec.params` и перекрывают `CHECK.params`.

```json
{
  "dataset": "task/samples", "llm_opinion": false,
  "facts": {"kind":"workspace","name":"only_decisions"},
  "checks": [{"source":{"kind":"file","name":"custom.py"},
              "params":{"app_ids_limit":50},"enabled":true}]
}
```

Запуск из редактора:
`POST /api/runs/{id}/checks/try` с `{code, params}` либо
`{source: {kind, name, code?}, params}`. Поддержан старый `{kind,name,code,params}`.
Ответ `{rows, executions, errors, status, persisted: true}`.
Результат и диагностика **сразу сохранены в общем отчёте**. Второй вызов
`/checks/add_result` больше не нужен. Для старого frontend он принимает только
точное повторение уже сохранённых строк как idempotent no-op; новые строки
от клиента отвергаются (422 UNVERIFIED_RESULT).

Не надо считать HTTP 200 с `COMPLETED_WITH_ERRORS` успехом проверки. Показывать
execution.diagnostic и остальные успешные строки. `GET /checks` возвращает
`rows, executions, errors, status`. У строк есть `_check_id`/`_ref` адаптера;
исходная схема CHECK/вердикты PASS/FAIL не изменены. Ошибка исполнения — отдельная
execution со статусом ERROR/TIMEOUT, не выдуманный PASS/FAIL. Метаданные executions
включают код, SHA256, параметры, duration, row_start/row_count.

## 4. Исполнение и ограничения

Встроенные неизменённые проверки — отдельные доверенные subprocess workers.
Пользовательские CHECK и PROCEDURE — один механизм checkrunner с лимитом времени
(по умолчанию 60 с на вызов, `CSD_CODE_TIMEOUT`), вывода 8 MiB, JSON-протоколом и
диагностикой. Импорт пользовательского модуля также ограничен этим запуском.
Одна плохая проверка не прерывает последующие. Плохая нормализация/сравнение
исключает заявку по правилам PR #5; ошибка глобальных facts завершает прогон ошибкой.

По умолчанию пользовательский код требует Docker, без fallback к exec:

```bash
docker build -f checkrunner/Dockerfile -t credit-strategy-checkrunner:local .
```

Контейнер без сети, read-only root/input, uid 65534, cap-drop ALL,
no-new-privileges, 512 MiB RAM, 1 CPU, 64 PID, tmpfs 32 MiB. Только код и нужные
входы/снимок одного прогона; production data/секреты/репозиторий не монтируются.
В контейнер workers добавлен pipeline для выполнения PROCEDURE.

Для **своего доверенного кода на локальном демо** администратор может установить
`CSD_TRUST_LOCAL_CODE=1`. Это отдельный процесс с тайм-аутом/лимитом вывода,
но не защита файлов/сети машины от вредоносного Python. Клиентский JSON не может
включить этот режим. Старый `CSD_TRUST_LOCAL_PROCEDURES=1` действует только на
процедуры для совместимости PR #5. Не включать trust для публичного сервиса.

Корневой Dockerfile теперь копирует api, pipeline, mart, checkrunner, task,
frontend. Обычный app-контейнер не получает Docker socket/CLI: builtin-прогоны
работают, пользовательский Docker-backend требует отдельной настроенной worker-
среды. Автоматически монтировать сокет Docker в публичное приложение нельзя.
Для доверенного демо в контейнере можно явно включить subprocess opt-in; для
недоверенного многопользовательского сервиса требуется отдельный sandbox worker-host.

## 5. Журнал и сохранение

`Stage`: status, started, finished, duration_seconds, completed, total,
code_ref, code_snapshot, code_sha256, message, error.

* prepare охватывает подготовку входа/разрешение процедур до начала normalize;
* normalize/compare progress считает фактически обработанные попытки заявок;
* у facts/store/llm нет искусственного процента, total=null;
* checks прогресс — завершённые executions, включая ошибки;
* окончательный status=done ставится после всех выбранных стадий, включая LLM;
* отсутствие LLM/его сбой → skipped с причиной, не падение анализа;
* ошибка этапа имеет finished/error, последующие pending → skipped;
* code_snapshot пользовательской процедуры не меняется при последующем
  редактировании файла; CHECK executions содержат фактически исполненный текст;
* активная ad-hoc проверка видна в `run.active_check`; итог — в executions.

Run/config/workspace JSON публикуется через atomic replace. Чтение DuckDB
использует действующий MartStore.connection с блокировкой; `.conn` у DuckMart
не вводится. Загрузка DuckDB атомарно заменяет прогон без unlink файла базы.
Запуски выполняются в пуле (по умолчанию 2, `CSD_RUN_WORKERS`), не в event loop.
HTTP `/checks/try` синхронно ждёт worker, но FastAPI выполняет его в threadpool,
другие запросы обслуживаются. Повторная ad-hoc проверка того же прогона → 409.

Поддерживается **один процесс uvicorn**, несколько пользователей/фоновых потоков.
Это не распределённая очередь; несколько uvicorn workers или реплик без общей
координации не поддерживаются. После перезапуска pending/running отмечаются
SERVER_RESTARTED с предложением повторить, не остаются навечно running.

## 6. Ошибки и готовность данных

* 404 NOT_FOUND — чужой/неизвестный ресурс;
* 422 INVALID_INPUT / стандартный FastAPI validation error — неверная конфигурация,
  источник, порядок этапов, настройки, поля/фильтры;
* 403 ORIGIN_DENIED — cross-origin запись;
* 409 CONFLICT, RUN_NOT_READY — данные прогона ещё не готовы или идёт другой try;
* ошибка исполнения — run.status=error либо check status ERROR/TIMEOUT с
  диагностикой, при этом HTTP-запрос мог успешно создать фоновую задачу.

Датасеты веб-API ограничены task/samples, data/RUN-*, workspace владельца и
явно зарегистрированными сервером CSD_DATASET_ROOTS (пути через os.pathsep).
Произвольный каталог файловой системы/чужой workspace не допускается.
Качество входа: `/quality`, run.quality и run.diagnostics из PR #5.
Основная summary/pivot содержит только eligible_apps; total_apps входа и
исключения смотреть в quality. Полностью отсутствующие заявки требуют
expected_app_ids; без внешнего списка их невозможно узнать из файлов.

## 7. Реальные проверки (7 ноября 2026)

На macOS ARM64, Python 3.13.4, DuckDB 2.0.0:

* 86 тестов + 8 subtests прошли; Docker integration test skipped, не засчитан
  как проверка контейнера. Есть regression-тесты чужих ID/подделки cookie,
  config CRUD/run/repeat, workspace/file/code/параметров, отказов/тайм-аутов,
  сохранения ошибок, этапов/снимков и восстановления после рестарта.
* Настоящий uvicorn + HTTP client: demo 300 заявок (около 1.98 с), samples 4
  заявки (0.82 с), полный набор 2002 заявки (28.31 с, включая HTTP polling,
  запрос pivot и экспорт). Проверены HTML и отказ чужому клиенту 404.
* Полный RUN-20260827-0001 извлечён только из выданного ЗАДАНИЕ.zip, организаторский
  приватный эталон не читался. 4004 ответа, eligible=2002, excluded=0,
  changed=502, decision_changed=385, 32 строки пяти проверок, 0 ошибок исполнения.
  Вертикальных фактов 572221. Данные/прогоны/секреты не добавлены в Git.
* Отдельный запуск RunManager полного набора: 21.14 с; normalize 2.83 с,
  compare 2.16 с, facts 1.35 с, store 6.14 с, checks 7.88 с, pivot 0.41 с.
  Это локальные замеры без SLA; внешний LLM не настроен и отмечен unavailable.
* Контейнер **не запускался**: команда docker отсутствует. Добавлен настоящий
  opt-in smoke test, строящий оба образа, проверяющий sandbox и HTTP-прогон:
  `CSD_TEST_DOCKER=1 python -m pytest checkrunner/tests/test_docker_runtime.py -q`.

Полная команда:

```bash
python -m pytest tests pipeline/tests mart/tests checkrunner/tests -q
```

## Согласование

Этот документ — конкретная передача интерфейсов для PR #4/#5. Использован
опубликованный протокол нейросети 1. Подтверждения нейросети 3 о переходе на
normalize.settings, cookie-модель и серверное сохранение /checks/try ещё нет.
Frontend этой веткой не редактировался. При объединении PR #4 нельзя возвращать
старые api/runs.py с exec/.conn/e или старую обработку facts(run, segments):
нужно оставить серверный протокол PR #5 и этой ветки, адаптировать форму UI.
