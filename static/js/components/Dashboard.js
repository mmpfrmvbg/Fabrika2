/**
 * Factory OS — Dashboard Component
 * Главный экран: KPI, Visions, превью журнала
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';

export function DashboardComponent(container) {
  let unsubscribe = null;
  let charts = {};
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'dashboard') {
        render(state);
      }
    });
  }
  
  function render(state) {
    if (!container) return;
    
    const { analytics, visions, journal, orchestrator, workersStatus } = state;
    
    // KPI Grid
    const kpiContainer = document.getElementById('kpi-grid');
    if (kpiContainer) {
      kpiContainer.innerHTML = renderKPIs(analytics, orchestrator);
    }
    
    // Visions - нормализуем массив
    const visionsArray = visions?.visions || Array.isArray(visions) ? visions : [];
    const visionsContainer = document.getElementById('dashboard-visions');
    if (visionsContainer) {
      visionsContainer.innerHTML = renderVisions(visionsArray);
    }
    
    // Journal preview - нормализуем массив
    const journalItems = journal?.items || Array.isArray(journal) ? journal : [];
    const journalContainer = document.getElementById('dashboard-log-feed');
    if (journalContainer) {
      journalContainer.innerHTML = renderJournalPreview({ items: journalItems });
    }
    
    // Charts (если есть аналитика)
    if (analytics) {
      renderCharts(analytics);
    }
  }
  
  function renderKPIs(analytics, orchestrator) {
    if (!analytics) {
      return `
        <div class="kpi-card">
          <div class="kpi-label">Work Items</div>
          <div class="kpi-value">—</div>
          <div class="kpi-delta neutral">Загрузка...</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Active Runs</div>
          <div class="kpi-value">${orchestrator?.running ? '✓' : '—'}</div>
          <div class="kpi-delta neutral">Orchestrator ${orchestrator?.running ? 'ON' : 'OFF'}</div>
        </div>
      `;
    }
    
    return `
      <div class="kpi-card">
        <div class="kpi-label">Total Work Items</div>
        <div class="kpi-value">${analytics.work_items?.total || 0}</div>
        <div class="kpi-delta neutral">Все задачи</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Atoms</div>
        <div class="kpi-value">${analytics.atoms?.total || 0}</div>
        <div class="kpi-delta neutral">
          Done: ${analytics.atoms?.completed || 0}
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Visions</div>
        <div class="kpi-value">${analytics.visions?.total || 0}</div>
        <div class="kpi-delta neutral">
          Completed: ${analytics.visions?.completed || 0}
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Active Runs</div>
        <div class="kpi-value">${analytics.runs?.active || 0}</div>
        <div class="kpi-delta ${orchestrator?.running ? 'up' : 'neutral'}">
          ${orchestrator?.running ? '↑ Running' : '— Stopped'}
        </div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">First Pass Rate</div>
        <div class="kpi-value">${((analytics.atoms?.first_pass_rate || 0) * 100).toFixed(0)}%</div>
        <div class="kpi-delta neutral">Без повторных попыток</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Avg Cycle Time</div>
        <div class="kpi-value">${formatDuration(analytics.atoms?.avg_cycle_time_sec)}</div>
        <div class="kpi-delta neutral">Атомы</div>
      </div>
    `;
  }
  
  function renderVisions(visions) {
    if (!visions || visions.length === 0) {
      return '<div style="color:var(--text-muted);padding:var(--space-3)">Загрузка Visions...</div>';
    }
    
    return visions.slice(0, 10).map(v => `
      <div class="vision-modal-card card" style="padding:var(--space-3);margin-bottom:var(--space-2)">
        <div style="display:flex;align-items:center;gap:var(--space-2)">
          <span class="kind-badge k-vision">vision</span>
          <span class="badge s-${v.status}">${getStatusLabel(v.status)}</span>
          <span class="tree-title" style="flex:1">${escapeHtml(v.title)}</span>
          <span class="mono-id" title="${v.id}">${v.id?.slice(0, 8)}...</span>
        </div>
        ${v.description ? `
          <div style="margin-top:6px;font-size:var(--text-sm);color:var(--text-muted)">
            ${escapeHtml(v.description)}
          </div>
        ` : ''}
      </div>
    `).join('');
  }
  
  function renderJournalPreview(journal) {
    // Нормализуем journal - может быть объектом { items: [...] } или массивом
    let itemsArray = [];
    if (Array.isArray(journal)) {
      itemsArray = journal;
    } else if (journal?.items && Array.isArray(journal.items)) {
      itemsArray = journal.items;
    } else if (journal?.data && Array.isArray(journal.data)) {
      itemsArray = journal.data;
    }

    if (!itemsArray || itemsArray.length === 0) {
      return '<div style="color:var(--text-muted);padding:var(--space-3)">Загрузка журнала...</div>';
    }

    const items = itemsArray.slice(0, 5);
    return items.map(item => `
      <div class="dashboard-log-row">
        <span class="log-time">${formatTime(item.event_time)}</span>
        <span class="log-msg">${escapeHtml(item.message || item.summary || '')}</span>
        <span class="badge s-${item.status || 'info'}">${item.event_type || ''}</span>
      </div>
    `).join('');
  }
  
  function renderCharts(analytics) {
    // TODO: Chart.js интеграция
    // Для простоты пока заглушка
  }
  
  subscribeToStore();
  
  return () => { 
    if (unsubscribe) unsubscribe();
    // Cleanup charts
    Object.values(charts).forEach(chart => chart?.destroy?.());
  };
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

function formatDuration(seconds) {
  if (!seconds) return '—';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
