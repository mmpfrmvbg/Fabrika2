# Factory OS — аудит `Fabrika2.0/proekt`

Дата: 2026-04-01. Режим: только чтение кода и проверки; правки кода не вносились (кроме временного скрипта проверки orphan-запросов, удалён после запуска).

**Важно:** в проекте **две линии** — каноническая **`factory/`** ( `wire()` → `Orchestrator` в `orchestrator_core.py`, `StateMachine` в `fsm.py`, `Guards`/`Actions` в `guards.py`/`actions.py`, DDL в `schema_ddl.py` + миграции в `db.py` ) и **legacy** **`factory_orchestrator_v1.py` + `factory_transitions_v1.sql`**. Ниже явно указано, к какой линии относится находка.

---

## Раздел 1. FSM корректность

### 1.1 Граф для `kind = atom` (по `factory_transitions_v1.sql`)

Все тройки `(from_state, event_name, to_state)` для строк с `kind_filter = 'atom'`:

| ID | from_state | event | to_state |
|----|------------|--------|----------|
| t10 | draft | atom_specified | planned |
| t11 | planned | send_to_judge | ready_for_judge |
| t12 | ready_for_judge | judge_approved | ready_for_work |
| t13 | ready_for_judge | judge_rejected | judge_rejected |
| t14 | ready_for_work | forge_started | in_progress |
| t15 | in_progress | forge_completed | in_review |
| t16 | in_progress | forge_failed | ready_for_work |
| t17 | in_progress | forge_failed | blocked |
| t18 | in_review | review_passed | done |
| t19 | in_review | review_failed | review_rejected |
| t20 | review_rejected | judge_reviewed_rejection | ready_for_work |
| t21 | review_rejected | judge_escalated | blocked |

Плюс универсальные правила с `kind_filter NULL` и `from_state = '*'` (t40–t43) применяются и к атомам, если совпадают `event` и guard.

- **Тупики:** терминальные `done`, `cancelled`, `archived`, `blocked` (из t17/t21) — исходящих FSM-переходов нет (ожидаемо). Промежуточные состояния имеют исходящие рёбра; «застрять» можно из‑за отсутствия внешних событий (например, никто не вызвал `send_to_judge`), это не тупик графа, а отсутствие триггера.
- **Недостижимость:** из корректного начального `draft` достижимы все статусы при наличии событий; обратно из `done`/`cancelled`/`archived` обычно нельзя без отдельных правил.
- **Цикл forge → review → reject → forge:** да, возможен по t15 → t19 → t20 → t14. **Ограничения:** счётчики `forge_attempts` / `review_rejections` / `judge_rejections` и переходы t16/t17/t21 (`guard_under_retry_limit` / `guard_over_retry_limit`, `blocked`), а также качество ревью/судьи в коде агентов.

### 1.2 Guards (legacy `factory_orchestrator_v1.py`)

- В SQL указаны имена вида **`guard_has_children`**, **`guard_has_files_declared`**, …
- В `Guards.get()` регистрируются методы **`has_children`**, **`has_files_declared`**, … **без префикса `guard_`**.
- В `TransitionEngine.find_transition`: если `Guards.get(name)` вернул `None`, проверка guard **пропускается**, и переход считается валидным.

**[CRITICAL] [Legacy]** Несовпадение имён SQL и реестра → guards для затронутых переходов **фактически отключены** (логика безопасности FSM обходится).

### 1.3 Universal transitions (`*`)

- В `find_transition` wildcard задан как `t["from_state"] == "*"`, согласовано с SQL.
- **`dependency_blocked` при `in_progress`:** универсальное правило t40 переведёт в `blocked` при `guard_has_unresolved_dep`. Разблокировка — t41. В legacy `Actions.save_resume_state` сохраняет текущий статус; `restore_from_block` восстанавливает. **Файловые локи:** `dependency_blocked` сам по себе их не снимает; снятие завязано на другие действия (`release_locks` в ветках forge/review/done). Риск «залипших» локов при блокировке по зависимости — см. раздел 5.

