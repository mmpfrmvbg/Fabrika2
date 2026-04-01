"""Сборка графа зависимостей приложения (composition root)."""
from pathlib import Path

from .config import AccountManager, resolve_db_path
from .db import init_db
from .actions import Actions
from .fsm import StateMachine
from .guards import Guards
from .logging import FactoryLogger
from .orchestrator_core import Orchestrator
from .work_items import WorkItemOps


def wire(db_path: Path | None = None) -> dict:
    """
    Создаёт и возвращает все компоненты фабрики.

        factory = wire()
        factory["ops"].create_vision("…")
        factory["orchestrator"].start()
    """
    conn = init_db(resolve_db_path(db_path))
    logger = FactoryLogger(conn)
    guards = Guards(conn)
    accounts = AccountManager(conn, logger)
    actions = Actions(conn, logger, accounts)
    sm = StateMachine(conn, guards, actions, logger)
    orchestrator = Orchestrator(conn, sm, accounts, logger)
    ops = WorkItemOps(conn, logger)

    return {
        "conn": conn,
        "logger": logger,
        "guards": guards,
        "actions": actions,
        "sm": sm,
        "accounts": accounts,
        "orchestrator": orchestrator,
        "ops": ops,
    }
