from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import factory.api_server as api_server


class _FakeRequest:
    async def json(self):
        return {"prompt": "hello", "context": {}, "work_item_id": None}


@pytest.mark.asyncio
async def test_chat_qwen_create_closes_service_connection(monkeypatch):
    temp_conn = MagicMock()
    rw_conn = MagicMock()
    rw_conn.execute.return_value.fetchone.return_value = None

    service = MagicMock()
    service.create_chat_session.return_value = "chat-1"
    service.close = MagicMock()

    monkeypatch.setattr(api_server, "_db_path", lambda: "test.db")
    monkeypatch.setattr(api_server, "init_db", lambda _: temp_conn, raising=False)
    monkeypatch.setattr(api_server, "_open_rw", lambda: rw_conn)
    monkeypatch.setattr(api_server, "AccountManager", lambda *_: MagicMock())
    monkeypatch.setattr(api_server, "FactoryLogger", lambda *_: MagicMock())
    monkeypatch.setattr(api_server, "ChatService", lambda *_: service)
    monkeypatch.setattr(
        api_server.ChatCreateRequest,
        "model_validate",
        staticmethod(lambda _: SimpleNamespace(prompt="hello", context={}, work_item_id=None)),
    )

    result = await api_server.chat_qwen_create(_FakeRequest())

    assert result == {"chat_id": "chat-1"}
    service.close.assert_called_once()
    rw_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_chat_qwen_stream_closes_connections_after_stream(monkeypatch):
    temp_conn = MagicMock()
    rw_conn = MagicMock()

    async def _stream():
        yield "data: test\n\n"

    service = MagicMock()
    service.stream_chat_response = MagicMock(return_value=_stream())
    service.close = MagicMock()

    monkeypatch.setattr(api_server, "_db_path", lambda: "test.db")
    monkeypatch.setattr(api_server, "init_db", lambda _: temp_conn, raising=False)
    monkeypatch.setattr(api_server, "_open_rw", lambda: rw_conn)
    monkeypatch.setattr(api_server, "AccountManager", lambda *_: MagicMock())
    monkeypatch.setattr(api_server, "FactoryLogger", lambda *_: MagicMock())
    monkeypatch.setattr(api_server, "ChatService", lambda *_: service)

    response = await api_server.chat_qwen_stream("chat-1")
    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks == ["data: test\n\n"]
    service.close.assert_called_once()
    rw_conn.close.assert_called_once()
