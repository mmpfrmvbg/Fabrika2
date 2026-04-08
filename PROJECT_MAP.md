# Fabrika2 Project Map

## api_server.py
- Lines: ~2337 (after PR #75)
- Remaining direct routes: minimal (auth, health, legacy)
- Domain routers included via _include_domain_routers()

## factory/routers/
- admin_health.py - admin & health endpoints
- chat.py - chat endpoints
- orchestrator.py - orchestration/dashboard routes (events, journal, judgements, verdicts, tree, analytics, stats, workers, improvements, queue, fsm, agents, failure-clusters, failures, hr, visions)
- qwen.py - qwen/visions endpoints
- work_items.py - work item endpoints

## Key modules
- factory/orchestrator_core.py - Orchestrator class, _dispatch_planner promotes draft atoms to ready_for_work (PR #74)
- factory/planner.py - Planner
- factory/forge.py - Forge worker