### 1.4 t08 и parent в `planned`

- t08: `from_state = ready_for_work`, событие `all_children_done` → `done`.
- **`[HIGH] [Legacy]`** Если родитель остаётся в `planned` (или ином статусе ≠ `ready_for_work`), пока все дети стали `done`, автоматический rollup через t08 **не сработает**. `_process_parent_completions()` выбирает родителей с любыми статусами кроме done/cancelled/archived и шлёт `all_children_done`, но **подходящее правило есть только для `ready_for_work`**, поэтому родитель в `planned` может **никогда не перейти в `done`**.

**[OK] [Production `factory/`]** Rollup идёт через `completion_inbox` и событие `parent_complete` (см. `migrate_schema` в `db.py`, `Orchestrator._dispatch_completion`), а не через обязательность статуса `ready_for_work` из t08.

### 1.5 t04 и `action_enqueue_children`

- В SQL: `action_enqueue_children`.
- В `Actions` legacy **нет** метода `enqueue_children` → в реестре нет `action_enqueue_children` → post-action **не выполняется**.

**[HIGH] [Legacy]** После `judge_approved` для vision с атомами дети **не ставятся в очередь** этим действием (если используется только legacy orchestrator и этот seed).

---

## Раздел 2. Целостность данных и схема

### 2.1 Foreign keys

- **`factory/db.py`:** `get_connection`, `ensure_schema` — `PRAGMA foreign_keys = ON`.
- **`factory_orchestrator_v1.py`:** `connect()` — `PRAGMA foreign_keys=ON`.
- **`factory/api_server.py`:** `_open_rw()` → `get_connection` — OK. **`_open_ro()`** — URI `mode=ro`, **явного `foreign_keys`** нет; для read-only это влияет мало.

**[LOW]** Точки без FK pragma: read-only URI в api_server (не критично для чтения).

### 2.2 Транзакции и атомарность

- **`TransitionEngine.apply`:** commit/rollback — OK.
- **`ForgeAgent.execute` (legacy `factory_agents_v1.py`):** много отдельных `commit()` после записи файлов и `file_changes`; при сбое до `submit_event` возможен **рассинхрон** «файл записан, статус не обновлён». **[HIGH]**
- **`get_or_execute_step` (legacy):** при падении `execute_fn` шаг помечается `failed` и исключение пробрасывается; при повторном вызове снова вставляется `started` через `INSERT OR REPLACE` — повторная попытка возможна. **[OK]** с оговоркой по идемпотентности внешних вызовов.

### 2.3 Orphan-запросы (выполнено на свежей БД после `init_db`)

```
wiq orphan: 0
runs orphan wi: 0
locks open wi done: 0
event orphan wi: 0
steps orphan run: 0
```

**[OK]** на чистой схеме нарушений нет.

### 2.4 CHECK и FSM

- **`factory_schema_v1.sql`** (отдельный документ) задаёт расширенный набор `status`/`kind`.
- **Рабочая схема** — **`factory/schema_ddl.py`**: другой набор колонок (например `retry_count` vs `forge_attempts` в старом файле). Нельзя смешивать DDL файлы без миграции.

**[MEDIUM]** Два DDL (`factory_schema_v1.sql` vs `schema_ddl.py`) — риск путаницы при ручном применении «не того» файла.

### 2.5 Индексы

- `idx_wi_parent`, `idx_wi_root`, `(kind, status)` в старой схеме; в `schema_ddl.py` — свои индексы. Запросы с `parent_id`, `root_id` покрыты; составной **`(root_id, kind)`** как отдельного индекса может отсутствовать — **[MEDIUM]** при очень больших деревьях возможны лишние сканы (зависит от планов SQLite).

---

## Раздел 3. Concurrency и SQLite

### 3.1 Подключения

