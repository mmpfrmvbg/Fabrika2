/**
 * Factory OS — Child Task Modal Component
 * Модальное окно создания дочерней задачи (Epic/Story/Task/Atom)
 */

import { api } from '../api/client.js';

// Состояние модального окна
let modalState = {
  parentId: null,
  childKind: '',
  isOpen: false
};

/**
 * Child Task Modal Component
 * @param {HTMLElement} container - контейнер для модального окна
 */
export function ChildTaskModalComponent(container) {
  if (!container) return null;

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render() {
    if (!modalState.isOpen) {
      container.classList.remove('open');
      container.setAttribute('aria-hidden', 'true');
      return;
    }

    container.classList.add('open');
    container.setAttribute('aria-hidden', 'false');

    const isAtom = modalState.childKind === 'atom';
    
    container.innerHTML = `
      <div class="vision-modal-backdrop" onclick="window.closeChildTaskModal()"></div>
      <div class="vision-modal-card card" style="padding:var(--space-4)">
        <div style="font-weight:600;margin-bottom:var(--space-2)" id="child-modal-heading">
          Новая дочерняя задача
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:var(--space-3)" id="child-modal-kind-hint">
          Тип: ${modalState.childKind}
        </div>
        
        <label style="display:block;font-size:var(--text-xs);color:var(--text-muted);margin-bottom:4px">
          Заголовок *
        </label>
        <input 
          id="child-modal-title" 
          type="text" 
          style="width:100%;margin-bottom:var(--space-3);padding:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-md);color:var(--text)" 
          placeholder="Введите заголовок задачи..."
        />
        
        <label style="display:block;font-size:var(--text-xs);color:var(--text-muted);margin-bottom:4px">
          Описание
        </label>
        <textarea 
          id="child-modal-desc" 
          rows="3" 
          style="width:100%;margin-bottom:var(--space-3);padding:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-md);color:var(--text);resize:vertical" 
          placeholder="Описание (опционально)"
        ></textarea>
        
        ${isAtom ? `
          <div id="child-modal-files-wrap" style="margin-bottom:var(--space-3)">
            <label style="display:block;font-size:var(--text-xs);color:var(--text-muted);margin-bottom:4px">
              Файлы атома (по одному на строку, intent=modify) *
            </label>
            <textarea 
              id="child-modal-files" 
              rows="4" 
              style="width:100%;padding:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-md);color:var(--text);font-family:var(--font-mono);font-size:11px" 
              placeholder="factory/hello_qwen.py
src/main.py
tests/test_main.py"
            ></textarea>
          </div>
        ` : ''}
        
        <div style="display:flex;gap:var(--space-2);justify-content:flex-end">
          <button 
            type="button" 
            onclick="window.closeChildTaskModal()" 
            style="padding:8px 14px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-md);cursor:pointer;color:var(--text-muted)"
          >
            Отмена
          </button>
          <button 
            type="button" 
            onclick="window.submitChildTaskModal()" 
            style="padding:8px 14px;background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:var(--radius-md);cursor:pointer"
          >
            Создать
          </button>
        </div>
      </div>
    `;

    // Фокус на поле заголовка после рендера
    setTimeout(() => {
      const titleInput = document.getElementById('child-modal-title');
      if (titleInput) titleInput.focus();
    }, 100);
  }

  // ═══════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════

  return {
    open(parentId, childKind) {
      modalState.parentId = parentId;
      modalState.childKind = childKind || '';
      modalState.isOpen = true;
      render();
    },

    close() {
      modalState.isOpen = false;
      modalState.parentId = null;
      modalState.childKind = '';
      render();
    },

    async submit() {
      const titleEl = document.getElementById('child-modal-title');
      const descEl = document.getElementById('child-modal-desc');
      const filesEl = document.getElementById('child-modal-files');

      const title = titleEl?.value.trim() || '';
      const description = descEl?.value.trim() || '';
      
      if (!title) {
        showFactoryToast('Нужен заголовок', 'err');
        return;
      }

      if (!modalState.parentId) {
        showFactoryToast('Нет родительской задачи', 'err');
        return;
      }

      const body = {
        title,
        description: description || undefined,
        kind: modalState.childKind
      };

      // Для атома добавляем файлы
      if (modalState.childKind === 'atom') {
        const raw = filesEl?.value || '';
        const files = raw.split(/\n/).map(s => s.trim()).filter(Boolean);
        
        if (files.length === 0) {
          showFactoryToast('Атом должен иметь файлы', 'err');
          return;
        }
        
        body.files = files.map(path => ({
          path,
          intent: 'modify'
        }));
      }

      try {
        const result = await api.createChild(modalState.parentId, body);
        
        showFactoryToast(`Создано: ${result.id || result.work_item_id || 'задача'}`, 'ok');
        
        // Закрываем модальное окно
        this.close();
        
        // Очищаем форму
        if (titleEl) titleEl.value = '';
        if (descEl) descEl.value = '';
        if (filesEl) filesEl.value = '';
        
        // Обновляем дерево задач
        if (window.loadInitialData) {
          await window.loadInitialData();
        }
        
        // Если есть глобальная функция обновления — вызываем
        if (window.refreshLiveData) {
          await window.refreshLiveData();
        }
        
      } catch (error) {
        showFactoryToast(`Ошибка: ${error.message}`, 'err');
      }
    }
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS (для onclick из HTML)
// ═══════════════════════════════════════════════════════

let childTaskModalInstance = null;

window.openChildTaskModal = (parentId, childKind) => {
  const container = document.getElementById('child-task-modal');
  if (!container) {
    showFactoryToast('Контейнер модального окна не найден', 'err');
    return;
  }
  
  if (!childTaskModalInstance) {
    childTaskModalInstance = ChildTaskModalComponent(container);
  }
  
  if (childTaskModalInstance) {
    childTaskModalInstance.open(parentId, childKind);
  }
};

window.closeChildTaskModal = () => {
  if (childTaskModalInstance) {
    childTaskModalInstance.close();
  }
};

window.submitChildTaskModal = () => {
  if (childTaskModalInstance) {
    childTaskModalInstance.submit();
  }
};

function showFactoryToast(message, kind = 'ok') {
  const el = document.getElementById('factory-toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'factory-toast visible ' + (kind === 'err' ? 'err' : 'ok');
  clearTimeout(el._hideT);
  el._hideT = setTimeout(() => { el.classList.remove('visible'); }, 3000);
}
