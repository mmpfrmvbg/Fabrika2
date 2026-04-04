from __future__ import annotations

import importlib


def test_config_env_var_overrides(monkeypatch, tmp_path) -> None:
    import factory.config as config

    db_path = tmp_path / "custom_factory.db"
    monkeypatch.setenv("FACTORY_DB_PATH", str(db_path))
    monkeypatch.setenv("FACTORY_WORKER_TIMEOUT", "123.5")
    monkeypatch.setenv("FACTORY_API_PORT", "19090")
    monkeypatch.setenv("FACTORY_WEBHOOK_URL", "https://hooks.example.test/f")
    monkeypatch.setenv("FACTORY_WEBHOOK_SECRET", "abc123")

    importlib.reload(config)
    try:
        assert config.get_db_path() == db_path.resolve()
        assert config.WORKER_STUCK_TIMEOUT_SECONDS == 123.5
        assert config.API_PORT == 19090
        assert config.FACTORY_WEBHOOK_URL == "https://hooks.example.test/f"
        assert config.FACTORY_WEBHOOK_SECRET == "abc123"
    finally:
        # Вернуть модуль к дефолтному состоянию после отката env.
        importlib.reload(config)
