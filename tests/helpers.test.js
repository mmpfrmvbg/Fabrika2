/**
 * Factory OS — Unit Tests
 * Тесты для helper функций и компонентов
 */

import { describe, it, expect } from 'vitest';
import { 
  escapeHtml, 
  formatTime, 
  formatTimeAgo, 
  formatDuration, 
  getStatusLabel, 
  isEmpty, 
  getSafe,
  debounce,
  throttle
} from '../utils/helpers.js';

describe('Helper Functions', () => {
  describe('escapeHtml', () => {
    it('should escape HTML special characters', () => {
      expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
      expect(escapeHtml('"quote"')).toBe('&quot;quote&quot;');
      expect(escapeHtml('&amp;')).toBe('&amp;amp;');
    });

    it('should handle null/undefined', () => {
      expect(escapeHtml(null)).toBe('');
      expect(escapeHtml(undefined)).toBe('');
      expect(escapeHtml('')).toBe('');
    });

    it('should return plain text unchanged', () => {
      expect(escapeHtml('hello')).toBe('hello');
      expect(escapeHtml('123')).toBe('123');
    });
  });

  describe('formatTime', () => {
    it('should format ISO time to HH:MM', () => {
      const result = formatTime('2024-01-01T14:30:00Z');
      expect(result).toMatch(/\d{2}:\d{2}/);
    });

    it('should handle null/undefined', () => {
      expect(formatTime(null)).toBe('');
      expect(formatTime(undefined)).toBe('');
    });
  });

  describe('formatDuration', () => {
    it('should format seconds to readable format', () => {
      expect(formatDuration(30)).toBe('30с');
      expect(formatDuration(90)).toBe('1м 30с');
      expect(formatDuration(3600)).toBe('60м 0с');
    });

    it('should handle invalid input', () => {
      expect(formatDuration(null)).toBe('—');
      expect(formatDuration(undefined)).toBe('—');
      expect(formatDuration(NaN)).toBe('—');
    });
  });

  describe('getStatusLabel', () => {
    it('should return correct labels for known statuses', () => {
      expect(getStatusLabel('draft')).toBe('Draft');
      expect(getStatusLabel('done')).toBe('Done');
      expect(getStatusLabel('in_progress')).toBe('Running');
    });

    it('should return unknown status unchanged', () => {
      expect(getStatusLabel('unknown')).toBe('unknown');
    });
  });

  describe('isEmpty', () => {
    it('should detect empty values', () => {
      expect(isEmpty(null)).toBe(true);
      expect(isEmpty(undefined)).toBe(true);
      expect(isEmpty('')).toBe(true);
      expect(isEmpty([])).toBe(true);
    });

    it('should detect non-empty values', () => {
      expect(isEmpty('hello')).toBe(false);
      expect(isEmpty([1, 2, 3])).toBe(false);
      expect(isEmpty(0)).toBe(false);
    });
  });

  describe('getSafe', () => {
    it('should safely access nested properties', () => {
      const obj = { user: { address: { city: 'Moscow' } } };
      expect(getSafe(obj, 'user.address.city')).toBe('Moscow');
      expect(getSafe(obj, 'user.name', 'Unknown')).toBe('Unknown');
    });

    it('should return default value for missing paths', () => {
      expect(getSafe(null, 'a.b.c', 'default')).toBe('default');
    });
  });

  describe('debounce', () => {
    it('should delay function execution', async () => {
      let called = false;
      const fn = debounce(() => { called = true; }, 50);
      fn();
      expect(called).toBe(false);
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(called).toBe(true);
    });
  });

  describe('throttle', () => {
    it('should limit function calls', () => {
      let count = 0;
      const fn = throttle(() => { count++; }, 100);
      fn();
      fn();
      fn();
      expect(count).toBe(1);
    });
  });
});
