/**
 * Factory OS — Journal Component
 * Рендеринг операционного журнала (GET /api/journal)
 */

import { store, subscribe } from '../state/store.js';
import { debounce } from '../utils/debounce.js';

const PAGE_SIZE = 100;
let currentPage = 0;
let filters = {
  root_id: '',
  work_item_id: '',
  run_id: '',
  kind: '',
  role: '',
  severity: 'all'
};

export function JournalComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'log' || state.activePage === 'dashboard') {
        render(state.journal);
      }
    });
  }
  
  function render(journal) {
    if (!container) return;
    
    if (!journal || !journal.items) {
      container.innerHTML = '<div style="color:var(--text-muted);padding:20px">Загрузка журнала...</div>';
      return;
    }
    
    const items = applyFilters(journal.items);
    const totalPages = Math.ceil(items.length / PAGE_SIZE);
    const pageItems = items.slice(
      currentPage * PAGE_SIZE,
      (currentPage + 1) * PAGE_SIZE
    );
    
    container.innerHTML = `
      <div class="log-filters" style="margin-bottom:var(--space-4);flex-wrap:wrap">
        <label style="font-size:10px;color:var(--text-faint);display:flex;flex-direction:column;gap:2px">
          Vision / ветка
          <select id="journal-filter-root" class="log-search" onchange="window.syncJournalRootFilter?.()">
            <option value="">— вся фабрика —</option>
          </select>
        </label>
        <input class="log-search" type="text" placeholder="work_item_id" 
               value="${filters.work_item_id}" 
               onchange="window.setJournalFilter('work_item_id', this.value)">
        <input class="log-search" type="text" placeholder="run_id" 
               value="${filters.run_id}"
               onchange="window.setJournalFilter('run_id', this.value)">
        <input class="log-search" type="text" placeholder="kind" 
               value="${filters.kind}"
               onchange="window.setJournalFilter('kind', this.value)">
        <input class="log-search" type="text" placeholder="role" 
               value="${filters.role}"
               onchange="window.setJournalFilter('role', this.value)">
        <input class="log-search" type="text" placeholder="Поиск в ленте…" 
               oninput="window.filterLog?.()">
        <button class="log-filter-btn log-sev-btn ${filters.severity === 'all' ? 'active' : ''}" 
                onclick="window.setSevFilter(this, 'all')">Все</button>
        <button class="log-filter-btn log-sev-btn ${filters.severity === 'info' ? 'active' : ''}" 
                onclick="window.setSevFilter(this, 'info')">INFO</button>
        <button class="log-filter-btn log-sev-btn ${filters.severity === 'warn' ? 'active' : ''}" 
                onclick="window.setSevFilter(this, 'warn')">WARN</button>
        <button class="log-filter-btn log-sev-btn ${filters.severity === 'error' ? 'active' : ''}" 
                onclick="window.setSevFilter(this, 'error')">ERROR</button>
        <div style="display:flex;gap:var(--space-2);align-items:center;margin-left:auto">
          <button class="log-filter-btn" onclick="window.logPage?.(-1)" ${currentPage === 0 ? 'disabled' : ''}>←</button>
          <span style="font-size:var(--text-xs);color:var(--text-muted)">
            Стр. ${currentPage + 1} из ${totalPages || 1}
          </span>
          <button class="log-filter-btn" onclick="window.logPage?.(1)" ${currentPage >= totalPages - 1 ? 'disabled' : ''}>→</button>
        </div>
      </div>
      
      <div style="border-top:1px solid var(--border)">
        <div class="log-entry" style="background:var(--surface-2)">
          <div class="log-entry-main journal-grid-head">
            <span class="jh">Время</span>
            <span class="jh">Источник</span>
            <span class="jh">Роль</span>
            <span class="jh">kind</span>
            <span class="jh">wi / run</span>
          </div>
        </div>
        ${renderLogEntries(pageItems)}
      </div>
    `;
  }
  
  function renderLogEntries(items) {
    if (!items || items.length === 0) {
      return '<div style="padding:20px;color:var(--text-muted)">Нет записей</div>';
    }
    
    return items.map(item => `
      <div class="log-entry ${item.severity === 'error' ? 'sev-error' : item.severity === 'warn' ? 'sev-warn' : 'sev-info'}"
           onclick="window.showJournalDetail?.(${JSON.stringify(item).replace(/"/g, '&quot;')})">
        <div class="log-entry-main journal-row">
          <span class="log-time">${formatTime(item.event_time)}</span>
          <span class="log-src">${escapeHtml(item.source_type || 'event')}</span>
          <span class="role-badge r-${item.actor_role || 'system'}">${escapeHtml(item.actor_role || 'system')}</span>
          <span class="kind-badge k-${item.kind || 'event'}">${escapeHtml(item.kind || 'event')}</span>
          <span class="td-mono" style="display:flex;align-items:center;gap:4px">
            ${item.work_item_id ? `
              <span>${monoId(item.work_item_id)}</span>
              <button type="button" 
                      onclick="event.stopPropagation();window.askQwenAboutEntity('work_item', '${item.work_item_id}')"
                      title="Спросить Qwen про задачу"
                      style="background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:var(--radius-sm);padding:1px 4px;font-size:8px;cursor:pointer">💬</button>
            ` : '—'}
            /
            ${item.run_id ? `
              <span>${monoId(item.run_id)}</span>
              <button type="button" 
                      onclick="event.stopPropagation();window.askQwenAboutEntity('run', '${item.run_id}')"
                      title="Спросить Qwen про прогон"
                      style="background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:var(--radius-sm);padding:1px 4px;font-size:8px;cursor:pointer">💬</button>
            ` : '—'}
          </span>
        </div>
        <div class="log-row1">
          <span class="title">${escapeHtml(item.message || item.summary || '')}</span>
        </div>
        ${item.payload ? `
          <details class="log-payload">
            <summary>Payload</summary>
            <pre>${escapeHtml(JSON.stringify(item.payload, null, 2))}</pre>
          </details>
        ` : ''}
      </div>
    `).join('');
  }
  
  function applyFilters(items) {
    return items.filter(item => {
      if (filters.work_item_id && item.work_item_id !== filters.work_item_id) return false;
      if (filters.run_id && item.run_id !== filters.run_id) return false;
      if (filters.kind && item.kind !== filters.kind) return false;
      if (filters.role && item.actor_role !== filters.role) return false;
      if (filters.severity !== 'all' && item.severity !== filters.severity) return false;
      return true;
    });
  }
  
  // Глобальные функции
  window.setJournalFilter = (key, value) => {
    filters[key] = value;
    currentPage = 0;
    const { journal } = store.state;
    render(journal);
  };
  
  window.setSevFilter = (btn, severity) => {
    filters.severity = severity;
    document.querySelectorAll('.log-sev-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentPage = 0;
    const { journal } = store.state;
    render(journal);
  };
  
  window.logPage = (dir) => {
    currentPage += dir;
    const { journal } = store.state;
    render(journal);
  };
  
  window.filterLog = debounce(() => {
    // Поиск по тексту
    const { journal } = store.state;
    render(journal);
  }, 300);
  
  window.showJournalDetail = (entry) => {
    const detailPane = document.getElementById('journal-detail-pane');
    const detailBody = document.getElementById('journal-detail-body');
    if (!detailPane || !detailBody) return;
    
    // Показываем панель
    detailPane.style.display = 'block';
    
    // Формируем детальную информацию
    const detail = {
      id: entry.id,
      event_time: entry.event_time,
      event_type: entry.event_type,
      source_type: entry.journal_source_type || entry.source_type,
      actor_role: entry.actor_role,
      kind: entry.kind,
      work_item_id: entry.work_item_id,
      run_id: entry.run_id,
      entity_id: entry.entity_id,
      severity: entry.severity,
      message: entry.message || entry.summary,
      payload: entry.payload
    };
    
    detailBody.innerHTML = `
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">ID:</strong> <span class="mono-id">${entry.id}</span> <button type="button" onclick="navigator.clipboard.writeText('${entry.id}');window.showFactoryToast('ID скопирован','ok')" style="margin-left:6px;padding:2px 6px;font-size:9px;background:var(--surface-3);border:1px solid var(--border);color:var(--text-muted);cursor:pointer;border-radius:3px" title="Копировать ID">📋</button></div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Время:</strong> ${entry.event_time || '—'}</div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Тип:</strong> ${entry.event_type || '—'}</div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Источник:</strong> ${entry.journal_source_type || entry.source_type || '—'}</div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Роль:</strong> ${entry.actor_role || '—'}</div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Kind:</strong> ${entry.kind || '—'}</div>
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Severity:</strong> ${entry.severity || 'info'}</div>
${entry.work_item_id ? `<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Work Item:</strong> <span class="mono-id">${entry.work_item_id}</span> <button type="button" onclick="window.navigateToWorkItem('${entry.work_item_id}')" style="margin-left:6px;padding:2px 6px;font-size:9px;background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);cursor:pointer;border-radius:3px" title="Перейти к задаче">→</button></div>` : ''}
${entry.run_id ? `<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Run:</strong> <span class="mono-id">${entry.run_id}</span></div>` : ''}
${entry.entity_id ? `<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Entity:</strong> <span class="mono-id">${entry.entity_id}</span></div>` : ''}
<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Сообщение:</strong><div style="margin-top:4px;color:var(--text-muted);line-height:1.5">${entry.message || entry.summary || '—'}</div></div>
${entry.payload && Object.keys(entry.payload).length > 0 ? `<div style="margin-bottom:var(--space-2)"><strong style="color:var(--text)">Payload:</strong><details style="margin-top:4px"><summary style="cursor:pointer;color:var(--text-faint)">Показать</summary><pre style="margin-top:6px;padding:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);max-height:400px;overflow:auto;font-size:10px;color:var(--text-muted)">${escapeHtml(JSON.stringify(entry.payload, null, 2))}</pre></details></div>` : ''}
    `;
  };
  
  window.closeJournalDetail = () => {
    const detailPane = document.getElementById('journal-detail-pane');
    if (detailPane) detailPane.style.display = 'none';
  };
  
  window.navigateToWorkItem = (id) => {
    store.selectWorkItem(id);
    window.goPage('tree');
    showFactoryToast(`Переход к ${id.slice(0, 8)}...`, 'ok');
  };
  
  window.showFactoryToast = (message, kind = 'ok') => {
    const el = document.getElementById('factory-toast');
    if (!el) return;
    el.textContent = message;
    el.className = 'factory-toast visible ' + (kind === 'err' ? 'err' : 'ok');
    clearTimeout(el._hideT);
    el._hideT = setTimeout(() => { el.classList.remove('visible'); }, 3000);
  };

  subscribeToStore();

  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers
function formatTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function monoId(raw) {
  if (!raw) return '—';
  const s = String(raw);
  const short = s.length > 8 ? s.slice(0, 8) + '…' : s;
  return `<span class="mono-id" title="${escapeHtml(s)}">${escapeHtml(short)}</span>`;
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
