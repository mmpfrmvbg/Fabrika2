from __future__ import annotations

import inspect

from factory import deps
from factory.routers import judgements, monitoring, qwen, runs, visions


def test_migrated_routers_do_not_use_api_server_db_helpers() -> None:
    modules = [visions, qwen, judgements, monitoring, runs]
    for module in modules:
        source = inspect.getsource(module)
        assert "api_server._open_ro" not in source
        assert "api_server._open_rw" not in source
        assert "api_server._db_path" not in source
        assert "api_server._LOG" not in source


def test_migrated_routers_use_factory_db_helpers() -> None:
    modules = [visions, qwen, judgements, monitoring, runs]
    for module in modules:
        source = inspect.getsource(module)
        assert "from factory.db import" in source
        assert "DB_PATH" in source
        assert "get_connection" in source
        assert "get_connection(DB_PATH" in source


def test_visions_api_key_dep_uses_factory_deps(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def _fake_require_api_key(request):
        called["request"] = request

    monkeypatch.setattr(deps, "require_api_key", _fake_require_api_key, raising=False)

    class _Req:
        pass

    req = _Req()
    import asyncio

    asyncio.run(visions._require_api_key_dep(req))

    assert called["request"] is req
