"""Shared API dependencies for router modules.

This module reduces direct coupling between domain routers and ``factory.api_server``.
Routers should import endpoint callables and shared DB helpers from here.
"""
from __future__ import annotations

from importlib import import_module
from collections.abc import Callable
from typing import Any

from .db import ensure_schema, get_connection, init_db

_ROUTER_BY_ENDPOINT: dict[str, str] = {
    "health": "admin_health",
    "api_health": "admin_health",
    "list_work_items": "work_items",
    "export_work_items": "work_items",
    "work_items_tree_endpoint": "work_items",
    "post_work_item_cancel": "work_items",
    "post_work_item_archive": "work_items",
    "patch_work_item": "work_items",
    "delete_work_item_endpoint": "work_items",
    "post_bulk_archive": "work_items",
    "post_work_item_run": "work_items",
    "post_tasks_forge_run_compat": "work_items",
    "get_work_item": "work_items",
    "get_task_bundle": "work_items",
    "create_work_item_legacy": "work_items",
    "create_run": "runs",
    "runs_for_work_item": "runs",
    "list_runs": "runs",
    "get_run_detail": "runs",
    "get_run_steps": "runs",
    "get_effective_run_id": "runs",
    "list_events": "runs",
    "stream_events": "runs",
    "api_metrics": "orchestrator",
    "orchestrator_status": "orchestrator",
    "orchestrator_start": "orchestrator",
    "orchestrator_stop": "orchestrator",
    "orchestrator_health": "orchestrator",
    "orchestrator_tick": "orchestrator",
    "chat_qwen_create": "chat",
    "chat_qwen_stream": "chat",
    "qwen_fix_endpoint": "qwen",
    "api_analytics": "analytics",
    "stats": "analytics",
    "api_workers_status": "analytics",
    "journal": "journal",
    "judgements": "journal",
    "judge_verdicts": "journal",
    "queue_forge_inbox": "journal",
    "fsm_work_item": "journal",
    "failure_clusters": "journal",
    "failures": "journal",
    "list_improvements": "improvements",
    "approve_improvement": "improvements",
    "reject_improvement": "improvements",
    "convert_improvement": "improvements",
    "visions": "visions",
    "create_vision": "visions",
    "decompose_vision_endpoint": "visions",
    "tree": "agents",
    "agents_list_compat": "agents",
    "hr_stub": "agents",
}


def __getattr__(name: str) -> Callable[..., Any]:
    router_name = _ROUTER_BY_ENDPOINT.get(name)
    if router_name is not None:
        router_module = import_module(f".routers.{router_name}", __package__)
        return getattr(router_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ensure_schema",
    "get_connection",
    "init_db",
    *_ROUTER_BY_ENDPOINT,
]
