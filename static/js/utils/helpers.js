/**
 * Factory OS — Helper Utilities
 * Общие вспомогательные функции для переиспользования
 */

/**
 * Экранирование HTML для защиты от XSS
 * @param {string} text - Текст для экранирования
 * @returns {string} Экранированный текст
 */
export function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Форматирование времени из ISO строки
 * @param {string} iso - ISO строка времени
 * @returns {string} Форматированное время (HH:MM)
 */
export function formatTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString('ru-RU', { 
    hour: '2-digit', 
    minute: '2-digit' 
  });
}

/**
 * Форматирование времени "как давно"
 * @param {string} iso - ISO строка времени
 * @returns {string} "X мин назад", "X ч назад", etc.
 */
export function formatTimeAgo(iso) {
  if (!iso) return '';
  const seconds = Math.floor((new Date() - new Date(iso)) / 1000);
  if (seconds < 60) return `${seconds}с назад`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}м назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}ч назад`;
  const days = Math.floor(hours / 24);
  return `${days}д назад`;
}

/**
 * Форматирование длительности из секунд
 * @param {number} seconds - Длительность в секундах
 * @returns {string} "Xм Yс" или "Xс"
 */
export function formatDuration(seconds) {
  if (!seconds || !Number.isFinite(seconds)) return '—';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
}

/**
 * Получение читаемого статуса
 * @param {string} status - Статус задачи
 * @returns {string} Читаемый статус
 */
export function getStatusLabel(status) {
  const labels = {
    draft: 'Draft',
    planned: 'Planned',
    ready_for_judge: '→ Judge',
    judge_rejected: 'Judge ✗',
    ready_for_work: 'Ready',
    in_progress: 'Running',
    in_review: 'In Review',
    review_rejected: 'Review ✗',
    blocked: 'Blocked',
    done: 'Done',
    cancelled: 'Cancelled',
    archived: 'Archived',
    queued: 'Queued',
    running: 'Running',
    failed: 'Failed',
    rejected: 'Rejected'
  };
  return labels[status] || status;
}

/**
 * Показ toast уведомления
 * @param {string} message - Сообщение
 * @param {'ok'|'err'} kind - Тип уведомления
 */
export function showFactoryToast(message, kind = 'ok') {
  const el = document.getElementById('factory-toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'factory-toast visible ' + (kind === 'err' ? 'err' : 'ok');
  clearTimeout(el._hideT);
  el._hideT = setTimeout(() => { el.classList.remove('visible'); }, 3000);
}

/**
 * Проверка на пустое значение
 * @param {*} value - Значение для проверки
 * @returns {boolean} true если пустое
 */
export function isEmpty(value) {
  return value === null || value === undefined || value === '' || (Array.isArray(value) && value.length === 0);
}

/**
 * Safe access к свойству объекта
 * @param {Object} obj - Объект
 * @param {string} path - Путь к свойству (например 'user.address.city')
 * @param {*} defaultValue - Значение по умолчанию
 * @returns {*} Значение или default
 */
export function getSafe(obj, path, defaultValue = null) {
  return path.split('.').reduce((acc, part) => acc && acc[part], obj) ?? defaultValue;
}

/**
 * Debounce функция
 * @param {Function} fn - Функция
 * @param {number} delay - Задержка в мс
 * @returns {Function} Debounced функция
 */
export function debounce(fn, delay = 300) {
  let timeoutId;
  return (...args) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
}

/**
 * Throttle функция
 * @param {Function} fn - Функция
 * @param {number} limit - Лимит в мс
 * @returns {Function} Throttled функция
 */
export function throttle(fn, limit = 100) {
  let inThrottle;
  return (...args) => {
    if (!inThrottle) {
      fn(...args);
      inThrottle = true;
      setTimeout(() => inThrottle = false, limit);
    }
  };
}
