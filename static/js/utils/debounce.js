/**
 * Factory OS — Debounce Utility
 * Задержка выполнения функции
 */

/**
 * Создаёт debounced версию функции
 * @param {Function} fn - функция для выполнения
 * @param {number} delay - задержка в мс
 * @returns {Function} debounced функция
 */
export function debounce(fn, delay = 300) {
  let timeoutId;
  
  return (...args) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => {
      fn(...args);
    }, delay);
  };
}

/**
 * Создаёт throttled версию функции
 * @param {Function} fn - функция для выполнения
 * @param {number} limit - минимальный интервал в мс
 * @returns {Function} throttled функция
 */
export function throttle(fn, limit = 100) {
  let inThrottle;
  
  return (...args) => {
    if (!inThrottle) {
      fn(...args);
      inThrottle = true;
      setTimeout(() => {
        inThrottle = false;
      }, limit);
    }
  };
}
