/**
 * Factory OS — Tree Component
 * Рендеринг дерева задач (Vision → Epic → Story → Task → Atom)
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';

// Состояние фильтров
let hideDone = false;
let hideCancelled = true;
let expandedNodes = new Set();

/**
 * Tree Component
 */
export function TreeComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'tree' || state.activePage === 'dashboard') {
        // Нормализуем workItems - может быть в обёртке
        const workItemsArray = state.workItems?.workItems || Array.isArray(state.workItems) ? state.workItems : [];
        render(state.tree, workItemsArray);
      }
    });
  }
  
  function render(tree, workItems) {
    if (!container) return;
    
    if (!tree || tree.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="es-icon">🌳</div>
          <div class="es-title">Дерево задач пустое</div>
          <div class="es-sub">
            <button class="btn primary" onclick="window.openVisionModal?.()">+ Новый Vision</button>
          </div>
        </div>
      `;
      return;
    }
    
    container.innerHTML = `
      <div class="tree-toolbar">
        <button type="button" class="tt-btn ${hideDone ? 'on' : ''}" onclick="window.toggleTreeFilter('done')">
          Скрыть завершённые
        </button>
        <button type="button" class="tt-btn ${hideCancelled ? 'on' : ''}" onclick="window.toggleTreeFilter('cancelled')">
          Скрыть отменённые
        </button>
        <button type="button" class="tt-btn" onclick="window.expandAll?.()">Развернуть всё</button>
        <button type="button" class="tt-btn" onclick="window.collapseAll?.()">Свернуть</button>
        <span class="mono" style="font-size:11px;color:var(--text-faint)">
          Показано ${countVisible(tree, workItems)} из ${workItems?.length || 0}
        </span>
      </div>
      ${renderTreeNodes(tree, 0)}
    `;
    
    attachEventListeners();
  }
  
  function renderTreeNodes(nodes, depth) {
    if (!nodes || nodes.length === 0) return '';
    
    return nodes.map(node => {
      const isVisible = isNodeVisible(node);
      if (!isVisible) return '';
      
      const isExpanded = expandedNodes.has(node.id);
      const children = node.children || [];
      const hasChildren = children.length > 0;
      
      return `
        <div class="tree-node" data-id="${escapeHtml(node.id)}">
          <div class="tree-row ${store.state.selectedWorkItemId === node.id ? 'selected' : ''}" 
               data-id="${escapeHtml(node.id)}"
               onclick="window.selectWorkItem?.('${escapeHtml(node.id)}')">
            ${hasChildren ? `
              <span class="tree-toggle ${isExpanded ? 'open' : ''}" 
                    onclick="event.stopPropagation(); window.toggleTreeExpand('${escapeHtml(node.id)}')">▶</span>
            ` : '<span style="width:16px"></span>'}
            
            <span class="kind-badge k-${node.kind}">${escapeHtml(node.kind)}</span>
            
            <span class="tree-title" title="${escapeHtml(node.title)}">${escapeHtml(node.title)}</span>
            
            <span class="badge s-${node.status}">
              <span class="badge-dot"></span>
              ${getStatusLabel(node.status)}
            </span>
            
            ${node.last_event ? `
              <span class="tree-last-ev" title="${escapeHtml(node.last_event)}">
                ${escapeHtml(node.last_event?.slice(0, 30) || '')}
              </span>
            ` : ''}
          </div>
          
          ${hasChildren && isExpanded ? `
            <div class="tree-children open" style="margin-left:${(depth + 1) * 24}px">
              ${renderTreeNodes(children, depth + 1)}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');
  }
  
  function isNodeVisible(node) {
    if (hideDone && (node.status === 'done' || node.status === 'archived')) return false;
    if (hideCancelled && node.status === 'cancelled') return false;
    
    if (!node.children || node.children.length === 0) return true;
    
    // Если есть видимые дети — показываем узел
    return node.children.some(child => isNodeVisible(child));
  }
  
  function countVisible(nodes, workItems) {
    if (!workItems) return 0;
    return workItems.filter(w => isNodeVisible(w)).length;
  }
  
  function attachEventListeners() {
    // Клик по tree-row
    const rows = container.querySelectorAll('.tree-row');
    rows.forEach(row => {
      row.addEventListener('click', (e) => {
        const id = row.getAttribute('data-id');
        if (id) {
          store.selectWorkItem(id);
          // Открыть детальную панель если есть
          if (window.openDetail) {
            const wi = store.state.workItems.find(w => w.id === id);
            if (wi) window.openDetail(wi);
          }
        }
      });
    });
  }
  
  // Глобальные функции (для onclick из HTML)
  window.toggleTreeFilter = (which) => {
    if (which === 'done') hideDone = !hideDone;
    if (which === 'cancelled') hideCancelled = !hideCancelled;
    const { tree, workItems } = store.state;
    render(tree, workItems);
  };
  
  window.expandAll = () => {
    const { workItems } = store.state;
    if (!workItems) return;
    workItems.forEach(w => expandedNodes.add(w.id));
    const { tree } = store.state;
    render(tree, workItems);
  };
  
  window.collapseAll = () => {
    expandedNodes.clear();
    const { tree, workItems } = store.state;
    render(tree, workItems);
  };
  
  window.toggleTreeExpand = (id) => {
    if (expandedNodes.has(id)) {
      expandedNodes.delete(id);
    } else {
      expandedNodes.add(id);
    }
    const { tree, workItems } = store.state;
    render(tree, workItems);
  };
  
  window.selectWorkItem = (id) => {
    store.selectWorkItem(id);
    const { tree, workItems } = store.state;
    render(tree, workItems);
  };
  
  subscribeToStore();
  
  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers
function getStatusLabel(status) {
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
    cancelled: 'Cancelled'
  };
  return labels[status] || status;
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
