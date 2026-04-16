"""Shared API dependencies for router modules.

This module reduces direct coupling between domain routers and ``factory.api_server``.
Routers should import endpoint callables and shared DB helpers from here.
"""
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any

from .db import ensure_schema, get_connection, init_db


_ROUTER_BY_ENDPOINT: dict[str, str] = {
    "health": "api_server",
    "api_health": "api_server",
    "api_metrics": "orchestrator",
    "orchestrator_status": "orchestrator",
    "orchestrator_start": "orchestrator",
    "orchestrator_stop": "orchestrator",
    "orchestrator_health": "orchestrator",
    "orchestrator_tick": "orchestrator",
    "chat_qwen_create": "chat",
    "chat_qwen_stream": "chat",
    "require_api_key": "api_server",
    "api_analytics": "analytics",
    "stats": "analytics",
    "api_workers_status": "analytics",
    "journal": "journal",
    "judgements": "journal",
    "judge_verdicts": "journal",
    "tree": "agents",
    "list_improvements": "improvements",
    "approve_improvement": "improvements",
    "reject_improvement": "improvements",
    "convert_improvement": "improvements",
    "visions": "visions",
    "create_vision": "visions",
    "decompose_vision_endpoint": "visions",
    "queue_forge_inbox": "journal",
    "fsm_work_item": "journal",
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
    "agents_list_compat": "agents",
    "failure_clusters": "journal",
    "failures": "journal",
    "hr_stub": "agents",
    "qwen_fix_endpoint": "qwen",
    "create_run": "runs",
    "runs_for_work_item": "runs",
    "list_runs": "runs",
    "get_run_detail": "runs",
    "get_run_steps": "runs",
    "get_effective_run_id": "runs",
    "list_events": "runs",
    "stream_events": "runs",
}


def __getattr__(name: str) -> Callable[..., Any]:
    module_name = _ROUTER_BY_ENDPOINT.get(name)
    if module_name is not None:
        module_path = ".api_server" if module_name == "api_server" else f".routers.{module_name}"
        module = import_module(module_path, package=__package__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_ENDPOINT_EXPORTS: list[str] = [
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
    "require_api_key",
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
    "agents_list_compat",
    "failure_clusters",
    "failures",
    "hr_stub",
    "qwen_fix_endpoint",
    "create_run",
    "runs_for_work_item",
    "list_runs",
    "get_run_detail",
    "get_run_steps",
    "get_effective_run_id",
    "list_events",
    "stream_events",
]


__all__ = ["ensure_schema", "get_connection", "init_db", *_ENDPOINT_EXPORTS]
