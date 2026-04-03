/**
 * Factory OS — Vision Creator Component
 * Модальное окно создания новой идеи (Vision)
 */

import { api } from '../api/client.js';
import { debounce } from '../utils/debounce.js';
import { showFactoryToast } from '../utils/helpers.js';
import { autoDecomposeVision } from '../autonomous/autoDecompose.js';

// Состояние компонента
let isEstimating = false;
let estimation = null;

/**
 * Vision Creator Component
 * @param {HTMLElement} container - контейнер для модального окна
 */
export function VisionCreatorComponent(container) {
  if (!container) return null;
  
  let isOpen = false;

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

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
      <div class="vc-backdrop" onclick="window.closeVisionCreator()"></div>
      <div class="vc-modal-card">
        <div class="vc-header">
          <div class="vc-title">
            <span class="vc-icon">✨</span>
            <h2>Создание новой идеи</h2>
          </div>
          <button class="vc-close-btn" onclick="window.closeVisionCreator()" title="Закрыть">×</button>
        </div>
        
        <div class="vc-body">
          ${renderForm()}
          ${renderEstimation()}
        </div>
        
        <div class="vc-footer">
          <button type="button" class="vc-btn-cancel" onclick="window.closeVisionCreator()">Отмена</button>
          <button type="button" class="vc-btn-create" onclick="window.submitVision()" ${isEstimating ? 'disabled' : ''}>
            ${isEstimating ? '⏳ Оценка...' : '✅ Создать и запустить'}
          </button>
        </div>
      </div>
    `;
    
    // Фокус на поле заголовка после рендера
    setTimeout(() => {
      const titleInput = document.getElementById('vc-title-input');
      if (titleInput) titleInput.focus();
    }, 100);
  }

  function renderForm() {
    return `
      <div class="vc-form">
        <div class="vc-form-group">
          <label for="vc-title-input" class="vc-label">
            Что хотите сделать? *
          </label>
          <input 
            id="vc-title-input" 
            type="text" 
            class="vc-input"
            placeholder="Хочу рефакторинг auth модуля"
            oninput="window.onVisionTitleChange()"
          />
        </div>
        
        <div class="vc-form-group">
          <label for="vc-desc-input" class="vc-label">
            Описание (опционально)
          </label>
          <textarea 
            id="vc-desc-input" 
            rows="4" 
            class="vc-textarea"
            placeholder="Нужно улучшить обработку ошибок, добавить логирование..."
            oninput="window.onVisionTitleChange()"
          ></textarea>
        </div>
      </div>
    `;
  }

  function renderEstimation() {
    if (!estimation) return '';
    
    return `
      <div class="vc-estimation-card">
        <div class="vc-estimation-header">
          <span class="vc-estimation-icon">🤖</span>
          <div class="vc-estimation-title">Фабрика оценила работу:</div>
        </div>
        
        <div class="vc-estimation-grid">
          <div class="vc-estimation-item">
            <span class="vc-estimation-value">${estimation.tasks || '~18'}</span>
            <span class="vc-estimation-label">задач</span>
          </div>
          <div class="vc-estimation-item">
            <span class="vc-estimation-value">${estimation.time || '~3-4 часа'}</span>
            <span class="vc-estimation-label">времени</span>
          </div>
          <div class="vc-estimation-item">
            <span class="vc-estimation-value">${estimation.files || '~12'}</span>
            <span class="vc-estimation-label">файлов</span>
          </div>
        </div>
        
        <div class="vc-estimation-note">
          ⚠️ Это приблизительная оценка. Фактическое время может отличаться.
        </div>
      </div>
    `;
  }

  // ═══════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════

  return {
    open() {
      isOpen = true;
      estimation = null;
      isEstimating = false;
      render();
    },
    
    close() {
      isOpen = false;
      render();
    },
    
    async submit() {
      const titleEl = document.getElementById('vc-title-input');
      const descEl = document.getElementById('vc-desc-input');
      
      const title = titleEl?.value.trim() || '';
      const description = descEl?.value.trim() || '';
      
      if (!title) {
        showFactoryToast('Введите заголовок', 'err');
        return;
      }
      
      try {
        // 1. Создаём Vision
        const vision = await api.createVision({ title, description });
        showFactoryToast(`Vision создан: ${vision.id?.slice(0, 8)}...`, 'ok');
        
        // 2. Закрываем modal
        this.close();
        
        // 3. Переключаем на Autonomous Mode
        if (window.switchToAutonomousMode) {
          window.switchToAutonomousMode();
        }
        
        // 4. Авто-декомпозиция (best-effort, не блокирует UX при ошибке)
        try {
          await autoDecomposeVision(vision.id, title, description);
        } catch (decomposeError) {
          console.warn('[VisionCreator] autoDecompose skipped:', decomposeError);
        }
        
        showFactoryToast('Фабрика начинает работу...', 'ok');
        
      } catch (error) {
        showFactoryToast(`Ошибка: ${error.message}`, 'err');
      }
    },
    
    async estimate() {
      const titleEl = document.getElementById('vc-title-input');
      const title = titleEl?.value.trim() || '';
      
      if (!title || title.length < 5) return;
      
      isEstimating = true;
      render();
      
      try {
        // Локальная оценка сложности на основе длины/структуры описания.
        estimation = estimateVisionScope(title, document.getElementById('vc-desc-input')?.value || '');
        
      } catch (error) {
        console.error('Estimation failed:', error);
      } finally {
        isEstimating = false;
        render();
      }
    }
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS
// ═══════════════════════════════════════════════════════

let visionCreatorInstance = null;

window.openVisionCreator = () => {
  const container = document.getElementById('vision-creator-modal');
  if (!container) {
    showFactoryToast('Контейнер Vision Creator не найден', 'err');
    return;
  }
  
  if (!visionCreatorInstance) {
    visionCreatorInstance = VisionCreatorComponent(container);
  }
  
  if (visionCreatorInstance) {
    visionCreatorInstance.open();
  }
};

window.closeVisionCreator = () => {
  if (visionCreatorInstance) {
    visionCreatorInstance.close();
  }
};

window.submitVision = () => {
  if (visionCreatorInstance) {
    visionCreatorInstance.submit();
  }
};

window.onVisionTitleChange = debounce(() => {
  if (visionCreatorInstance && !isEstimating) {
    visionCreatorInstance.estimate();
  }
}, 500);

function estimateVisionScope(title, description) {
  const t = (title || '').trim();
  const d = (description || '').trim();
  const words = `${t} ${d}`.split(/\s+/).filter(Boolean).length;
  const lines = d.split('\n').map(s => s.trim()).filter(Boolean).length;
  const complexity = Math.max(1, Math.min(5, Math.ceil(words / 25) + Math.ceil(lines / 6)));
  const tasks = 6 + complexity * 4;
  const files = 3 + complexity * 2;
  const hours = 1 + complexity;
  return {
    tasks: `~${tasks}`,
    files: `~${files}`,
    time: `~${hours}-${hours + 1} ч`,
  };
}

// Helpers импортируются из utils/helpers.js