- Основные write-пути: `factory/db.py:get_connection` (WAL, busy_timeout 15000, wal_autocheckpoint 100).
- Много тестов и утилит вызывают `sqlite3.connect` напрямую — **[LOW]** дублирование настроек.
- Пуллинга нет: **новое соединение на запрос** в типичном REST-потоке api_server (read-only).

### 3.2 DDL

- `ensure_schema` использует **advisory file lock** (`*.migrate.lock`) и затем DDL — **[OK]** снижает гонки при параллельном старте.

### 3.3 Запись из API и оркестратора

- WAL + busy_timeout; конкурирующие писатели ждут или получают `database is locked`. **[OK]** при типичных нагрузках; при длительных write — задержки.

### 3.4 Lease

- `lease_work`: `SELECT … LIMIT 1` затем `UPDATE` **без** уникального условия «если всё ещё lease_owner IS NULL» в одном SQL → при двух воркерах теоретически возможна гонка. Для одного процесса оркестратора — **[OK]**. Для нескольких процессов — **[MEDIUM/HIGH]**.

---

## Раздел 4. Безопасность

### 4.1 SQL injection

- **api_server:** запросы собираются с `?` параметрами; динамика — без конкатенации пользовательских строк в опасные места (кроме контролируемых фрагментов WHERE в других модулях — см. ниже).

**[MEDIUM]** В `factory/dashboard_unified_journal.py` и др. есть сбор SQL через f-string с **параметризованными** хвостами — нужно сохранять дисциплину, чтобы туда не попали сырые пользовательские строки.

### 4.2 Path traversal

- **`ForgeAgent` (`factory_agents_v1.py`):** `self.workspace / path` без нормализации и проверки границы workspace.

**[HIGH]** LLM может вернуть `../` пути → запись вне workspace.

### 4.3 API authentication

- **Нет аутентификации** на `POST /api/work-items/{id}/run`, `POST /api/visions`, `POST /api/improvements/...`, `POST /api/orchestrator/*`.

**[HIGH]** Любой, кто достучался до порта, может запускать сценарии и писать в БД (где разрешено).

- **CORS в `api_server`:** `allow_origin_regex` для localhost — **не** `*`.

### 4.4 Prompt injection / reviewer

- Описания задач попадают в промпты без жёсткой санитизации — **[MEDIUM]** ожидаемо для LLM-систем; mitigations — политики и доверенная среда.
- **Reviewer** в legacy вызывает `pytest` / `py_compile` с `cwd=workspace` — не полноценный sandbox (как и в большинстве CI).

### 4.5 HTML (factory-os.html)

- Используется **`escapeHtml`** для пользовательских строк в основных шаблонах.

**[OK]** с оговоркой: при добавлении новых фрагментов нужно сохранять экранирование.

---

## Раздел 5. Ошибки и восстановление

### 5.1 Orchestrator

- **Legacy:** при исключении в `apply` — rollback. При crash процесса до commit — откат последней транзакции.
- **`_recover_on_startup`:** сброс протухших lease, пометка stuck runs, освобождение истёкших file_locks по `expires_at`.

**[MEDIUM]** Не все аномалии (например `in_progress` без run) покрыты явной матрицей recovery в legacy; в **`factory/orchestrator_core.py`** логика богаче.

### 5.2 Агенты

- **`run_once`:** при ошибке после `lease_work` вызывается `release_lease`; если `release_lease` упадёт, lease может зависнуть до recovery — **[LOW]**.
- **Пустой вывод LLM (`_parse_file_outputs`):** переход в `forge_completed` возможен без файлов — **[MEDIUM]** (в production пайплайне частично компенсируется `forge_worker` / проверками).

### 5.3 Журнал событий

- Не каждое побочное действие в legacy `Actions` пишет в `event_log` (например часть только меняет строки).

**[MEDIUM]** Полнота доменного журнала зависит от того, используется ли `FactoryLogger` (production) или `emit_event` (legacy).

