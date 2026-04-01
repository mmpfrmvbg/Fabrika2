"""
Factory Core v0.1.0 — песочница Fabrika2.0.

Слои:
  config → models → schema_ddl → db → guards/actions → fsm → orchestrator_core → work_items
  composition (wire) собирает граф зависимостей.
"""
from .actions import Actions
from .cli import cli_status
from .composition import wire
from .config import (
    ACCOUNTS,
    DB_PATH,
    MAX_ATOM_RETRIES,
    MAX_CONCURRENT_FORGE_RUNS,
    MAX_DECOMPOSITION_DEPTH,
    ORCHESTRATOR_ARCHITECT_SCAN_TICKS,
    ORCHESTRATOR_POLL_INTERVAL,
    AccountExhaustedError,
    AccountManager,
)
from .db import gen_id, get_connection, init_db, transaction
from .fsm import StateMachine, apply_transition, find_matching_transition
from .guards import Guards
from .logging import FactoryLogger
from .models import (
    CheckType,
    CommentType,
    DecisionVerdict,
    EventType,
    FileIntent,
    LinkType,
    QueueName,
    Role,
    RunStatus,
    RunType,
    Severity,
    StepKind,
    WorkItemKind,
    WorkItemStatus,
)
from .orchestrator_core import Orchestrator, wait_for_async_workers
from .qwen_cli_runner import (
    ForgeResult,
    looks_rate_limited,
    reset_e2e_qwen_simulation_hooks,
    run_qwen_cli,
)
from .schema_ddl import DDL
from .work_items import WorkItemOps

__all__ = [
    "ACCOUNTS",
    "DB_PATH",
    "DDL",
    "MAX_ATOM_RETRIES",
    "MAX_CONCURRENT_FORGE_RUNS",
    "MAX_DECOMPOSITION_DEPTH",
    "ORCHESTRATOR_POLL_INTERVAL",
    "ORCHESTRATOR_ARCHITECT_SCAN_TICKS",
    "AccountExhaustedError",
    "AccountManager",
    "Actions",
    "CheckType",
    "CommentType",
    "DecisionVerdict",
    "EventType",
    "FactoryLogger",
    "FileIntent",
    "ForgeResult",
    "Guards",
    "LinkType",
    "Orchestrator",
    "wait_for_async_workers",
    "looks_rate_limited",
    "QueueName",
    "Role",
    "RunStatus",
    "RunType",
    "Severity",
    "StateMachine",
    "apply_transition",
    "find_matching_transition",
    "StepKind",
    "WorkItemKind",
    "WorkItemOps",
    "WorkItemStatus",
    "cli_status",
    "reset_e2e_qwen_simulation_hooks",
    "run_qwen_cli",
    "wire",
    "gen_id",
    "get_connection",
    "init_db",
    "transaction",
]
