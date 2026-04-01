"""
Сервис для диалога с Qwen Code CLI в чат-режиме.
Интеграция через Server-Sent Events (SSE) для стриминга ответа.

ВАЖНО: SQLite connection нельзя использовать across threads.
Каждый worker thread должен создавать своё соединение.
"""
import asyncio
import json
import uuid
import time
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional
from datetime import datetime, timezone

from .config import AccountManager, resolve_db_path
from .db import get_connection
from .qwen_cli_runner import run_qwen_cli
from .logging import FactoryLogger
from .models import EventType, Role, Severity

# Хранилище активных чат-сессий с TTL
_active_chats: dict[str, dict] = {}
_CHAT_TTL_SECONDS = 300


def _cleanup_expired_chats():
    now = time.time()
    expired = [
        chat_id for chat_id, session in _active_chats.items()
        if session.get('created_at', 0) + _CHAT_TTL_SECONDS < now
    ]
    for chat_id in expired:
        del _active_chats[chat_id]


class ChatService:
    """Сервис для чата с Qwen."""

    def __init__(self, db_path, account_manager: AccountManager):
        self.db_path = db_path
        self.account_manager = account_manager
        # Основное соединение для основного потока
        self.conn = get_connection(db_path)
        self.logger = FactoryLogger(self.conn)

    def create_chat_session(self, prompt: str, context: dict) -> str:
        _cleanup_expired_chats()
        chat_id = str(uuid.uuid4())
        _active_chats[chat_id] = {
            'prompt': prompt,
            'context': context,
            'status': 'pending',
            'response': '',
            'created_at': time.time()
        }
        return chat_id

    async def stream_chat_response(self, chat_id: str) -> AsyncGenerator[str, None]:
        session = _active_chats.get(chat_id)
        if not session:
            yield f"data: {json.dumps({'type': 'error', 'error': 'Chat session not found'})}\n\n"
            return

        try:
            session['status'] = 'running'
            queue = asyncio.Queue()

            def on_chunk(chunk: str):
                queue.put_nowait(chunk)

            # Запуск в executor - ВАЖНО: run_qwen_cli создаёт СВОЁ соединение для логов
            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(
                None,
                lambda: self._run_qwen_chat(session, on_chunk)
            )
            
            # Читать из очереди и yield'ить по мере поступления
            while True:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    # Keepalive для поддержания соединения
                    yield f": keepalive\n\n"
            
            # Финальный ответ
            yield f"data: {json.dumps({'type': 'done', 'full_response': session['response']})}\n\n"
            
            # Сохранить в event_log
            self._save_to_event_log(
                session['context'].get('work_item_id'),
                session['prompt'],
                session['response']
            )
            
        except Exception as e:
            session['status'] = 'error'
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    def _run_qwen_chat(
        self,
        session: dict,
        on_chunk: Callable[[str], None]
    ) -> None:
        """
        Запустить Qwen CLI в чат-режиме с callback для стриминга.
        ВАЖНО: Создаём НОВОЕ соединение для этого worker thread.
        """
        # Создаём отдельное соединение для этого потока
        thread_conn = get_connection(self.db_path)
        thread_logger = FactoryLogger(thread_conn)
        
        full_prompt = self._build_chat_prompt(
            session['prompt'],
            session['context']
        )

        result = run_qwen_cli(
            conn=thread_conn,
            account_manager=self.account_manager,
            logger=thread_logger,
            work_item_id=session['context'].get('work_item_id', ''),
            title='Chat with Qwen',
            description=full_prompt,
            cwd=str(session.get('workspace'))
        )

        if result.ok:
            chunks = result.stdout.split('\n')
            for chunk in chunks:
                if chunk.strip():
                    on_chunk(chunk + '\n')
                    session['response'] += chunk + '\n'
        else:
            raise RuntimeError(result.error_message or 'Qwen CLI failed')
        finally:
            # Закрываем соединение этого потока
            thread_conn.close()
    
    def _build_chat_prompt(self, user_prompt: str, context: dict) -> str:
        """
        Собрать промпт с контекстом задачи.
        """
        parts = []
        
        # System prompt
        parts.append("""Ты — Qwen Code CLI, интегрированный в Factory OS.
Твоя задача: помогать разработчику с кодом в контексте текущей задачи.
Отвечай кратко, по делу. Если нужно изменить код — предлагай конкретные правки.
Используй русский язык для общения.""")
        
        # Контекст задачи
        if context.get('work_item_id'):
            parts.append(f"\n\n## Контекст задачи:")
            parts.append(f"- ID: {context['work_item_id']}")
            parts.append(f"- Тип: {context.get('kind', 'unknown')}")
            parts.append(f"- Заголовок: {context.get('title', 'N/A')}")
            if context.get('description'):
                parts.append(f"- Описание: {context['description']}")
            parts.append(f"- Статус: {context.get('status', 'N/A')}")
        
        # Файлы (если есть)
        if context.get('files'):
            parts.append(f"\n\n## Файлы задачи:")
            for f in context['files']:
                parts.append(f"- {f['path']} ({f.get('intent', 'modify')})")
        
        # Пользовательский вопрос
        parts.append(f"\n\n## Вопрос пользователя:\n{user_prompt}")
        
        return '\n'.join(parts)
    
    def _save_to_event_log(
        self, 
        work_item_id: Optional[str], 
        prompt: str, 
        response: str
    ):
        """
        Сохранить диалог в event_log.
        
        ═══════════════════════════════════════════════════════
        ИСПРАВЛЕНИЕ БАГА #4: Правильный SQL с 9 параметрами
        ═══════════════════════════════════════════════════════
        """
        if not work_item_id:
            return
        
        self.conn.execute("""
            INSERT INTO event_log (
                event_time, event_type, entity_type, entity_id,
                work_item_id, actor_role, severity, message, payload
            ) VALUES (?, ?, 'work_item', ?, ?, ?, 'info', ?, ?)
        """, (
            # ✅ 9 колонок, 9 значений
            datetime.now(timezone.utc).isoformat(),  # ✅ event_time
            'creator_chat',                           # ✅ event_type
            work_item_id,                             # ✅ entity_id
            work_item_id,                             # ✅ work_item_id
            'creator',                                # ✅ actor_role (не orchestrator!)
            f'Chat: {prompt[:100]}...',               # ✅ message
            json.dumps({
                'prompt': prompt,
                'response': response[:2000],
                'response_length': len(response)
            })  # ✅ payload
        ))
        self.conn.commit()
        
        # Логирование в FactoryLogger
        self.logger.log(
            EventType.TASK_STATUS_CHANGED,
            'work_item',
            work_item_id,
            f'Chat с Qwen: {prompt[:50]}...',
            work_item_id=work_item_id,
            actor_role='creator',
            severity=Severity.INFO,
            payload={
                'chat_prompt': prompt,
                'chat_response_length': len(response)
            },
            tags=['chat', 'qwen']
        )
