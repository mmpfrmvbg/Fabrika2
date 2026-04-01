# Fabrika 2.0 (proekt) — Quickstart (real LLM)

## Prerequisites
- Python 3.11+ (project uses stdlib `unittest`)
- Node-installed `qwen` CLI available in PATH (`where qwen` should work on Windows)

## Environment
Create or edit `Fabrika2.0/proekt/.env`:
- `FACTORY_QWEN_DRY_RUN=0`
- `FACTORY_QWEN_BIN=qwen`
- `FACTORY_QWEN_PROMPT_VIA=stdin`
- `FACTORY_QWEN_MAX_SESSION_TURNS=25`
- `FACTORY_QWEN_TIMEOUT_SEC=600`
- `FACTORY_QWEN_SUBPROCESS_KEY_ENV=OPENAI_API_KEY` (recommended; runner injects per-account token into subprocess env)

Provide accounts in one of two ways:
- **OAuth tokens (recommended)**: put files in repo root `.qwen/`:
  - `.qwen/oauth_creds.json`
  - `.qwen/oauth_creds_2.json`
  - `.qwen/oauth_creds_3.json`
  Each must contain `access_token`.
- **Explicit keys**: set `FACTORY_API_KEY_1..3` in `.env`.

## Verify LLM access (smoke)
Run from `Fabrika2.0/proekt`:

```bash
python -m factory.verify_qwen_accounts
```

It checks OAuth files, AccountManager rotation, and does a short `qwen` CLI call per token.

## Run the factory (API + orchestrator)
From `Fabrika2.0/proekt`:

```bash
python -m factory.api_server
```

По умолчанию порт **8000** (`FACTORY_API_PORT`, хост `FACTORY_API_HOST`). Пример с явным портом: `python -m factory.api_server --port 8000`.

The server exposes:
- `POST /api/visions` (creates Vision and runs planner immediately)
- background orchestrator tick loop (interval `FACTORY_TICK_INTERVAL`, default 3s)

### API key (mutating endpoints)

Если задана переменная **`FACTORY_API_KEY`**, все **POST** (и прочие мутирующие вызовы), включая `POST /api/visions`, `POST /api/work-items/{id}/run`, `POST /api/orchestrator/*`, `POST /api/improvements/*`, требуют заголовок **`X-API-Key`** с тем же значением. **GET** остаются без ключа (удобно для локального дашборда). Если `FACTORY_API_KEY` не задан — режим разработки без auth.

Пример:

```bash
set FACTORY_API_KEY=mysecret
curl -s -H "X-API-Key: mysecret" -H "Content-Type: application/json" -d "{\"title\":\"T\",\"description\":\"D\"}" http://127.0.0.1:8000/api/visions
```

## Create your first Vision

```bash
powershell -NoProfile -Command "Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/visions' -ContentType 'application/json' -Body (@{ title='Hello Factory'; description='Создать Python-скрипт hello.py, который выводит ''Hello, Factory!'' и текущую дату' } | ConvertTo-Json) | ConvertTo-Json -Depth 6"
```

При включённом `FACTORY_API_KEY` добавьте к вызову заголовок `X-API-Key`, например: `-Headers @{ 'X-API-Key' = $env:FACTORY_API_KEY }`. В `factory-os.html` задайте `window.FACTORY_API_KEY` тем же значением, иначе кнопки «New Vision», «▶ Run» и оркестратор вернут 401/403.

### Локальный UI без реального LLM (planner / forge)

Для проверки цепочки Vision → декомпозиция → атом в очереди → ручной Forge без вызова `qwen` установите **`FACTORY_QWEN_DRY_RUN=1`**. Планировщик подставит детерминированное дерево (несколько epic/story/atom); Forge при необходимости также может работать в dry-run в зависимости от остальных переменных окружения.

## Autonomous Mode (API + worker-ы)

Оркестратор в `api_server` на каждом tick подставляет в `forge_inbox` атомы в `ready_for_work`, у которых ещё нет строки в `work_item_queue`. Отдельные процессы **`python -m factory.worker`** атомарно берут строки очереди (lease), выполняют тот же путь, что и tick: `forge_started` → `run_forge_queued_runs` → review → judge. Ручной `POST /api/work-items/{id}/run` не обязателен.

Переменные worker: `FACTORY_WORKER_ID`, `FACTORY_WORKER_POLL` (секунды простоя при пустой очереди), `FACTORY_DB` / `FACTORY_DB_PATH`, `FACTORY_QWEN_DRY_RUN=1` для тестов. В worker выставляется `FACTORY_ORCHESTRATOR_ASYNC=0`, чтобы forge/review шли синхронно в процессе worker.

**Терминал 1 — API + orchestrator tick**

```bash
cd Fabrika2.0/proekt
set FACTORY_QWEN_DRY_RUN=1
python -m factory.api_server
```

**Терминал 2 — Worker 1**

```bash
cd Fabrika2.0/proekt
set FACTORY_QWEN_DRY_RUN=1
python -m factory.worker --id worker-1
```

**Терминал 3 — Worker 2 (опционально)**

```bash
cd Fabrika2.0/proekt
set FACTORY_QWEN_DRY_RUN=1
python -m factory.worker --id worker-2 --poll 3
```

**Терминал 4 — Vision**

```bash
curl -X POST http://localhost:8000/api/visions -H "Content-Type: application/json" -d "{\"title\":\"Calculator\",\"description\":\"Create calculator with add,sub,mul,div\"}"
```

Откройте `factory-os.html`: индикатор **🤖 Auto (N)** в шапке и KPI **Active workers** берутся из `GET /api/stats` (`active_workers`, `workers_snapshot`). Деталь атома показывает `queue / lease`, если есть строка в `work_item_queue`.

## Observe progress
- Orchestrator status: `GET /api/orchestrator/status`
- Work item tree: `GET /api/work-items/tree?root_id=<vision_id>`
- Work item detail + files + event log: `GET /api/work-items/<id>`
- Runs and run steps:
  - `GET /api/work-items/<id>/runs`
  - `GET /api/runs/<run_id>/steps`

## Run tests
From `Fabrika2.0/proekt`:

```bash
python -m unittest discover -v -s factory/tests
```

## Experiments used in this repo

### Modify existing code (calculator)
Goal: ensure Planner sets `intent=modify` and Forge **extends** existing files (does not rewrite).

- Workspace precondition: existing `calculator/` + `tests/test_calc.py` already present.
- Vision text (example):

```bash
powershell -NoProfile -Command "Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/visions' -ContentType 'application/json' -Body (@{ title='calculator modify'; description='Модифицируй существующий код (не создавать с нуля). В calculator/calc.py расширь класс Calculator: добавь power(a,b) и sqrt(a) (sqrt от отрицательного -> ValueError). Обнови tests/test_calc.py: добавить тесты для power и sqrt, не ломая старые.' } | ConvertTo-Json) | ConvertTo-Json -Depth 6"
```

### Two visions "concurrently" (logger + config)
Create two visions ~5 seconds apart, targeting **different** files/directories (`logger/**` vs `config/**`), then observe whether their atoms can progress without file-lock conflicts.

### Stress scenarios
- **Vague prompt**: `"Сделай что-нибудь полезное с данными"` (planner should still produce a small tree).
- **Modify missing file**: `"Модифицируй файл nonexistent.py: ..."` (observe whether Forge fails or creates the file anyway).
- **Kill API server mid-run**: stop `api_server` while Forge is running, restart, and observe recovery (leases, locked DB, stuck runs).