---

## Раздел 6. Поверхность API

### 6.1 Дубликаты: FastAPI vs `factory_dashboard_v1.py`

- **FastAPI** (`factory/api_server.py`, порт по умолчанию 8000): полный набор `/api/*`, аналитика, journal, improvements.
- **stdlib dashboard** (`factory_dashboard_v1.py`, порт 8420): другой набор путей (`/api/tree` возвращает **массив корней**, не `{"roots":…}`), другая форма JSON.

**[MEDIUM]** Два сервера — риск рассинхрона контрактов; для UI рекомендуется один источник.

### 6.2 Ошибки

- FastAPI: `HTTPException` с разными `detail`; единого JSON-формата ошибок нет.

### 6.3 Контракт

- **`GET /api/work-items/tree`** возвращает `{"tree": [...]}` — структура задаётся `build_work_items_tree`.
- **OpenAPI:** доступна автогенерация FastAPI (`/docs` при включении).

### 6.4 Порядок маршрутов

- В `api_server.py` явный комментарий: `/api/work-items/tree` объявлен **до** `/api/work-items/{wi_id}` — **[OK]**.

---

## Раздел 7. UI и API (factory-os.html)

### 7.1 Mock vs live

- При отсутствии `FACTORY_API_BASE` / ошибке загрузки показывается **MOCK** / офлайн-состояние; данные подгружаются через `loadFactorySnapshot`, journal, stats, tree.

### 7.2 Polling

- **`FACTORY_POLL_MS`** по умолчанию **5000** мс; при ошибке в `refreshLiveData` — только `console.warn` и баннер, **без exponential backoff**.

### 7.3 POST

- Запуск forge: совместимость **`POST /api/tasks/{id}/forge-run`** и **`POST /api/work-items/{id}/run`** (реализовано).

---

## Раздел 8. Тесты

### 8.1 Прогон

```
python -m unittest discover -v -s factory/tests
```

Результат: **61 тест за ~74 с, все пройдены (OK)**.  
(Код выхода оболочки мог быть ненулевым из‑за stderr в PowerShell — на результат тестов не влияет.)

### 8.2 Покрытие

- Есть интеграционные тесты API, оркестратора, forge/review/judge, concurrent init, analytics.
- **`factory_orchestrator_v1.py`** и **`factory_agents_v1.py`** как **standalone** почти не покрыты автотестами проекта.

### 8.3 Пробелы

- Нет целевых тестов на **path traversal** в forge.
- Нет тестов на **двухпроцессный lease** на одной очереди.
- Legacy FSM + seed из `factory_transitions_v1.sql` **не** верифицируется end-to-end в CI.

---

## Раздел 9. Мёртвый код и дублирование

### 9.1 Файлы

- **`factory_dashboard_v1.py`:** альтернативный дашборд; production UI — **`factory-os.html` + api_server**.
- **`factory_orchestrator_v1.py` / `factory_agents_v1.py`:** по смыслу **legacy/демо**; живой пайплайн — пакет **`factory/`**.

### 9.2 Имена в SQL vs код

- Множество **`action_*` / `guard_*` в `factory_transitions_v1.sql`** не сопоставлены с legacy Python — см. раздел 1.

### 9.3 Дублирование утилит

- **`gen_id`:** `factory_orchestrator_v1.py` и `factory/db.py` (разные форматы префиксов).
- **`emit_event`:** только в legacy orchestrator; в production — `FactoryLogger`.

### 9.4 Схема

- **`initiative`:** используется в моделях и FSM seed в `schema_ddl.py`.
- **`prompt_versions` / `artifacts`:** таблицы есть; запись в `prompt_versions` из HR-стаба в `factory_agents_v1.py` — чтение; массовой записи в production-коде не найдено.
- **`context_snapshots`:** используется в legacy `action_build_judge_context` и концептуально в judge — **[OK]** частично.

---

