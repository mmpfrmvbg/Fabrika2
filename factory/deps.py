"""Shared API dependencies for router modules.

This module reduces direct coupling between domain routers and ``factory.api_server``.
Routers should import endpoint callables and shared DB helpers from here.
"""
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
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
    "agents_list_compat": "agents",
    "failure_clusters": "journal",
    "failures": "journal",
    "hr_stub": "agents",
    "qwen_fix_endpoint": "api_server",
}


def __getattr__(name: str) -> Callable[..., Any]:
    module_name = _ROUTER_BY_ENDPOINT.get(name)
    if module_name is not None:
        module_path = ".api_server" if module_name == "api_server" else f".routers.{module_name}"
        module = import_module(module_path, package=__package__)
        return getattr(module, name)
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
    if name in _API_ENDPOINT_NAMES:
        return getattr(_api_server(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ensure_schema",
    "get_connection",
    "init_db",
    *_API_ENDPOINT_NAMES,
]
