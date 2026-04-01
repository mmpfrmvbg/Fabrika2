"""Хелперы для тестов qwen_cli_runner (перезагрузка ACCOUNTS)."""

from __future__ import annotations

import importlib
import os


def reload_config_with_account_keys(n: int, *, prefix: str = "ut_qwen_") -> None:
    """Задать ``FACTORY_API_KEY_1..n`` и перезагрузить ``config`` + ``db``."""
    import factory.config as fc
    import factory.db as fdb

    for i in range(1, 10):
        os.environ.pop(f"FACTORY_API_KEY_{i}", None)
        os.environ.pop(f"FACTORY_API_NAME_{i}", None)
        os.environ.pop(f"FACTORY_API_LIMIT_{i}", None)
    for i in range(1, n + 1):
        os.environ[f"FACTORY_API_KEY_{i}"] = f"{prefix}key_{i}"
        os.environ[f"FACTORY_API_NAME_{i}"] = f"Ut{i}"
        os.environ[f"FACTORY_API_LIMIT_{i}"] = "3000"
    importlib.reload(fc)
    # load_dotenv в config снова подхватил лишние FACTORY_API_KEY_* из .env
    for i in range(n + 1, 10):
        os.environ.pop(f"FACTORY_API_KEY_{i}", None)
        os.environ.pop(f"FACTORY_API_NAME_{i}", None)
        os.environ.pop(f"FACTORY_API_LIMIT_{i}", None)
    importlib.reload(fc)
    importlib.reload(fdb)
    import factory.qwen_cli_runner as qcr

    importlib.reload(qcr)


def reload_single_test_account() -> None:
    """Вернуть один слот ACCOUNTS (как в остальных юнит-тестах)."""
    import factory.config as fc
    import factory.db as fdb

    for i in range(1, 10):
        os.environ.pop(f"FACTORY_API_KEY_{i}", None)
        os.environ.pop(f"FACTORY_API_NAME_{i}", None)
        os.environ.pop(f"FACTORY_API_LIMIT_{i}", None)
    os.environ["FACTORY_API_KEY_1"] = "unit-test-key-forge-exhausted"
    os.environ["FACTORY_API_NAME_1"] = "ci"
    os.environ["FACTORY_API_LIMIT_1"] = "3000"
    importlib.reload(fc)
    for i in range(2, 10):
        os.environ.pop(f"FACTORY_API_KEY_{i}", None)
        os.environ.pop(f"FACTORY_API_NAME_{i}", None)
        os.environ.pop(f"FACTORY_API_LIMIT_{i}", None)
    importlib.reload(fc)
    importlib.reload(fdb)
    import factory.qwen_cli_runner as qcr

    importlib.reload(qcr)