## Раздел 10. Производительность

### 10.1 N+1

- **`BaseAgentAdapter._build_context`:** множество SELECT на один `wi_id` — **[MEDIUM]**.
- **`build_work_items_tree`:** в памяти строит дерево — при 10k+ узлов возможны затраты памяти/времени — **[MEDIUM]**.

### 10.2 Небounded

- **`/api/events`:** `limit` с `le=500` — **[OK]**.
- **`factory_dashboard_v1.api_events`:** `limit` из query без верхней границы по умолчанию 200 — при ручном завышении — **[LOW]**.

### 10.3 Индексы

- См. 2.5; тяжёлые отчёты по `event_log` используют индексы по времени/work_item_id.

### 10.4 WAL checkpoint

- `wal_autocheckpoint` задан в `get_connection`; явный `PRAGMA wal_checkpoint` в коде не искался как обязательный — **[LOW]** для длительных write-нагрузок может понадобиться обслуживание.

---

## Раздел 11. Конфигурация и деплой

### 11.1 Переменные окружения

- **`FACTORY_DB` / `FACTORY_DB_PATH`**, **`FACTORY_API_PORT`**, **`FACTORY_TICK_INTERVAL`**, **`FACTORY_ORCHESTRATOR_ASYNC`**, **`FACTORY_INTROSPECT_TICKS`**, ключи аккаунтов **`FACTORY_API_KEY_*`**, **`FACTORY_QWEN_*`** и др. — см. `factory/config.py` и `.env.example`.

### 11.2 CLI

- **`python -m factory`** → `factory/cli.py`: команды статуса, дашборда, seed и т.д. Команды получают своё соединение через `wire()` по необходимости.

### 11.3 Зависимости

- **`requirements.txt`:** pydantic, fastapi, uvicorn, httpx — диапазоны версий указаны; pytest в prod-файле не закреплён (тесты через stdlib unittest).

---

## Раздел 12. Документация

### 12.1 QUICKSTART.md

- Команды с **`--port 8020`** согласованы с явным указанием порта; дефолт API — **8000** без аргумента.

**[LOW]** Пользователь может перепутать дефолт порта, если не читает help.

### 12.2 Docstrings

- Заголовок **`factory_orchestrator_v1.py`** описывает single-writer и recovery — частично совпадает с тем, как устроен **`factory/orchestrator_core.py`**, но это разные реализации.

- Утверждение в **`factory_agents_v1.py`** «агенты не меняют статус напрямую» — **нарушается** прямыми `INSERT`/`UPDATE` в `PlannerAgent`, `ArchitectAgent`, `HRAgent` (создание work items, комментарии и т.д.) — статусы новых сущностей задаются SQL. Для **существующего** work item переходы идут через `submit_event` — частично верно.

**[MEDIUM]** Неточность документации относительно фактических мутаций.

### 12.3 Схема: run_steps vs event_log

- В DDL прокомментировано разделение; в production **`FactoryLogger`** пишет доменные события; технические шаги — в `run_steps`. Смешение минимально, но возможны дубли по смыслу — **[LOW]**.

---

# Итоговый отчёт

## 1. Executive Summary

Каноническая реализация **`factory/`** (FSM с `Guards.resolve`/`Actions.resolve`, оркестратор, API, тесты) выглядит согласованной и покрытой тестами. Параллельно существуют **legacy** **`factory_orchestrator_v1.py`** и **`factory_transitions_v1.sql`**, где **имена guard/action не совпадают с кодом**, что приводит к **тихому отключению guard’ов и пропуску действий** — это главный архитектурный риск, если эту линию ещё запускают. Отдельно выделяются **отсутствие аутентификации API** и **path traversal в legacy ForgeAgent**.

**Оценка находок:** Критических **3**, Высоких **~6**, Средних **~12**, Низких **~8** (часть пересекается между legacy и production).

## 2. Findings Table

