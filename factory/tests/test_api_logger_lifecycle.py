from unittest.mock import MagicMock

import factory.api_server as api_server


def test_get_logger_owns_and_closes_internal_connection(monkeypatch):
    created_conn = MagicMock()

    monkeypatch.setattr(api_server, "_logger", None)
    monkeypatch.setattr(api_server, "_db_path", lambda: "test.db")
    monkeypatch.setattr(api_server, "get_connection", lambda _: created_conn)

    logger = api_server._get_logger()
    assert logger is api_server._logger

    api_server._close_logger()

    created_conn.close.assert_called_once()
    assert api_server._logger is None


def test_get_logger_with_external_connection_is_not_closed(monkeypatch):
    external_conn = MagicMock()

    monkeypatch.setattr(api_server, "_logger", None)

    logger = api_server._get_logger(conn=external_conn)
    assert logger is api_server._logger

    api_server._close_logger()

    external_conn.close.assert_not_called()
    assert api_server._logger is None
