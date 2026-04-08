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
    "stream_events",
    "journal",
    "judgements",
    "judge_verdicts",
    "tree",
    "api_analytics",
    "stats",
    "api_workers_status",
    "list_improvements",
    "approve_improvement",
    "reject_improvement",
    "convert_improvement",
    "queue_forge_inbox",
    "fsm_work_item",
    "agents_list_compat",
    "failure_clusters",
    "failures",
    "hr_stub",
    "visions",
    "create_vision",
    "decompose_vision_endpoint",
    "qwen_fix_endpoint",
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
    "create_run",
    "runs_for_work_item",
    "list_runs",
    "get_run_detail",
    "get_run_steps",
    "get_effective_run_id",
)


def _api_server() -> Any:
    from . import api_server

    return api_server


def __getattr__(name: str) -> Callable[..., Any]:
    if name in _API_ENDPOINT_NAMES:
        return getattr(_api_server(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ensure_schema",
    "get_connection",
    "init_db",
    *_API_ENDPOINT_NAMES,
]
