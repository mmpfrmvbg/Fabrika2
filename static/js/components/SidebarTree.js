/**
 * Factory OS — Sidebar Quick Jump Component
 * Мини-дерево для быстрой навигации по Vision из sidebar
 */

import { store, subscribe } from '../state/store.js';
import { escapeHtml, getStatusLabel } from '../utils/helpers.js';

/**
 * Sidebar Quick Jump Component
 * @param {HTMLElement} container - контейнер для компонента
 */
export function SidebarTreeComponent(container) {
  if (!container) return null;
  
  let unsubscribe = null;

  // ═══════════════════════════════════════════════════════
  // SUBSCRIBE TO STORE
  // ═══════════════════════════════════════════════════════

  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.visions && state.visions.length > 0) {
        render(state.visions);
      }
    });
  }

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render(visions) {
    if (!container) return;
    
    // Нормализуем visions
    const visionsArray = visions?.visions || Array.isArray(visions) ? visions : [];
    
    if (visionsArray.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:10px;padding:var(--space-2)">Нет Vision</div>';
      return;
    }
    
    // Показываем первые 5 Vision
    const visibleVisions = visionsArray.slice(0, 5);
    
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:2px">
        ${visibleVisions.map(vision => renderVisionNode(vision)).join('')}
        ${visionsArray.length > 5 ? `
          <div style="font-size:10px;color:var(--text-muted);padding:var(--space-1) var(--space-2)">
            + ещё ${visionsArray.length - 5} Vision
          </div>
        ` : ''}
      </div>
    `;
    
    attachEventListeners();
  }
  
  function renderVisionNode(vision) {
    const isSelected = store.state.selectedWorkItemId === vision.id;
    const statusClass = `s-${vision.status}`;
    const statusLabel = getStatusLabel(vision.status);
    
    return `
      <div 
        class="sidebar-tree-node ${isSelected ? 'selected' : ''}" 
        data-id="${vision.id}"
        style="display:flex;align-items:center;gap:6px;padding:4px 8px;border-radius:var(--radius-sm);cursor:pointer;font-size:10px;color:var(--text-muted);transition:background var(--transition),color var(--transition)"
        title="${escapeHtml(vision.title || vision.id)}"
      >
        <span class="kind-badge k-vision" style="font-size:8px;padding:1px 4px">V</span>
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(vision.title || 'Без названия')}</span>
        <span class="badge ${statusClass}" style="font-size:8px;padding:1px 4px">${statusLabel}</span>
      </div>
    `;
  }
  
  // ═══════════════════════════════════════════════════════
  // EVENT LISTENERS
  // ═══════════════════════════════════════════════════════

  function attachEventListeners() {
    const nodes = container.querySelectorAll('.sidebar-tree-node');
    nodes.forEach(node => {
      node.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = node.getAttribute('data-id');
        if (id) {
          handleVisionClick(id);
        }
      });
    });
  }
  
  function handleVisionClick(visionId) {
    // Переход на страницу Tree
    if (window.goPage) {
      window.goPage('tree');
    }
    
    // Выбор Vision
    store.selectWorkItem(visionId);
    
    // Обновление UI
    const { visions, workItems } = store.state;
    if (window.renderTree) {
      window.renderTree(workItems.tree, workItems);
    }
    
    // Открыть detail panel если есть
    if (window.openDetail) {
      const wi = store.state.workItems?.find(w => w.id === visionId);
      if (wi) window.openDetail(wi);
    }
    
    // Закрыть accordion (опционально)
    const accordionBody = container.closest('.sidebar-acc-body');
    if (accordionBody) {
      accordionBody.classList.remove('open');
      const toggle = document.getElementById('sidebar-quickjump-toggle');
      if (toggle) toggle.textContent = '▸ Quick jump';
    }
    
    showFactoryToast(`Переход к Vision: ${visionId.slice(0, 8)}...`, 'ok');
  }
  
  // ═══════════════════════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════════════════════

  subscribeToStore();
  
  return () => {
    if (unsubscribe) unsubscribe();
  };
}

// Helpers импортируются из utils/helpers.js
