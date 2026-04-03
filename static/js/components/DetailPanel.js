/**
 * Factory OS — Detail Panel Component
 * Панель деталей задачи с breadcrumbs, actions, files, comments, timeline
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';
import { escapeHtml, getStatusLabel, formatTime } from '../utils/helpers.js';

// Состояние компонента
let currentWorkItem = null;
let isEditingTitle = false;
let detailData = {
  children: [],
  runs: [],
  events: [],
  decisions: [],
  comments: []
};

/**
 * Detail Panel Component
 * @param {HTMLElement} container - контейнер для панели
 */
export function DetailPanelComponent(container) {
  let unsubscribe = null;

  // ═══════════════════════════════════════════════════════
  // SUBSCRIBE TO STORE
  // ═══════════════════════════════════════════════════════

  function subscribeToStore() {
    unsubscribe = subscribe((state, changes) => {
      // Открытие/закрытие панели
      if (state.selectedWorkItemId) {
        container.classList.add('open');
        loadDetailData(state.selectedWorkItemId);
      } else {
        container.classList.remove('open');
      }

      // Обновление данных после загрузки
      if (changes.detailData || changes._forceRender) {
        render();
      }
    });
  }

  // ═══════════════════════════════════════════════════════
  // LOAD DETAIL DATA
  // ═══════════════════════════════════════════════════════

  async function loadDetailData(workItemId) {
    if (!workItemId) return;

    const wi = store.state.workItems?.find(w => w.id === workItemId);
    if (!wi) return;

    currentWorkItem = wi;

    try {
      // Загружаем расширенные данные
      const detail = await api.getWorkItem(workItemId).catch(() => null);
      const runsRes = await api.getRuns({ work_item_id: workItemId }).catch(() => ({ items: [] }));
      const eventsRes = await api.getWorkItemEvents(workItemId).catch((e) => {
        console.warn('[DetailPanel] Events not available:', e.message);
        return { items: [] };
      });

      detailData = {
        children: detail?.children || [],
        runs: runsRes?.items || [],
        events: eventsRes?.items || [],
        decisions: [], // TODO: загрузить из API
        comments: detail?.comments || []
      };

      render();
    } catch (error) {
      console.error('[DetailPanel] Failed to load data:', error);
      render(); // Рендерим с тем что есть
    }
  }

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render() {
    if (!container || !currentWorkItem) return;

    container.innerHTML = renderHTML();
    attachEventListeners();
  }

  function renderHTML() {
    const wi = currentWorkItem;
    const { children, runs, events, comments } = detailData;

    return `
      <div class="detail-panel-header">
        <div class="dp-header-stack">
          <div class="dp-title-wrap">
            ${isEditingTitle ? renderTitleEdit() : renderTitleDisplay()}
            ${renderBreadcrumbs(wi)}
          </div>
          <div class="dp-badges-row">
            ${renderBadges(wi)}
            <button type="button" 
                    onclick="window.askQwenAboutEntity('work_item', '${wi.id}')"
                    title="Спросить Qwen про эту задачу"
                    style="background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:var(--radius-sm);padding:4px 8px;font-size:10px;cursor:pointer;display:flex;align-items:center;gap:4px">
              💬 Спросить Qwen
            </button>
          </div>
          <div class="dp-next-action">
            ${renderNextAction(wi)}
          </div>
          <div class="dp-actions-row">
            ${renderActionButtons(wi)}
          </div>
        </div>
        <button class="detail-panel-close" onclick="window.closeDetail()" title="Закрыть" aria-label="Закрыть панель деталей">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/>
            <line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div class="detail-panel-body" id="dp-body">
        ${renderFirstScreen(wi, children, runs)}
        <div style="height:14px"></div>
        ${renderAccordions(wi, children, runs, events, comments)}
      </div>
    `;
  }

  function renderTitleDisplay() {
    return `
      <div id="dp-title" style="font-size:var(--text-sm);font-weight:600;line-height:1.25">
        ${escapeHtml(currentWorkItem.title || 'Без названия')}
      </div>
    `;
  }

  function renderTitleEdit() {
    return `
      <div id="dp-title-edit-row" style="display:flex;flex-direction:column;gap:6px;width:100%">
        <input 
          id="dp-title-input" 
          type="text" 
          value="${escapeHtml(currentWorkItem.title || '')}"
          style="width:100%;padding:8px;border-radius:var(--radius-sm);border:1px solid var(--border);background:var(--surface-2);color:var(--text);font-size:13px;box-sizing:border-box" 
        />
        <div style="display:flex;gap:8px">
          <button type="button" class="btn primary" style="font-size:11px;padding:6px 10px" onclick="window.saveDetailTitleEdit()">Save</button>
          <button type="button" class="btn" style="font-size:11px;padding:6px 10px" onclick="window.cancelDetailTitleEdit()">Cancel</button>
        </div>
      </div>
    `;
  }

  function renderBreadcrumbs(wi) {
    const chain = buildBreadcrumbsChain(wi);
    if (chain.length === 0) return '';

    return `
      <div id="dp-breadcrumbs" class="dp-breadcrumbs">
        ${chain.map((item, idx) => {
          const isCur = idx === chain.length - 1;
          return isCur 
            ? `<span class="crumb current">${escapeHtml(item.label)}</span>`
            : `<span class="crumb" onclick="window.navigateDetailTo('${item.id}')">${escapeHtml(item.label)}</span>`;
        }).join('<span style="opacity:0.6"> → </span>')}
      </div>
    `;
  }

  function buildBreadcrumbsChain(wi) {
    const chain = [];
    const seen = new Set();
    let curId = wi.id;
    
    for (let i = 0; i < 16; i++) {
      if (!curId || seen.has(curId)) break;
      seen.add(curId);
      
      const item = store.state.workItems?.find(w => w.id === curId);
      if (item) {
        chain.push({ id: curId, label: item.title || item.kind || curId });
      }
      
      const pid = item?.parent_id;
      if (!pid) break;
      curId = pid;
    }
    
    chain.reverse();
    return chain;
  }

  function renderBadges(wi) {
    return `
      <span class="kind-badge k-${wi.kind || 'task'}">${escapeHtml(wi.kind || 'task')}</span>
      <span class="badge s-${wi.status || 'draft'}">
        <span class="badge-dot"></span>
        ${getStatusLabel(wi.status)}
      </span>
      <span class="mono-id" title="${wi.id}">${wi.id?.slice(0, 8)}...</span>
    `;
  }

  function renderNextAction(wi) {
    const nextAction = getNextActionText(wi.status);
    return `
      <span title="${nextAction.title}">${nextAction.text}</span>
    `;
  }

  function renderActionButtons(wi) {
    const status = wi.status?.toLowerCase();
    const isAtom = wi.kind?.toLowerCase() === 'atom';
    
    let buttons = [];
    
    if (status === 'ready_for_work' && isAtom) {
      buttons.push(`
        <button type="button" class="btn primary" onclick="window.runWorkItemFromDetail('${wi.id}')" title="POST /api/work-items/${wi.id}/run">
          ▶ Запустить Forge
        </button>
      `);
    }
    
    if (['draft', 'planned', 'ready_for_judge', 'judge_rejected'].includes(status)) {
      buttons.push(`
        <button type="button" class="btn dp-btn-edit" onclick="window.startDetailTitleEdit()">✏️ Редактировать</button>
        <button type="button" class="btn dp-btn-danger" onclick="window.workItemCancelFromDetail('${wi.id}')">❌ Отменить</button>
        <button type="button" class="btn dp-btn-danger" onclick="window.workItemDeleteFromDetail('${wi.id}')">🗑 Удалить</button>
      `);
    } else if (status === 'in_progress' || status === 'in_review') {
      buttons.push(`
        <button type="button" class="btn dp-btn-danger" onclick="window.workItemCancelFromDetail('${wi.id}')">❌ Отменить</button>
      `);
    } else if (status === 'done') {
      buttons.push(`
        <button type="button" class="btn dp-btn-archive" onclick="window.workItemArchiveFromDetail('${wi.id}')">📦 Архивировать</button>
      `);
    } else if (status === 'cancelled') {
      buttons.push(`
        <button type="button" class="btn dp-btn-danger" onclick="window.workItemDeleteFromDetail('${wi.id}')">🗑 Удалить</button>
      `);
    }
    
    return buttons.join('');
  }

  function renderFirstScreen(wi, children, runs) {
    const childrenSummary = getChildrenStatusSummary(children);
    const runsCount = runs.length;
    
    return `
      <div class="dp-firstscreen">
        <div class="dp-cards3">
          <div class="dp-mini-card">
            <div class="dp-mini-title">Родитель</div>
            ${wi.parent_id ? `
              <div class="dp-kv2">
                <span class="k">ID</span>
                <span class="v mono-id">${wi.parent_id.slice(0, 8)}...</span>
                <span class="k">Название</span>
                <span class="v">${getParentTitle(wi.parent_id)}</span>
              </div>
            ` : '<span class="dp-muted">Корневая задача</span>'}
          </div>
          
          <div class="dp-mini-card">
            <div class="dp-mini-title">Дочерние</div>
            <div class="dp-kv2">
              <span class="k">Всего</span>
              <span class="v mono-id">${childrenSummary.total}</span>
              <span class="k">Готово</span>
              <span class="v mono-id">${childrenSummary.done} (${childrenSummary.pct}%)</span>
            </div>
          </div>
          
          <div class="dp-mini-card">
            <div class="dp-mini-title">Прогоны</div>
            <div class="dp-kv2">
              <span class="k">Всего</span>
              <span class="v mono-id">${runsCount}</span>
              <span class="k">Последний</span>
              <span class="v mono-id">${getLastRunStatus(runs)}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderAccordions(wi, children, runs, events, comments) {
    const accordions = [];
    
    // Children accordion
    if (children.length > 0) {
      accordions.push(renderChildrenAccordion(children));
    }
    
    // Runs accordion
    if (runs.length > 0) {
      accordions.push(renderRunsAccordion(runs));
    }
    
    // Files accordion (из work item)
    if (wi.files && wi.files.length > 0) {
      accordions.push(renderFilesAccordion(wi.files));
    }
    
    // Comments accordion
    if (comments.length > 0) {
      accordions.push(renderCommentsAccordion(comments));
    }
    
    // Events/Timeline accordion
    if (events.length > 0) {
      accordions.push(renderEventsAccordion(events));
    }
    
    return accordions.join('');
  }

  function renderChildrenAccordion(children) {
    return `
      <div class="dp-acc" id="dp-acc-children">
        <button type="button" class="dp-acc-h" onclick="window.toggleDpAccordion('dp-acc-children')">
          <span class="t">📦 Дочерние задачи</span>
          <span class="meta">${children.length} шт.</span>
        </button>
        <div class="dp-acc-b">
          <div class="dp-acc-inner">
            ${children.map(child => `
              <div class="comment-item" style="cursor:pointer" onclick="window.selectWorkItem('${child.id}')">
                <div class="comment-meta">
                  <span class="kind-badge k-${child.kind}">${child.kind}</span>
                  <span class="badge s-${child.status}">${getStatusLabel(child.status)}</span>
                  <span class="mono-id" style="margin-left:auto">${child.id.slice(0, 8)}...</span>
                </div>
                <div class="comment-body">${escapeHtml(child.title || 'Без названия')}</div>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function renderRunsAccordion(runs) {
    return `
      <div class="dp-acc" id="dp-acc-runs">
        <button type="button" class="dp-acc-h" onclick="window.toggleDpAccordion('dp-acc-runs')">
          <span class="t">⚡ Прогоны (Forge/Review)</span>
          <span class="meta">${runs.length} шт.</span>
        </button>
        <div class="dp-acc-b">
          <div class="dp-acc-inner">
            ${runs.map(run => `
              <div class="comment-item">
                <div class="comment-meta">
                  <span class="mono-id">${run.id?.slice(0, 8)}...</span>
                  <span class="badge s-${run.status === 'completed' ? 'done' : run.status === 'running' ? 'in_progress' : 'failed'}">
                    ${run.status}
                  </span>
                  <span class="mono-id" style="margin-left:auto">${formatTime(run.started_at)}</span>
                </div>
                <div class="comment-body">
                  <span class="role-badge r-${run.role || 'forge'}">${run.role}</span>
                  ${run.finished_at ? `· ${formatDuration(run.started_at, run.finished_at)}` : ''}
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function renderFilesAccordion(files) {
    return `
      <div class="dp-acc" id="dp-acc-files">
        <button type="button" class="dp-acc-h" onclick="window.toggleDpAccordion('dp-acc-files')">
          <span class="t">📄 Файлы</span>
          <span class="meta">${files.length} шт.</span>
        </button>
        <div class="dp-acc-b">
          <div class="dp-acc-inner">
            ${files.map((file, idx) => `
              <div class="dp-file-row">
                <span class="dp-file-path">${escapeHtml(file.path)}</span>
                <span class="dp-file-intent" style="color:var(--${getFileIntentColor(file.intent || 'modify')})">
                  ${file.intent || 'modify'}
                </span>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function renderCommentsAccordion(comments) {
    return `
      <div class="dp-acc" id="dp-acc-comments">
        <button type="button" class="dp-acc-h" onclick="window.toggleDpAccordion('dp-acc-comments')">
          <span class="t">💬 Комментарии</span>
          <span class="meta">${comments.length} шт.</span>
        </button>
        <div class="dp-acc-b">
          <div class="dp-acc-inner">
            ${comments.map(comment => `
              <div class="comment-item">
                <div class="comment-meta">
                  <span class="role-badge r-${comment.author_role || 'system'}">${comment.author_role || 'system'}</span>
                  <span class="comment-time">${formatTime(comment.created_at)}</span>
                </div>
                <div class="comment-body">${escapeHtml(comment.text || comment.body || '')}</div>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function renderEventsAccordion(events) {
    return `
      <div class="dp-acc" id="dp-acc-events">
        <button type="button" class="dp-acc-h" onclick="window.toggleDpAccordion('dp-acc-events')">
          <span class="t">📜 События (Timeline)</span>
          <span class="meta">${events.length} шт.</span>
        </button>
        <div class="dp-acc-b">
          <div class="dp-acc-inner">
            <div class="timeline">
              ${events.map(event => `
                <div class="timeline-item">
                  <div class="timeline-dot ${getEventDotClass(event.severity || 'info')}"></div>
                  <div class="timeline-content">
                    <div class="timeline-title">${escapeHtml(event.message || event.event_type || '')}</div>
                    <div class="timeline-meta">
                      ${formatTime(event.event_time)} · 
                      <span class="role-badge r-${event.actor_role || 'system'}">${event.actor_role || 'system'}</span>
                    </div>
                  </div>
                </div>
              `).join('')}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ═══════════════════════════════════════════════════════
  // EVENT LISTENERS
  // ═══════════════════════════════════════════════════════

  function attachEventListeners() {
    // Close button
    const closeBtn = container.querySelector('.detail-panel-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        store.selectWorkItem(null);
      });
    }
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

function getNextActionText(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'ready_for_work' || s === 'ready') return { text: '⚡ Готов к Forge', title: 'Готов к запуску Forge' };
  if (s === 'in_progress' || s === 'running') return { text: '🔄 В работе', title: 'В работе' };
  if (s === 'done' || s === 'completed') return { text: '✅ Завершён', title: 'Завершён' };
  if (s.includes('blocked') || s.includes('rejected') || s.includes('failed')) return { text: '⚠ Требует внимания', title: 'Требует внимания' };
  return { text: '📝 Ожидает следующего шага', title: 'Ожидает следующего шага' };
}

function getChildrenStatusSummary(children) {
  const out = { total: 0, done: 0, in_progress: 0, planned: 0, blocked: 0, cancelled: 0, archived: 0 };
  const ch = Array.isArray(children) ? children : [];
  out.total = ch.length;
  ch.forEach(c => {
    const s = String(c.status || '').toLowerCase();
    if (s in out) out[s] += 1;
  });
  const completed = out.done + out.cancelled + out.archived;
  out.pct = out.total ? Math.round((completed / out.total) * 100) : 0;
  return out;
}

function getParentTitle(parentId) {
  const parent = store.state.workItems?.find(w => w.id === parentId);
  return parent ? escapeHtml(parent.title || parent.kind || parentId) : 'Не найдено';
}

function getLastRunStatus(runs) {
  if (!runs || runs.length === 0) return '—';
  const lastRun = runs[runs.length - 1];
  return lastRun.status || 'unknown';
}

function getFileIntentColor(intent) {
  const colors = {
    modify: 'orange',
    create: 'success',
    delete: 'error'
  };
  return colors[intent] || 'orange';
}

function getEventDotClass(severity) {
  const s = String(severity || '').toLowerCase();
  if (s === 'warn' || s === 'warning') return 'warn';
  if (s === 'error') return 'error';
  return 'done';
}

// Helpers импортируются из utils/helpers.js

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS (для onclick из HTML)
// ═══════════════════════════════════════════════════════

window.toggleDpAccordion = (id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('open');
};

window.startDetailTitleEdit = () => {
  isEditingTitle = true;
  // Триггерим ре-рендер через store update
  store.update({ _forceRender: true });
};

window.cancelDetailTitleEdit = () => {
  isEditingTitle = false;
  store.update({ _forceRender: true });
};

window.saveDetailTitleEdit = async () => {
  const input = document.getElementById('dp-title-input');
  if (!input || !currentWorkItem) return;
  
  const newTitle = input.value.trim();
  if (!newTitle) return;
  
  try {
    await api.patchWorkItem(currentWorkItem.id, { title: newTitle });
    // Обновляем в store
    const idx = store.state.workItems?.findIndex(w => w.id === currentWorkItem.id);
    if (idx !== -1) {
      store.state.workItems[idx].title = newTitle;
    }
    currentWorkItem.title = newTitle;
    isEditingTitle = false;
    store.update({ _forceRender: true });
    showFactoryToast('Сохранено', 'ok');
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.navigateDetailTo = (workItemId) => {
  store.selectWorkItem(workItemId);
};

window.closeDetail = () => {
  store.selectWorkItem(null);
};

window.selectWorkItem = (id) => {
  store.selectWorkItem(id);
};

window.runWorkItemFromDetail = async (wiId) => {
  try {
    await api.runWorkItem(wiId);
    showFactoryToast('Forge запущен', 'ok');
    // Обновляем данные
    await loadDetailData(wiId);
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.workItemCancelFromDetail = async (wiId) => {
  if (!confirm('Отменить задачу и все дочерние?')) return;
  try {
    await api.cancelWorkItem(wiId);
    showFactoryToast('Отменено', 'ok');
    store.selectWorkItem(null);
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.workItemArchiveFromDetail = async (wiId) => {
  try {
    await api.archiveWorkItem(wiId);
    showFactoryToast('Архивировано', 'ok');
    store.selectWorkItem(null);
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.workItemDeleteFromDetail = async (wiId) => {
  if (!confirm('Удалить безвозвратно?')) return;
  try {
    await api.deleteWorkItem(wiId);
    showFactoryToast('Удалено', 'ok');
    store.selectWorkItem(null);
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
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
