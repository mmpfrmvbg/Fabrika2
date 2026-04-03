/**
 * Factory OS — Accessibility Utilities
 * Утилиты для улучшения доступности
 */

/**
 * Добавить ARIA label к элементу
 * @param {string} selector - CSS селектор
 * @param {string} label - ARIA label
 */
export function setAriaLabel(selector, label) {
  const el = document.querySelector(selector);
  if (el) {
    el.setAttribute('aria-label', label);
  }
}

/**
 * Добавить role к элементу
 * @param {string} selector - CSS селектор
 * @param {string} role - ARIA role
 */
export function setRole(selector, role) {
  const el = document.querySelector(selector);
  if (el) {
    el.setAttribute('role', role);
  }
}

/**
 * Trap focus внутри модального окна
 * @param {HTMLElement} modal - модальное окно
 */
export function trapFocus(modal) {
  const focusableElements = modal.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  
  const firstFocusable = focusableElements[0];
  const lastFocusable = focusableElements[focusableElements.length - 1];

  modal.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      if (e.shiftKey) {
        if (document.activeElement === firstFocusable) {
          e.preventDefault();
          lastFocusable.focus();
        }
      } else {
        if (document.activeElement === lastFocusable) {
          e.preventDefault();
          firstFocusable.focus();
        }
      }
    }
    
    if (e.key === 'Escape') {
      modal.dispatchEvent(new CustomEvent('close'));
    }
  });
}

/**
 * Announce сообщение для screen readers
 * @param {string} message - сообщение
 */
export function announceToScreenReader(message) {
  let announcer = document.getElementById('a11y-announcer');
  
  if (!announcer) {
    announcer = document.createElement('div');
    announcer.id = 'a11y-announcer';
    announcer.setAttribute('aria-live', 'polite');
    announcer.setAttribute('aria-atomic', 'true');
    announcer.className = 'sr-only';
    document.body.appendChild(announcer);
  }
  
  announcer.textContent = '';
  setTimeout(() => {
    announcer.textContent = message;
  }, 100);
}

/**
 * Инициализация accessibility улучшений
 */
export function initializeAccessibility() {
  // Добавить skip link
  const skipLink = document.createElement('a');
  skipLink.href = '#main-content';
  skipLink.className = 'skip-link';
  skipLink.textContent = 'Перейти к основному содержимому';
  document.body.insertBefore(skipLink, document.body.firstChild);

  // Announce загрузка страницы
  announceToScreenReader('Страница загружена');

  // Добавить keyboard shortcuts help
  document.addEventListener('keydown', (e) => {
    if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
      const activeElement = document.activeElement;
      if (activeElement.tagName !== 'INPUT' && activeElement.tagName !== 'TEXTAREA') {
        e.preventDefault();
        showKeyboardShortcutsHelp();
      }
    }
  });
}

/**
 * Показать справку по keyboard shortcuts
 */
function showKeyboardShortcutsHelp() {
  const existing = document.getElementById('keyboard-shortcuts-modal');
  if (existing) {
    existing.remove();
    return;
  }

  const modal = document.createElement('div');
  modal.id = 'keyboard-shortcuts-modal';
  modal.className = 'keyboard-shortcuts-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-label', 'Keyboard Shortcuts');
  
  modal.innerHTML = `
    <div class="keyboard-shortcuts-backdrop" onclick="this.parentElement.remove()"></div>
    <div class="keyboard-shortcuts-content">
      <h2>⌨️ Горячие клавиши</h2>
      <div class="keyboard-shortcuts-list">
        <div class="shortcut-item">
          <kbd>Ctrl+K</kbd>
          <span>Фокус на поиск журнала</span>
        </div>
        <div class="shortcut-item">
          <kbd>Escape</kbd>
          <span>Закрыть modal/panel/chat</span>
        </div>
        <div class="shortcut-item">
          <kbd>?</kbd>
          <span>Показать эту справку</span>
        </div>
        <div class="shortcut-item">
          <kbd>Ctrl+Enter</kbd>
          <span>Отправить форму</span>
        </div>
      </div>
      <button onclick="this.closest('.keyboard-shortcuts-modal').remove()" class="btn primary">
        Закрыть
      </button>
    </div>
  `;

  document.body.appendChild(modal);
  
  // Trap focus
  const content = modal.querySelector('.keyboard-shortcuts-content');
  if (content) {
    content.querySelector('button').focus();
  }
}

// CSS для accessibility
const accessibilityCSS = `
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border-width: 0;
  }

  .skip-link {
    position: absolute;
    top: -40px;
    left: 0;
    background: var(--primary);
    color: white;
    padding: 8px;
    z-index: 10000;
    transition: top 0.3s;
  }

  .skip-link:focus {
    top: 0;
  }

  .keyboard-shortcuts-modal {
    position: fixed;
    inset: 0;
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .keyboard-shortcuts-backdrop {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
  }

  .keyboard-shortcuts-content {
    position: relative;
    z-index: 1;
    max-width: 500px;
    width: 90vw;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: var(--space-6);
    box-shadow: var(--shadow-lg);
  }

  .keyboard-shortcuts-content h2 {
    margin-bottom: var(--space-4);
    color: var(--text);
  }

  .keyboard-shortcuts-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
  }

  .shortcut-item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .shortcut-item kbd {
    padding: 4px 8px;
    background: var(--surface-3);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    min-width: 80px;
    text-align: center;
  }

  .shortcut-item span {
    color: var(--text-muted);
  }
`;

// Inject CSS
const style = document.createElement('style');
style.textContent = accessibilityCSS;
document.head.appendChild(style);

// Глобальный экспорт
window.initializeAccessibility = initializeAccessibility;
window.announceToScreenReader = announceToScreenReader;
