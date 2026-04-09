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
    "api_metrics": "api_server",
    "orchestrator_status": "api_server",
    "orchestrator_start": "api_server",
    "orchestrator_stop": "api_server",
    "orchestrator_health": "api_server",
    "orchestrator_tick": "api_server",
    "chat_qwen_create": "api_server",
    "chat_qwen_stream": "api_server",
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
    "queue_forge_inbox": "work_items",
    "fsm_work_item": "work_items",
    "agents_list_compat": "agents",
    "failure_clusters": "work_items",
    "failures": "work_items",
    "hr_stub": "work_items",
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
