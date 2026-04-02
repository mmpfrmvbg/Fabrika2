/**
 * Factory OS — Storage Utility
 * Утилиты для работы с localStorage
 */

const STORAGE_PREFIX = 'fabrika2:';

export const Storage = {
  /**
   * Получить значение из localStorage
   * @param {string} key - Ключ
   * @param {*} defaultValue - Значение по умолчанию
   * @returns {*} Значение
   */
  get(key, defaultValue = null) {
    try {
      const item = localStorage.getItem(`${STORAGE_PREFIX}${key}`);
      if (item === null || item === undefined) {
        return defaultValue;
      }
      return JSON.parse(item);
    } catch (error) {
      console.error('[Storage] Get error:', error);
      return defaultValue;
    }
  },
  
  /**
   * Сохранить значение в localStorage
   * @param {string} key - Ключ
   * @param {*} value - Значение
   */
  set(key, value) {
    try {
      localStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(value));
    } catch (error) {
      console.error('[Storage] Set error:', error);
    }
  },
  
  /**
   * Удалить значение из localStorage
   * @param {string} key - Ключ
   */
  remove(key) {
    try {
      localStorage.removeItem(`${STORAGE_PREFIX}${key}`);
    } catch (error) {
      console.error('[Storage] Remove error:', error);
    }
  },
  
  /**
   * Очистить всё хранилище
   */
  clear() {
    try {
      const keys = Object.keys(localStorage);
      keys.forEach(key => {
        if (key.startsWith(STORAGE_PREFIX)) {
          localStorage.removeItem(key);
        }
      });
    } catch (error) {
      console.error('[Storage] Clear error:', error);
    }
  },
  
  /**
   * Получить все ключи хранилища
   * @returns {string[]} Массив ключей
   */
  keys() {
    try {
      const allKeys = Object.keys(localStorage);
      return allKeys.filter(key => key.startsWith(STORAGE_PREFIX));
    } catch (error) {
      console.error('[Storage] Keys error:', error);
      return [];
    }
  }
};

/**
 * Ключи хранилища (константы)
 */
export const StorageKeys = {
  MODE: 'mode',                    // 'autonomous' | 'developer'
  PAUSED: 'paused',                // boolean
  LAST_VISION_ID: 'lastVisionId',  // string
  PREFERENCES: 'preferences',      // object
  THEME: 'theme'                   // 'dark' | 'light'
};

/**
 * Инициализация хранилища при загрузке приложения
 */
export function initializeStorage() {
  // Значения по умолчанию
  const defaults = {
    [StorageKeys.MODE]: 'autonomous',
    [StorageKeys.PAUSED]: false,
    [StorageKeys.THEME]: 'dark'
  };
  
  // Установка дефолтов если не существует
  Object.entries(defaults).forEach(([key, value]) => {
    if (Storage.get(key) === null) {
      Storage.set(key, value);
    }
  });
}

/**
 * Глобальные функции для использования в UI
 */
window.Storage = Storage;
window.StorageKeys = StorageKeys;
window.initializeStorage = initializeStorage;
