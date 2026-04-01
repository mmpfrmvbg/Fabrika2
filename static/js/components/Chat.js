/**
 * Factory OS — Chat Component
 * Компонент чата с Qwen (SSE стриминг)
 */

import { store, subscribe } from '../state/store.js';

/**
 * Chat Component
 * @param {HTMLElement} container - контейнер для чата
 */
export function ChatComponent(container) {
  let unsubscribe = null;
  
  // ═══════════════════════════════════════════════════════
  // SUBSCRIBE TO STORE
  // ═══════════════════════════════════════════════════════
  
  function subscribeToStore() {
    unsubscribe = subscribe((state, changes) => {
      // Стриминг-апдейт — точечное изменение, не full render
      if (changes._isStreamUpdate && state.chat.isOpen) {
        updateLastAssistantMessage(changes._streamChunk);
        scrollToBottom();
        return;
      }
      
      // Полный re-render для структурных изменений
      if (state.chat.isOpen) {
        container.classList.add('open');
        render(state.chat);
      } else {
        container.classList.remove('open');
      }
    });
  }
  
  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════
  
  function render(chatState) {
    container.innerHTML = renderChatHTML(chatState);
    attachEventListeners();
    scrollToBottom();
  }
  
  function renderChatHTML(chatState) {
    const { messages, isLoading, contextWorkItemId } = chatState;
    
    return `
      <div class="chat-panel">
        <div class="chat-header">
          <div class="chat-title">
            <span class="chat-icon">💬</span>
            <h3>Chat с Qwen</h3>
            ${contextWorkItemId ? 
              `<span class="chat-context-badge">Контекст: ${contextWorkItemId.slice(0, 8)}...</span>` : 
              '<span class="chat-context-badge">Без контекста</span>'
            }
          </div>
          <button class="chat-close-btn" title="Закрыть" aria-label="Закрыть чат">×</button>
        </div>
        
        <div class="chat-messages" role="log" aria-live="polite">
          ${messages.length === 0 ? 
            '<div class="chat-empty-state">Начните диалог с Qwen о коде</div>' : 
            ''
          }
          ${messages.map(m => renderMessage(m)).join('')}
          ${isLoading ? '<div class="chat-typing">Qwen думает...</div>' : ''}
        </div>
        
        <div class="chat-input-form">
          <textarea 
            class="chat-input-textarea"
            placeholder="Спросите Qwen о коде..." 
            rows="3"
            aria-label="Сообщение Qwen"
          ></textarea>
          <button class="chat-send-btn" ${isLoading ? 'disabled' : ''}>
            Отправить
          </button>
        </div>
      </div>
    `;
  }
  
  function renderMessage(message) {
    const { role, content, timestamp } = message;
    const isUser = role === 'user';

    return `
      <div class="chat-message ${isUser ? 'user' : 'assistant'}" role="article">
        <div class="chat-message-header">
          <span class="chat-message-role">${isUser ? 'Вы' : 'Qwen'}</span>
          <span class="chat-message-time">${formatTime(timestamp)}</span>
        </div>
        <div class="chat-message-content" style="white-space:pre-wrap">${escapeHtml(content)}</div>
      </div>
    `;
  }
  
  // ═══════════════════════════════════════════════════════
  // EVENT LISTENERS
  // ═══════════════════════════════════════════════════════
  
  function attachEventListeners() {
    // Закрыть чат
    const closeBtn = container.querySelector('.chat-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        store.closeChat();
      });
    }
    
    // Отправить сообщение
    const sendBtn = container.querySelector('.chat-send-btn');
    const textarea = container.querySelector('.chat-input-textarea');
    
    if (sendBtn && textarea) {
      sendBtn.addEventListener('click', () => {
        const prompt = textarea.value.trim();
        if (prompt) {
          textarea.value = '';
          store.sendMessage(prompt);
        }
      });
      
      // Отправка по Enter (без Shift)
      textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendBtn.click();
        }
      });
      
      // Auto-resize textarea
      textarea.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
      });
    }
  }
  
  // ═══════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════
  
  // ═══════════════════════════════════════════════════════
  // ИСПРАВЛЕНИЕ БАГА #3: Точечное обновление для стриминга
  // ═══════════════════════════════════════════════════════
  
  function updateLastAssistantMessage(content) {
    const messagesContainer = container.querySelector('.chat-messages');
    if (!messagesContainer) return;
    
    const messages = messagesContainer.querySelectorAll('.chat-message.assistant');
    const lastMessage = messages[messages.length - 1];
    
    if (lastMessage) {
      const contentEl = lastMessage.querySelector('.chat-message-content');
      if (contentEl) {
        // Заменяем только текст, не весь DOM
        contentEl.textContent = content;
      }
    } else {
      // Если сообщения ещё нет — полный re-render
      render(store.state.chat);
    }
  }
  
  function scrollToBottom() {
    const messagesContainer = container.querySelector('.chat-messages');
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }
  
  function formatTime(iso) {
    return new Date(iso).toLocaleTimeString('ru-RU', { 
      hour: '2-digit', 
      minute: '2-digit' 
    });
  }
  
  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  // ═══════════════════════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════════════════════
  
  subscribeToStore();
  
  // Cleanup
  return () => {
    if (unsubscribe) unsubscribe();
  };
}
