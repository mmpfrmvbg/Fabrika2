# Fabrika 2.0 — песочница (`proekt/`)

Модуль `factory/` — SQLite + FSM + оркестратор + агенты Фазы 2 (ревьюер, судья, планировщик, архитектор).

## Архитектура (канон)

- **UI:** `factory-os.html` → HTTP → **FastAPI** `factory/api_server.py` → SQLite (`factory/db.py`, DDL в `schema_ddl.py`).
- **Контур:** `wire()` → `Orchestrator` (`orchestrator_core.py`) → **FSM** `fsm.py` с `guards.py` / `actions.py` → агенты (Forge, Review, Judge, Planner, Architect, …).

Глубокий разбор рисков и истории линий кода: **[AUDIT_REPORT.md](AUDIT_REPORT.md)**.

## Legacy (deprecated)

Исторические файлы перенесены в **`factory/legacy/`** — не используются в production, не импортируются из `factory/` и не участвуют в CI:

- `orchestrator_v1.py`, `agents_v1.py`, `dashboard_v1.py` (старый stdlib-дашборд)
- `transitions_v1.sql`, `schema_v1.sql` (старые FSM/DDL; канон — `schema_ddl.py` + `db.py`)

FSM и переходы задаются только через `schema_ddl.py` / seed в `db.py` и таблицу `state_transitions`.

## Документация контракта

- **[docs/PHASE2_AGENT_CONTRACT.md](docs/PHASE2_AGENT_CONTRACT.md)** — поведение агентов и FSM, согласованное с кодом (судья: два события, эскалация `sent_to_judge`, пути архитектора, транзакции, stub планировщика).
- **[docs/AGENT_PROMPT_CONFIG.yaml](docs/AGENT_PROMPT_CONFIG.yaml)** — MVP-конфиг промптов по ролям; отдельный генератор промптов не нужен, пока хватает YAML + § контракта.

Дальше по приоритету: **один полный ручной E2E** (`--e2e-manual`) и проверка трейса в БД; миграции, `build_agent_prompt.py`, dashboard, context snapshots — после этого.

## База данных (dev)

При смене DDL или seed в `factory/schema_ddl.py` допустимо **удалить `factory.db`** и поднять базу с нуля (`init_db`). Миграции для этой песочницы не обязательны на текущей стадии.

## Сбои forge-run (run.failed.*)

После неуспешного `run_qwen_cli` в журнале сущности `run` уточняют причину:

- **`run.failed.account_exhausted`** — ни один аккаунт не доступен (cooldown / исчерпание пула).
- **`run.failed.account_rotation_limit`** — исчерпан лимит итераций ротации без успешного вызова CLI.
- **`run.failed.cli_error`** — ненулевой exit, таймаут, ошибка запуска и т.п. (не квота в смысле «все слоты»).

## Forge: промпт, песочница, реальная кузница

- **`factory/forge_prompt.py`** — сборка текста для Qwen из атома: объявленные файлы, содержимое read/modify из рабочей копии, комментарии, решения (`decisions`), задача.
- **`factory/forge_sandbox.py`** — временная директория, копии файлов, после CLI — diff → `file_changes` и шаги `file_write`. Папка удаляется после прогона; путь на момент работы пишется в `runs.sandbox_ref`.
- **`FACTORY_WORKSPACE_ROOT`** — корень репозитория для чтения исходников (по умолчанию текущий каталог процесса). Запускайте E2E и оркестратор из каталога `proekt/`, чтобы пути вроде `factory/models.py` совпадали с файлами на диске.
- **`FACTORY_QWEN_DRY_RUN=1`** — без subprocess; после «успеха» в песочнице добавляется маркер diff (dry placeholder), чтобы в БД были реальные `file_changes` и `file_write`.
- **Реальный Qwen** (`FACTORY_QWEN_DRY_RUN=0`): CLI вызывается с `cwd` = песочница и полным промптом; ожидается, что инструмент правит файлы в этой директории. Автокоммит git в репозиторий фабрики в этом MVP не делается.
- **Канонический режим для реального редактирования через QWEN-CODE-CLI:** промпт передаётся в **stdin** (`FACTORY_QWEN_PROMPT_VIA=stdin`, значение по умолчанию в `factory/qwen_cli_runner.py` — вызов вида `qwen … -p -`). Режим argv не считается основным для forge из‑за длины промпта и лимита командной строки на Windows. Зафиксировано в [docs/PHASE2_AGENT_CONTRACT.md](docs/PHASE2_AGENT_CONTRACT.md) (раздел 7).
- **MVP-контур с внешним агентом:** при зелёном `python -m factory --e2e-qwen-wet-edit` с `FACTORY_QWEN_DRY_RUN=0` подтверждены реальный diff в `file_changes` и прохождение атома до `done` (задача → judge → forge → Qwen меняет файл → артефакт → review).