| # | Severity | Section | Finding | Fix complexity |
|---|----------|---------|---------|----------------|
| 1 | CRITICAL | 1 / Legacy | Имена `guard_*` в SQL не совпадают с реестром `Guards` → guards не вызываются | Низкая (префикс/алиасы или правка SQL) |
| 2 | CRITICAL | 1 / Legacy | Отсутствуют `action_enqueue_children`, `action_return_to_planner`, `action_log_abandonment`, `action_cleanup_locks` — побочные эффекты не выполняются | Средняя |
| 3 | CRITICAL | 4 / API | Нет аутентификации на опасных POST-эндпоинтах | Средняя–высокая |
| 4 | HIGH | 1 / Legacy | t08 только из `ready_for_work` — родитель в `planned` с готовыми детьми может не завершиться | Средняя |
| 5 | HIGH | 2 / Legacy | Много мелких commit в ForgeAgent — рассинхрон файлов и БД | Средняя |
| 6 | HIGH | 4 | Path traversal в `ForgeAgent._write_file` | Низкая |
| 7 | HIGH | 9 | Две параллельные кодовые базы оркестратора/агентов путают аудит и деплой | Документация / удаление legacy |
| 8 | MEDIUM | 3 | `lease_work` не атомарен между процессами | Средняя |
| 9 | MEDIUM | 9 | Два DDL (`factory_schema_v1.sql` vs `schema_ddl.py`) | Документация |
| 10 | MEDIUM | 8 | Legacy модули без тестов | Тесты или deprecation |
| 11 | MEDIUM | 10 | N+1 в `_build_context` | Средняя |
| 12 | LOW | 2 | RO API без `PRAGMA foreign_keys` | Тривиально |
| 13 | LOW | 11 | Дефолт порта API 8000 vs пример 8020 в QUICKSTART | Документация |

## 3. Top 5 Risks

1. **Legacy FSM с «немыми» guards/actions** — неверное поведение при использовании `factory_orchestrator_v1` + SQL seed.
2. **Открытый write API** на localhost/сети без auth — произвольные vision/run/improvements.
3. **Path traversal в forge** — запись вне workspace.
4. **Рассинхрон файловой системы и БД** в legacy Forge при частичных сбоях.
5. **Два HTTP-дашборда** с разными JSON-контрактами — ошибки интеграции.

## 4. Top 5 Quick Wins

1. Пометить **`factory_orchestrator_v1.py`** / **`factory_transitions_v1.sql`** как deprecated в README или удалить из путей запуска.
2. В **`Guards.get`** (legacy) резолвить и `has_children`, и `guard_has_children` (алиас) — мало строк, снимает CRITICAL при сохранении SQL.
3. Добавить **`resolve()`** нормализацию путей в `Path.resolve()` относительно workspace с проверкой `relative_to`.
4. Включить **токен/API key** для POST в FastAPI (один env) — минимальный gate.
5. Зафиксировать в QUICKSTART **один порт** по умолчанию или явно «пример».

## 5. Architecture Diagram (текстовый)

```
[factory-os.html] --HTTP--> [FastAPI factory.api_server]
                                |-- read: sqlite ?mode=ro
                                |-- write: get_connection (WAL)
                                +-- bg thread: wire().orchestrator.tick()

[CLI / composition wire()] --> init_db --> [SQLite WAL]
                      --> StateMachine(state_transitions)
                      --> Orchestrator.tick --> queues --> forge/review/judge agents

Legacy (отдельно):
factory_orchestrator_v1.Orchestrator + TransitionEngine  --> тот же SQLite только если вручную;
factory_agents_v1.*  --> требует импортов из orchestrator (сейчас закомментирован) — модуль не самодостаточен
```

## 6. Recommended Fix Order

1. Не использовать legacy loop в проде **или** починить соответствие имён guard/action SQL↔Python.
2. Закрыть POST API (auth/reverse proxy) для любых не-localhost деплоев.
3. Нормализация путей forge.
4. Укрупнить транзакции в legacy Forge или перейти на production `forge_worker`.
5. Свести дашборды к одному серверу и контракту.

