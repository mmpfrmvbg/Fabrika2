"""FACTORY_API_KEY: GET открыты, POST без ключа → 403 при установленном ключе."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from factory.api_server import app
from factory.db import init_db


class ApiKeyGateTests(unittest.TestCase):
    def test_without_key_post_works(self) -> None:
        prev = os.environ.get("FACTORY_API_KEY")
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        db = Path(tempfile.mkstemp(prefix="factory_auth_", suffix=".db")[1])
        try:
            os.environ.pop("FACTORY_API_KEY", None)
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            init_db(db).close()
            os.environ["FACTORY_DB"] = str(db)
            client = TestClient(app)
            r = client.get("/api/tree")
            self.assertEqual(r.status_code, 200)
            p = client.post("/api/visions", json={"title": "noauth", "description": ""})
            self.assertEqual(p.status_code, 200)
            self.assertTrue(p.json().get("ok"))
        finally:
            if prev is None:
                os.environ.pop("FACTORY_API_KEY", None)
            else:
                os.environ["FACTORY_API_KEY"] = prev
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            try:
                db.unlink(missing_ok=True)
            except OSError:
                pass
            os.environ.pop("FACTORY_DB", None)

    def test_with_key_post_requires_header(self) -> None:
        prev = os.environ.get("FACTORY_API_KEY")
        prev_dry = os.environ.get("FACTORY_QWEN_DRY_RUN")
        db = Path(tempfile.mkstemp(prefix="factory_auth_", suffix=".db")[1])
        try:
            os.environ["FACTORY_QWEN_DRY_RUN"] = "1"
            init_db(db).close()
            os.environ["FACTORY_DB"] = str(db)
            os.environ["FACTORY_API_KEY"] = "test123"
            client = TestClient(app)
            g = client.get("/api/tree")
            self.assertEqual(g.status_code, 200)
            p = client.post(
                "/api/visions",
                json={"title": "x", "description": "y"},
            )
            self.assertEqual(p.status_code, 403)
            ok = client.post(
                "/api/visions",
                json={"title": "x", "description": "y"},
                headers={"X-API-Key": "test123"},
            )
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json().get("ok"))
        finally:
            if prev is None:
                os.environ.pop("FACTORY_API_KEY", None)
            else:
                os.environ["FACTORY_API_KEY"] = prev
            if prev_dry is None:
                os.environ.pop("FACTORY_QWEN_DRY_RUN", None)
            else:
                os.environ["FACTORY_QWEN_DRY_RUN"] = prev_dry
            try:
                db.unlink(missing_ok=True)
            except OSError:
                pass
            os.environ.pop("FACTORY_DB", None)


if __name__ == "__main__":
    unittest.main()
