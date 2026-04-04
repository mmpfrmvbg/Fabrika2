/**
 * Factory OS — Tree Component
 * Рендеринг дерева задач (Vision → Epic → Story → Task → Atom)
 */

import { store, subscribe } from '../state/store.js';
import { escapeHtml, getStatusLabel } from '../utils/helpers.js';
import { api } from '../api/client.js';

// Состояние фильтров
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
    const filters = store.state.treeFilters || {};
    const searchQuery = String(filters.searchQuery || '').trim().toLowerCase();
    const selectedStatus = String(filters.status || 'all').toLowerCase();
    const hideDone = !!filters.hideDone;
    const hideCancelled = !!filters.hideCancelled;
    const filteredCount = countVisible(workItems, filters);
    const totalCount = workItems?.length || 0;
    const hasActiveFilters = isFiltersActive(filters);
    const availableStatuses = getAvailableStatuses(workItems);
    
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
        <input
          type="text"
          class="input"
          placeholder="Search by title, description, id..."
          value="${escapeHtml(filters.searchQuery || '')}"
          oninput="window.updateTreeSearch(this.value)"
          style="min-width:220px;max-width:320px"
        />
        <select
          class="select"
          onchange="window.updateTreeStatus(this.value)"
          style="min-width:160px"
        >
          <option value="all" ${selectedStatus === 'all' ? 'selected' : ''}>All statuses</option>
          ${availableStatuses.map(status => `
            <option value="${escapeHtml(status)}" ${selectedStatus === status ? 'selected' : ''}>
              ${escapeHtml(getStatusLabel(status))}
            </option>
          `).join('')}
        </select>
        <button type="button" class="tt-btn" onclick="window.clearTreeFilters?.()">Clear filters</button>
        <button type="button" class="tt-btn ${hideDone ? 'on' : ''}" onclick="window.toggleTreeFilter('done')">
          Скрыть завершённые
        </button>
        <button type="button" class="tt-btn ${hideCancelled ? 'on' : ''}" onclick="window.toggleTreeFilter('cancelled')">
          Скрыть отменённые
        </button>
        <button type="button" class="tt-btn" onclick="window.expandAll?.()">Развернуть всё</button>
        <button type="button" class="tt-btn" onclick="window.collapseAll?.()">Свернуть</button>
        <span class="mono" style="font-size:11px;color:var(--text-faint)">
          ${hasActiveFilters ? `Showing ${filteredCount} of ${totalCount} items` : `Showing ${totalCount} items`}
        </span>
      </div>
      ${renderTreeNodes(tree, 0, filters)}
    `;
    
    attachEventListeners();
  }
  
  function renderTreeNodes(nodes, depth, filters) {
    if (!nodes || nodes.length === 0) return '';
    
    return nodes.map(node => {
      const isVisible = isNodeVisible(node, filters);
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

            ${node.last_event ? `
              <span class="tree-last-ev" title="${escapeHtml(node.last_event)}">
                ${escapeHtml(node.last_event?.slice(0, 30) || '')}
              </span>
            ` : ''}

            <div class="tree-meta" onclick="event.stopPropagation()">
              ${node.kind === 'atom' ? renderAtomControls(node) : ''}
              <button type="button" 
                      class="btn-ask-qwen" 
                      onclick="window.askQwenAboutEntity('work_item', '${node.id}')"
                      title="Спросить Qwen про эту задачу"
                      style="background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:var(--radius-sm);padding:2px 6px;font-size:9px;cursor:pointer;margin-right:4px">
                💬
              </button>
              ${node.kind === 'vision' ? renderVisionPipeline(node) : ''}
              <span class="badge s-${node.status}">
                <span class="badge-dot"></span>
                ${getStatusLabel(node.status)}
              </span>
            </div>
          </div>

          ${hasChildren && isExpanded ? `
            <div class="tree-children open" style="margin-left:${(depth + 1) * 24}px">
              ${renderTreeNodes(children, depth + 1, filters)}
            </div>
          ` : ''}
        </div>
      `;
    }).join('');
  }
  
  function isNodeVisible(node, filters = {}) {
    const query = String(filters.searchQuery || '').trim().toLowerCase();
    const statusFilter = String(filters.status || 'all').toLowerCase();
    const hideDone = !!filters.hideDone;
    const hideCancelled = !!filters.hideCancelled;

    if (hideDone && (node.status === 'done' || node.status === 'archived')) return false;
    if (hideCancelled && node.status === 'cancelled') return false;

    const nodeStatus = String(node.status || '').toLowerCase();
    const inStatus = statusFilter === 'all' || nodeStatus === statusFilter;
    const inSearch = !query || [
      node.id,
      node.title,
      node.description
    ].some(value => String(value || '').toLowerCase().includes(query));
    const isSelfMatch = inStatus && inSearch;
    
    if (!node.children || node.children.length === 0) return isSelfMatch;
    
    // Показываем родителя, если совпал сам или есть совпадающие дети
    return isSelfMatch || node.children.some(child => isNodeVisible(child, filters));
  }
  
  function countVisible(workItems, filters) {
    if (!workItems) return 0;
    return workItems.filter(w => isNodeVisible(w, filters)).length;
  }

  function isFiltersActive(filters = {}) {
    return Boolean(
      String(filters.searchQuery || '').trim() ||
      String(filters.status || 'all').toLowerCase() !== 'all'
    );
  }

  function getAvailableStatuses(workItems = []) {
    const statuses = [...new Set(
      workItems
        .map(item => String(item?.status || '').toLowerCase())
        .filter(Boolean)
    )];
    return statuses.sort();
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
            const wi = store.state.workItems?.find(w => w.id === id);
            if (wi) window.openDetail(wi);
          }
        }
      });
    });
  }
  
  // Глобальные функции (для onclick из HTML)
  window.toggleTreeFilter = (which) => {
    const current = store.state.treeFilters || {};
    if (which === 'done') store.updateTreeFilters({ hideDone: !current.hideDone });
    if (which === 'cancelled') store.updateTreeFilters({ hideCancelled: !current.hideCancelled });
    const { tree, workItems } = store.state;
    render(tree, workItems);
  };

  window.updateTreeSearch = (value) => {
    store.updateTreeFilters({ searchQuery: String(value || '') });
  };

  window.updateTreeStatus = (value) => {
    store.updateTreeFilters({ status: String(value || 'all').toLowerCase() });
  };

  window.clearTreeFilters = () => {
    store.clearTreeFilters();
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

// ═══════════════════════════════════════════════════════
// ATOM CONTROLS (Run button)
// ═══════════════════════════════════════════════════════

function renderAtomControls(atom) {
  const status = atom.status?.toLowerCase();
  const isReady = status === 'ready_for_work';
  const isInProgress = status === 'in_progress';
  
  if (isReady) {
    return `
      <button type="button" 
              class="btn-dash-run" 
              onclick="window.runWorkItemFromTree('${atom.id}')"
              title="POST /api/work-items/${atom.id}/run">
        ▶ Запустить
      </button>
    `;
  }
  
  if (isInProgress) {
    return `
      <span class="tree-run-hint" title="Forge в работе">⏳</span>
    `;
  }
  
  return '';
}

// ═══════════════════════════════════════════════════════
// VISION PIPELINE BAR
// ═══════════════════════════════════════════════════════

function renderVisionPipeline(vision) {
  const atoms = collectAtomsUnderVision(vision.id);
  if (atoms.length === 0) return '';
  
  const pending = new Set(['draft', 'planned', 'ready_for_work']);
  const inProgress = new Set(['in_progress', 'in_review', 'ready_for_judge']);
  
  let nPending = 0;
  let nInProgress = 0;
  let nDone = 0;
  
  atoms.forEach(a => {
    const s = String(a.status || '').toLowerCase();
    if (s === 'done' || s === 'archived' || s === 'cancelled') {
      nDone += 1;
    } else if (inProgress.has(s)) {
      nInProgress += 1;
    } else {
      nPending += 1;
    }
  });
  
  const total = atoms.length;
  const pctDone = total ? Math.round((nDone / total) * 100) : 0;
  
  const wd = (nDone / total) * 100;
  const wi = (nInProgress / total) * 100;
  const wp = (nPending / total) * 100;
  
  return `
    <div class="tree-vision-pipeline" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;width:100%;margin-top:4px">
      <div style="flex:1;min-width:120px;height:6px;border-radius:3px;border:1px solid var(--border);background:var(--surface-2);overflow:hidden;display:flex" title="Прогресс: Done · In Progress · Pending">
        <div style="width:${wd}%;background:var(--success);opacity:0.9"></div>
        <div style="width:${wi}%;background:var(--warning);opacity:0.85"></div>
        <div style="width:${wp}%;background:var(--surface-4);opacity:0.95"></div>
      </div>
      <span class="mono-id" style="font-size:9px;color:var(--text-muted);white-space:nowrap">${nDone}/${total} (${pctDone}%)</span>
    </div>
  `;
}

function collectAtomsUnderVision(visionId) {
  const atoms = [];
  const workItems = store.state.workItems || [];
  
  const walk = (id) => {
    const children = workItems.filter(w => w.parent_id === id);
    for (const child of children) {
      const k = String(child.kind || '').toLowerCase();
      if (k === 'atom' || k === 'atm_change') {
        atoms.push(child);
      }
      walk(child.id);
    }
  };
  
  walk(visionId);
  return atoms;
}
