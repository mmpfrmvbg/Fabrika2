from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from factory.config import AccountManager
from factory.db import DB_PATH, get_connection
from factory.logging import FactoryLogger
from factory.qwen_cli_runner import run_qwen_cli
from factory.schemas import QwenFixRequest


async def _require_api_key(request: Request) -> None:
    import factory.api_server as api_server

    await api_server.require_api_key(request)


def qwen_fix_endpoint(
    body: QwenFixRequest = Body(...),
    _: None = Depends(_require_api_key),
) -> dict[str, Any]:
    """
    Запрос исправления ошибки у Qwen.
    Используется для авто-исправления Forge ошибок.
    """
    error_type = str(body.type or "unknown").strip()
    message = body.message.strip()
    context = body.context

    prompt = f"""
Произошла ошибка при выполнении Forge задачи.

Тип ошибки: {error_type}
Сообщение: {message}
Контекст: {json.dumps(context, indent=2)}

Проанализируй ошибку и предложи исправление.
Верни ТОЛЬКО JSON без markdown:
{{
  "suggestion": "Описание проблемы и решения",
  "files": ["path/to/file.py"],
  "changes": [
    {{
      "file": "path/to/file.py",
      "action": "modify",
      "content": "Новое содержимое файла или diff"
    }}
  ],
  "confidence": 0.95
}}
"""

    try:
        with get_connection(DB_PATH) as conn:
            logger = FactoryLogger(conn)
            am = AccountManager(conn, logger)
            result = run_qwen_cli(
                conn=conn,
                account_manager=am,
                logger=logger,
                work_item_id="api_fix_preview",
                title=f"Fix: {error_type}",
                description=message,
                full_prompt=prompt,
            )
        result_text = result.stdout or result.stderr or ""

        import re

        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if json_match:
            fix = json.loads(json_match.group())
        else:
            fix = json.loads(result_text)

        return {"fix": fix, "ok": True}

    except json.JSONDecodeError:
        logging.getLogger(__name__).exception("Qwen fix JSON error")
        raise HTTPException(status_code=500, detail={"error": "Invalid JSON from Qwen"})
    except Exception:
        logging.getLogger(__name__).exception("Qwen fix error")
        raise HTTPException(status_code=500, detail={"error": "Fix failed"})


def build_router() -> APIRouter:
    from factory import deps as srv

    router = APIRouter(tags=["qwen"])
    router.add_api_route("/api/qwen/fix", srv.qwen_fix_endpoint, methods=["POST"])
    return router
