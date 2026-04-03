/**
 * Factory OS — Empty State Component
 * Универсальный компонент для пустых состояний
 */

/**
 * Empty State Component
 * @param {HTMLElement} container - контейнер
 * @param {Object} options - настройки
 */
export function EmptyStateComponent(container, options = {}) {
  if (!container) return null;

  const {
    icon = '📭',
    title = 'Нет данных',
    description = '',
    actionText = '',
    actionCallback = null
  } = options;

  function render() {
    container.innerHTML = `
      <div class="empty-state">
        <div class="es-icon" aria-hidden="true">${icon}</div>
        <div class="es-title">${escapeHtml(title)}</div>
        ${description ? `<div class="es-sub">${escapeHtml(description)}</div>` : ''}
        ${actionText ? `
          <div class="es-actions">
            <button type="button" class="btn primary" id="empty-state-action-btn">
              ${escapeHtml(actionText)}
            </button>
          </div>
        ` : ''}
      </div>
    `;

    // Attach action callback
    if (actionCallback) {
      const btn = container.querySelector('#empty-state-action-btn');
      if (btn) {
        btn.addEventListener('click', actionCallback);
      }
    }
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  render();

  return {
    update(newOptions) {
      Object.assign(options, newOptions);
      render();
    }
  };
}

// Глобальный экспорт
window.EmptyStateComponent = EmptyStateComponent;
