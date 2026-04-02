/**
 * Factory OS — EscalationView Component
 * Модальное окно эскалации ошибок пользователю
 */

/**
 * EscalationView Component
 * @param {HTMLElement} container - контейнер для модалки
 * @param {Object} error - Объект ошибки
 */
export function EscalationViewComponent(container, error) {
  if (!container) return null;
  
  let isOpen = false;

  function render() {
    if (!container) return;
    
    if (!isOpen) {
      container.classList.remove('open');
      container.setAttribute('aria-hidden', 'true');
      return;
    }
    
    container.classList.add('open');
    container.setAttribute('aria-hidden', 'false');
    container.innerHTML = `
      <div class="ev-backdrop" onclick="window.closeEscalationModal()"></div>
      <div class="ev-modal-card">
        ${renderHeader()}
        ${renderBody(error)}
        ${renderActions(error)}
      </div>
    `;
  }

  function renderHeader() {
    return `
      <div class="ev-header">
        <div class="ev-header-title">
          <span class="ev-icon">⚠️</span>
          <h2>Требуется ваше внимание</h2>
        </div>
        <button class="ev-close-btn" onclick="window.closeEscalationModal()" title="Закрыть">×</button>
      </div>
    `;
  }

  function renderBody(error) {
    return `
      <div class="ev-body">
        <div class="ev-error">
          <div class="ev-error-type">❌ ${escapeHtml(error.type)}</div>
          <div class="ev-error-message">${escapeHtml(error.message)}</div>
        </div>
        
        <div class="ev-attempts">
          <div class="ev-attempts-title">
            Фабрика пыталась исправить (${error.attempts || 0}/${error.maxAttempts || 3}):
          </div>
          ${(error.attemptsHistory || []).map(a => `
            <div class="ev-attempt ${a.success ? 'success' : 'failed'}">
              ${a.success ? '✅' : '❌'} ${escapeHtml(a.message)}
            </div>
          `).join('')}
        </div>
        
        ${error.suggestion ? `
          <div class="ev-suggestion">
            <div class="ev-suggestion-title">💡 Qwen предлагает:</div>
            <div class="ev-suggestion-text">${escapeHtml(error.suggestion)}</div>
          </div>
        ` : ''}
      </div>
    `;
  }

  function renderActions(error) {
    return `
      <div class="ev-actions">
        <button onclick="window.delegateToQwen('${error.runId}')" class="ev-btn-primary">
          🤖 Доверить Qwen
        </button>
        <button onclick="window.fixManually('${error.runId}')" class="ev-btn-secondary">
          ✏️ Вручную
        </button>
        <button onclick="window.skipTask('${error.runId}')" class="ev-btn-secondary">
          ⏭️ Пропустить
        </button>
        <button onclick="window.stopFactory()" class="ev-btn-danger">
          ⏹️ Остановить
        </button>
      </div>
    `;
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  return {
    open() {
      isOpen = true;
      render();
    },
    close() {
      isOpen = false;
      render();
    }
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS
// ═══════════════════════════════════════════════════════

let escalationInstance = null;

window.showEscalationModal = (run, result) => {
  const container = document.getElementById('escalation-modal');
  if (!container) {
    console.error('[Escalation] Modal container not found');
    return;
  }
  
  if (!escalationInstance) {
    escalationInstance = EscalationViewComponent(container);
  }
  
  const error = {
    runId: run.id,
    type: run.event_type || 'forge_failed',
    message: run.message || 'Forge failed',
    attempts: result.attempts || 0,
    maxAttempts: 3,
    attemptsHistory: result.attemptsHistory || [],
    suggestion: result.suggestion || ''
  };
  
  escalationInstance.open(error);
};

window.closeEscalationModal = () => {
  if (escalationInstance) {
    escalationInstance.close();
  }
};

window.delegateToQwen = (runId) => {
  console.log('[Escalation] Delegate to Qwen:', runId);
  window.closeEscalationModal();
  window.showFactoryToast('Qwen исправляет...', 'ok');
};

window.fixManually = (runId) => {
  console.log('[Escalation] Fix manually:', runId);
  window.closeEscalationModal();
  window.showFactoryToast('Откройте редактор для исправления', 'ok');
};

window.skipTask = (runId) => {
  console.log('[Escalation] Skip task:', runId);
  window.closeEscalationModal();
  window.showFactoryToast('Задача пропущена', 'ok');
};

window.stopFactory = () => {
  console.log('[Escalation] Stop factory');
  window.closeEscalationModal();
  if (window.toggleFactoryPause) {
    window.factoryPaused = true;
    window.toggleFactoryPause();
  }
  window.showFactoryToast('Фабрика остановлена', 'ok');
};
