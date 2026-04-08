import asyncio
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture()
def service(tmp_path):
    account_manager = MagicMock()
    patches = [
        patch("factory.chat_service.get_connection", return_value=MagicMock()),
        patch("factory.chat_service.FactoryLogger", return_value=MagicMock()),
    ]
    for p in patches:
        p.start()
    from factory.chat_service import ChatService
    svc = ChatService(db_path=str(tmp_path / "test.db"), account_manager=account_manager)
    yield svc
    for p in patches:
        p.stop()


def test_create_session_returns_uuid(service):
    chat_id = service.create_chat_session("hello", {})
    assert isinstance(chat_id, str) and len(chat_id) == 36


def test_create_session_unique_ids(service):
    id1 = service.create_chat_session("a", {})
    id2 = service.create_chat_session("b", {})
    assert id1 != id2


def test_stream_unknown_id_yields_error(service):
    async def run():
        chunks = []
        async for chunk in service.stream_chat_response("no-such-id"):
            chunks.append(chunk)
        return chunks
    chunks = asyncio.get_event_loop().run_until_complete(run())
    assert any("error" in c for c in chunks)


def test_stream_error_not_leaked(service):
    chat_id = service.create_chat_session("test", {})
    async def run():
        chunks = []
        async for chunk in service.stream_chat_response(chat_id):
            chunks.append(chunk)
            if len(chunks) > 30:
                break
        return chunks
    with patch.object(service, "_run_qwen_chat", side_effect=RuntimeError("secret")):
        chunks = asyncio.get_event_loop().run_until_complete(run())
    assert "secret" not in "".join(chunks)
    assert any("Internal server error" in c for c in chunks)


def test_close_closes_connection_once(tmp_path):
    account_manager = MagicMock()
    fake_conn = MagicMock()
    with patch("factory.chat_service.get_connection", return_value=fake_conn), patch(
        "factory.chat_service.FactoryLogger", return_value=MagicMock()
    ):
        from factory.chat_service import ChatService

        service = ChatService(db_path=str(tmp_path / "test.db"), account_manager=account_manager)
        service.close()
        service.close()

    fake_conn.close.assert_called_once()


def test_context_manager_closes_connection(tmp_path):
    account_manager = MagicMock()
    fake_conn = MagicMock()
    with patch("factory.chat_service.get_connection", return_value=fake_conn), patch(
        "factory.chat_service.FactoryLogger", return_value=MagicMock()
    ):
        from factory.chat_service import ChatService

        with ChatService(db_path=str(tmp_path / "test.db"), account_manager=account_manager):
            pass

    fake_conn.close.assert_called_once()
