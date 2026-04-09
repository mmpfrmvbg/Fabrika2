"""Shared API dependencies for router modules.

This module reduces direct coupling between domain routers and ``factory.api_server``.
Routers should import endpoint callables and shared DB helpers from here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .db import ensure_schema, get_connection, init_db

# Endpoints exposed for router wiring.
_API_ENDPOINT_NAMES: tuple[str, ...] = (
    "health",
    "api_health",
    "api_metrics",
    "orchestrator_status",
    "orchestrator_start",
    "orchestrator_stop",
    "orchestrator_health",
    "orchestrator_tick",
    "chat_qwen_create",
    "chat_qwen_stream",
    "api_analytics",
    "stats",
    "api_workers_status",
    "journal",
    "judgements",
    "judge_verdicts",
    "tree",
    "list_improvements",
    "approve_improvement",
    "reject_improvement",
    "convert_improvement",
    "visions",
    "create_vision",
    "decompose_vision_endpoint",
    "queue_forge_inbox",
    "fsm_work_item",
    "agents_list_compat",
    "failure_clusters",
    "failures",
    "hr_stub",
    "qwen_fix_endpoint",
)


def _api_server() -> Any:
    from . import api_server

    return api_server


def __getattr__(name: str) -> Callable[..., Any]:
    if name in {"list_improvements", "approve_improvement", "reject_improvement", "convert_improvement"}:
        from .routers import improvements

        return getattr(improvements, name)
    if name in {"journal", "tree"}:
        from .routers import journal

        return getattr(journal, name)
    if name in {"judgements", "queue_forge_inbox", "judge_verdicts", "fsm_work_item"}:
        from .routers import judgements

        return getattr(judgements, name)
    if name in {
        "api_analytics",
        "stats",
        "api_workers_status",
        "agents_list_compat",
        "failure_clusters",
        "failures",
        "hr_stub",
    }:
        from .routers import analytics

        return getattr(analytics, name)
    if name in {"visions", "create_vision", "decompose_vision_endpoint"}:
        from .routers import visions

        return getattr(visions, name)
    if name in {
        "create_run",
        "runs_for_work_item",
        "list_runs",
        "get_run_detail",
        "get_run_steps",
        "get_effective_run_id",
        "list_events",
        "stream_events",
    }:
        from .routers import runs

        return getattr(runs, name)
    if name in {
        "list_work_items",
        "export_work_items",
        "work_items_tree_endpoint",
        "post_work_item_cancel",
        "post_work_item_archive",
        "patch_work_item",
        "delete_work_item_endpoint",
        "post_bulk_archive",
        "post_work_item_run",
        "post_tasks_forge_run_compat",
        "get_work_item",
        "get_task_bundle",
        "create_work_item_legacy",
    }:
        from .routers import work_items

        return getattr(work_items, name)
    if name in _API_ENDPOINT_NAMES:
        return getattr(_api_server(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ensure_schema",
    "get_connection",
    "init_db",
    *_API_ENDPOINT_NAMES,
]
