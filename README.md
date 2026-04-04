# Fabrika2

Fabrika2 is a task-orchestration system built around **FastAPI + SQLite + FSM + agent pipeline** (Planner → Forge → Review → Judge). The project includes an operational API, worker loop, dashboard-compatible endpoints, and test coverage for API/workflow behavior.

## Architecture

### Core components
- **API server**: `factory/api_server.py` (FastAPI endpoints, auth, orchestration controls).
- **Database layer**: `factory/db.py` + `factory/schema_ddl.py` (SQLite WAL, schema init/migrations helpers).
- **Workflow engine**: `factory/orchestrator_core.py`, `factory/fsm.py`, `factory/actions.py`, `factory/guards.py`.
- **Agent roles**: `factory/agents/` (planner, forge, reviewer, judge, architect).
- **Background worker**: `factory/worker.py` (claims queue items and executes forge pipeline).
- **UI/static dashboard assets**: `factory-os.html`, `static/`.

### Request flow
1. Client calls API endpoint.
2. Endpoint validates inputs (FastAPI + Pydantic + explicit business validation).
3. API reads/writes SQLite through `factory/db.py` connection helpers.
4. Orchestrator/worker advances work items through FSM transitions.
5. Events, runs, and artifacts are persisted for dashboards and analytics.

## Configuration

All production-sensitive defaults are centralized in `factory/config.py` with environment variable overrides.

### Important environment variables
- `FACTORY_DB_PATH` (or legacy `FACTORY_DB`) — SQLite path.
- `FACTORY_API_HOST` — API bind host (default: `127.0.0.1`).
- `FACTORY_API_PORT` — API bind port (default: `8000`).
- `FACTORY_TICK_INTERVAL` — orchestrator tick interval seconds (min `0.2`).
- `FACTORY_WORKER_POLL` — worker idle poll seconds (min `0.5`).
- `FACTORY_WORKER_TIMEOUT` — stuck running-item recovery timeout in seconds.
- `FACTORY_SQLITE_TIMEOUT_SECONDS` — SQLite connection timeout.
- `FACTORY_SQLITE_BUSY_TIMEOUT_MS` — SQLite busy timeout pragma.
- `FACTORY_QWEN_DECOMPOSE_TIMEOUT` — timeout for `/api/visions/{id}/decompose` calls.
- `FACTORY_QWEN_FIX_TIMEOUT` — timeout for `/api/qwen/fix` calls.
- `FACTORY_API_KEY` — if set, mutating endpoints require `X-API-Key` header.

## Setup

### 1) Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment
```bash
export FACTORY_DB_PATH=/absolute/path/to/factory.db
export FACTORY_API_PORT=8000
# Optional auth for mutating endpoints:
export FACTORY_API_KEY=change-me
```

### 3) Run API
```bash
python -m factory.api_server
```

### 4) Run background worker (optional but recommended in production)
```bash
python -m factory.worker --id worker-1 --poll 3
```

## API overview

> All endpoints now use request/query/path validation and return proper HTTP codes:
> - `400` for malformed business input,
> - `422` for schema/type validation failures,
> - `404` for missing entities.

### Health & orchestration
- `GET /health`
- `GET /api/orchestrator/status`
- `POST /api/orchestrator/start`
- `POST /api/orchestrator/stop`
- `POST /api/orchestrator/tick`

### Work items
- `GET /api/work-items`
- `GET /api/work-items/tree`
- `GET /api/work-items/{wi_id}`
- `GET /api/work-items/{wi_id}/runs`
- `PATCH /api/work-items/{wi_id}`
- `POST /api/work-items/{wi_id}/cancel`
- `POST /api/work-items/{wi_id}/archive`
- `POST /api/work-items/{wi_id}/run`
- `DELETE /api/work-items/{wi_id}`

### Runs/events/analytics
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/steps`
- `GET /api/events`
- `GET /api/journal`
- `GET /api/analytics`
- `GET /api/stats`

### Vision and Qwen endpoints
- `GET /api/visions`
- `POST /api/visions`
- `POST /api/visions/{vision_id}/decompose`
- `POST /api/chat/qwen`
- `GET /api/chat/qwen/{chat_id}/stream`
- `POST /api/qwen/fix`

## API examples

### Create a vision
```bash
curl -X POST http://127.0.0.1:8000/api/visions \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"title":"Improve deployment reliability","description":"Reduce failed deployments and MTTR"}'
```

### Patch work item metadata
```bash
curl -X PATCH http://127.0.0.1:8000/api/work-items/wi_123 \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"title":"Updated title","description":"Updated details"}'
```

### Read recent runs for a work item
```bash
curl "http://127.0.0.1:8000/api/runs?work_item_id=wi_123&limit=50"
```

### Typical error response examples
```json
{"detail":"work_item not found"}
```
(HTTP 404)

```json
{"detail":"id required"}
```
(HTTP 400)

```json
{"detail":[{"type":"string_too_short", "loc":["path","wi_id"], "msg":"String should have at least 1 character"}]}
```
(HTTP 422)

## Testing

Run all tests:
```bash
pytest -q
```

If you want only API-focused tests:
```bash
pytest -q factory/tests/test_api_*.py
```

## Notes for production
- Keep SQLite on fast local disk; keep WAL enabled.
- Protect mutating endpoints via `FACTORY_API_KEY` and network controls.
- Run API and worker as separate supervised processes.
- Add log shipping/metrics around `event_log`, `runs`, and worker heartbeats.