### Дальше (устойчивость real-run)

Реализовано: ``--e2e-qwen-wet-failover`` (ротация после симулированного 429), ``--e2e-qwen-wet-forge-no-artifact`` (успех CLI без diff → ``run.failed.forge_no_artifact``). Общий штатный путь подготовки атома до ``ready_for_work``: ``factory/e2e_qwen_wet_shared.drive_wet_hello_atom_to_ready_for_work`` (используется всеми тремя wet-E2E).

## Tests (E2E)

Из каталога `proekt/` (нужен `.env` с `FACTORY_API_KEY_1`):

```bash
python -m factory --e2e-manual
python -m factory --e2e-qwen-dry
python -m factory --e2e-two-atoms
# только локально, FACTORY_QWEN_DRY_RUN=0:
python -m factory --e2e-qwen-wet-edit
python -m factory --e2e-qwen-wet-failover
python -m factory --e2e-qwen-wet-forge-no-artifact
```

- **`--e2e-manual`** — happy path: vision → epic → atom → judge → forge-worker (`qwen_cli_runner`, по умолчанию dry-run) → review → `done`. БД `factory_e2e_manual.db`, `assert_happy_atom()` / `assert_trace_integrity()`.
- **`--e2e-qwen-dry`** — тот же маршрут во временной БД + контракт с раннером и песочницей (`qwen.run.invocation`, `qwen_cli_runner`, шаги `file_write`, `file_changes`). `FACTORY_QWEN_DRY_RUN=1` в CI. Для прогона с настоящим бинарником: `FACTORY_QWEN_DRY_RUN=0` и тот же флаг из каталога `proekt/`.
- **`--e2e-qwen-wet-edit`** — отдельный атом с одним файлом `factory/hello_qwen.py` и прямой задачей на правку; **только при `FACTORY_QWEN_DRY_RUN=0`**. Проверяет, что в БД есть `file_changes` (modify) и `file_write` по этому пути. Нужен реальный `qwen`, запуск из `proekt/`. Промпт в stdin — канон по умолчанию; при сбоях см. `.env.example`: `FACTORY_QWEN_EXTRA_ARGS`, `FACTORY_QWEN_MAX_SESSION_TURNS`, `FACTORY_QWEN_DEBUG=1`.
- **`--e2e-qwen-wet-failover`** — тот же сценарий, что wet-edit, но первый вызов CLI в раннере симулирует rate limit (`FACTORY_QWEN_E2E_SIMULATE_RATE_LIMIT_ON_FIRST_CALL`); второй аккаунт реально вызывает Qwen. Нужны **минимум два** API-аккаунта и `FACTORY_QWEN_DRY_RUN=0`.
- **`--e2e-qwen-wet-forge-no-artifact`** — wet без изменения объявленных modify-файлов после «успешного» CLI: ожидается `forge_failed`, событие `run.failed.forge_no_artifact`, атом в `ready_for_work`. Использует E2E-хук без реального Qwen (см. `FACTORY_QWEN_E2E_SIMULATE_OK_NO_FILE_CHANGE`).
- **`--e2e-two-atoms`** — один epic, два атома: первый до `done`, второй с принудительным отказом ревью / эскалация. БД `factory_e2e_two_atoms.db`.

В корневом CI job `factory-e2e` сценарии из workflow должны проходить без `AssertionError`.

## CLI

Из каталога `proekt/` (нужен `.env` с `FACTORY_API_KEY_1`):

```bash
python -m factory --demo
python -m factory --run
python -m factory --e2e-golden
python -m factory --e2e-chain
python -m factory --e2e-manual
python -m factory --e2e-qwen-dry
python -m factory --e2e-live
python -m factory --e2e-two-atoms
# локально, см. wet-E2E в Tests (E2E)
```

- `--e2e-golden` — минимальный путь: атом уже в `in_review` → один `tick()` → `done`.
- `--e2e-chain` — цепочка: `ready_for_judge` → судья → кузница (`forge_started`) → вызов `forge_completed` из сценария → ревьюер → `done` (временная БД; реальная Кузница не нужна).
- **`--e2e-manual`** — см. **Tests (E2E)**; SQL для отладки — в докстринге `factory/e2e_manual_trace.py`.
- **`--e2e-qwen-dry`** — см. **Tests (E2E)** и блок про сбои forge-run выше.
- **`--e2e-live`** — см. **Tests (E2E)** (по умолчанию dry; smoke при wet).
- **`--e2e-qwen-wet-edit`** — см. **Tests (E2E)** (только `FACTORY_QWEN_DRY_RUN=0`).
- **`--e2e-qwen-wet-failover`** / **`--e2e-qwen-wet-forge-no-artifact`** — см. **Tests (E2E)**.
- **`--e2e-two-atoms`** — см. **Tests (E2E)** выше.