## 7. Dead Code for Removal (кандидаты)

- **`factory_dashboard_v1.py`** — если везде используется `factory-os.html` + api_server.
- **`factory_orchestrator_v1.py`**, **`factory_agents_v1.py`** — при полном переходе на `factory/` и отсутствии внешних вызовов.
- **`factory_schema_v1.sql`** — если ни одна инсталляция не применяет его как источник истины (проверить перед удалением).

## 8. Test Gaps

- E2E: legacy `TransitionEngine` + seed из `factory_transitions_v1.sql`.
- Безопасность: path traversal, отсутствие auth на API.
- Concurrency: два процесса, один lease queue.
- Recovery: `in_progress` без активного run, явная матрица.

---

## Post-fix status (2026-04-01)

После аудита выполнена изоляция legacy, правки production и документация.

**Закрыто переносом в `factory/legacy/` (не используется в сборке/тестах):**

- П. 1–2, 4–5, 7 (часть), 9 (два DDL как «второй файл»), 10 (legacy без тестов) — относились к `factory_orchestrator_v1.py`, `factory_agents_v1.py`, `factory_transitions_v1.sql`, `factory_schema_v1.sql`, `factory_dashboard_v1.py`; теперь в `factory/legacy/` с пометкой DEPRECATED.

**Закрыто правками production:**

- **П. 3 (API auth):** `FACTORY_API_KEY` + заголовок `X-API-Key` на мутирующие эндпоинты в `factory/api_server.py`; без ключа — dev-режим.
- **П. 6 (path traversal):** `safe_path_under_workspace` в `factory/forge_sandbox.py` для путей из БД; юнит-тест на `../outside.txt`.
- **П. 8 (lease race):** `_process_queue` в `orchestrator_core.py` — атомарный `UPDATE … WHERE rowid=(SELECT … LIMIT 1) RETURNING`; при ошибке handler сброс lease.
- **П. 11 (N+1 контекста):** `resolve_task_context` в `task_context.py` переведён на один рекурсивный CTE вместо цикла SELECT.
- **П. 12 (RO + FK):** `get_connection(..., read_only=True)` в `db.py` с `PRAGMA foreign_keys=ON`; `_open_ro` и `dashboard_api._connect` используют helper.

**Известные ограничения (сознательно не трогали или вне scope):**

- Два HTTP-контракта: stdlib-дашборд остаётся только как файл в `factory/legacy/dashboard_v1.py` — рассинхрон с FastAPI не устранялся кодом UI.
- Prompt injection / полный sandbox для reviewer — как в аудите, mitigations на уровне среды.
- Двухпроцессный stress-тест lease и E2E legacy FSM — по-прежнему нет отдельных тестов; production lease очереди усилен атомарным UPDATE.
- Индекс `(root_id, kind)` и прочие perf-задачи §2.5 / §10 — не добавлялись без профилирования.

---

## Приложение: соответствие разделам промпта

| Раздел | Итог |
|--------|------|
| 1 FSM | Находки по legacy + **[OK]** по production `fsm.py` |
| 2 Data | **[OK]** FK в write; orphan 0 на чистой БД |
| 3 SQLite | **[OK]** WAL/timeout; оговорки по multi-writer lease |
| 4 Security | **[HIGH]** auth + path + LLM |
| 5 Errors | **[MEDIUM]** частичное recovery |
| 6 API | **[OK]** маршруты; **[MEDIUM]** дубли серверов |
| 7 UI | **[OK]** escapeHtml; polling без backoff |
| 8 Tests | 64 passed (после post-fix) |
| 9 Dead code | legacy слой |
| 10 Perf | N+1, дерево в памяти |
| 11 Config | env в config.py |
| 12 Docs | мелкие расхождения |
