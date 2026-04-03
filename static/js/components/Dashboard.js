/**
 * Factory OS — Dashboard Component
 * Главный экран: KPI, Visions, превью журнала
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';
import { escapeHtml, formatTime, formatDuration, getStatusLabel } from '../utils/helpers.js';

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

    const { analytics, visions, journal, orchestrator, workersStatus, workItems } = state;

    // KPI Grid
    const kpiContainer = document.getElementById('kpi-grid');
    if (kpiContainer) {
      kpiContainer.innerHTML = renderKPIs(analytics, orchestrator, workItems);
    }

    // Visions - нормализуем массив
    const visionsArray = Array.isArray(visions)
      ? visions
      : Array.isArray(visions?.visions)
        ? visions.visions
        : [];
    const visionsContainer = document.getElementById('dashboard-visions');
    if (visionsContainer) {
      visionsContainer.innerHTML = renderVisions(visionsArray);
    }

    // Journal preview - нормализуем массив
    let journalItems = [];
    if (journal?.items && Array.isArray(journal.items)) {
      journalItems = journal.items;
    } else if (Array.isArray(journal)) {
      journalItems = journal;
    }
    const journalContainer = document.getElementById('dashboard-log-feed');
    if (journalContainer) {
      journalContainer.innerHTML = renderJournalPreview({ items: journalItems });
    }

    // Charts
    renderStatusChart(workItems);
    renderActivityChart(journalItems);
    
    // Runs table
    renderRunsTable();
  }
  
  function renderKPIs(analytics, orchestrator, workItems) {
    if (!analytics) {
      // Fallback если analytics ещё не загружен
      const atoms = workItems?.filter(w => w.kind === 'atom') || [];
      const inProgress = workItems?.filter(w => w.status === 'in_progress') || [];
      return `
        <div class="kpi-card">
          <div class="kpi-label">Work Items</div>
          <div class="kpi-value">${workItems?.length || 0}</div>
          <div class="kpi-delta neutral">Загрузка...</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Атомы</div>
          <div class="kpi-value">${atoms.length}</div>
          <div class="kpi-delta neutral">Всего</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">В работе</div>
          <div class="kpi-value">${inProgress.length}</div>
          <div class="kpi-delta neutral">in_progress</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Orchestrator</div>
          <div class="kpi-value">${orchestrator?.running ? '✓' : '—'}</div>
          <div class="kpi-delta neutral">${orchestrator?.running ? 'Running' : 'Stopped'}</div>
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
  
  subscribeToStore();
  
  return () => { 
    if (unsubscribe) unsubscribe();
    // Cleanup charts
    Object.values(charts).forEach(chart => chart?.destroy?.());
  };
}

// ═══════════════════════════════════════════════════════
// CHARTS
// ═══════════════════════════════════════════════════════

let statusChart = null;
let activityChart = null;

function renderStatusChart(workItems) {
  const canvas = document.getElementById('chart-status');
  if (!canvas || typeof Chart === 'undefined') return;
  
  // Считаем статусы
  const counts = {};
  (workItems || []).forEach(wi => {
    const status = wi.status || 'unknown';
    counts[status] = (counts[status] || 0) + 1;
  });
  
  const entries = Object.entries(counts).filter(([,v]) => v > 0);
  if (entries.length === 0) return;
  
  const statusColors = {
    draft: '#3d3d42',
    planned: '#5591c7',
    ready_for_judge: '#d4a040',
    judge_rejected: '#d163a7',
    ready_for_work: '#4f98a3',
    in_progress: '#e09248',
    in_review: '#a870df',
    review_rejected: '#dd6974',
    blocked: '#c8763a',
    done: '#5aaa38',
    cancelled: '#2a2a2e',
    archived: '#1a1a1e'
  };
  
  if (statusChart) {
    statusChart.destroy();
  }
  
  statusChart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: entries.map(([k]) => getStatusLabel(k)),
      datasets: [{
        data: entries.map(([,v]) => v),
        backgroundColor: entries.map(([k]) => statusColors[k] || '#666'),
        borderWidth: 0,
        hoverOffset: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: {
          position: 'right',
          labels: {
            color: '#72727a',
            font: { size: 10, family: 'JetBrains Mono' },
            boxWidth: 10,
            padding: 8
          }
        }
      }
    }
  });
}

function renderActivityChart(journalItems) {
  const canvas = document.getElementById('chart-activity');
  if (!canvas || typeof Chart === 'undefined') return;
  
  // Группируем события по часам (последние 12 часов)
  const now = new Date();
  const hours = [];
  const hourData = [];
  
  for (let i = 11; i >= 0; i--) {
    const h = new Date(now.getTime() - i * 3600000);
    const hourLabel = h.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    hours.push(hourLabel);
    
    // Считаем события за этот час
    const hourStart = new Date(h);
    hourStart.setMinutes(0, 0, 0);
    const hourEnd = new Date(h);
    hourEnd.setMinutes(59, 59, 999);
    
    const count = (journalItems || []).filter(item => {
      const itemTime = new Date(item.event_time);
      return itemTime >= hourStart && itemTime <= hourEnd;
    }).length;
    
    hourData.push(count);
  }
  
  if (activityChart) {
    activityChart.destroy();
  }
  
  activityChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: hours,
      datasets: [{
        label: 'События',
        data: hourData,
        backgroundColor: 'rgba(79,152,163,0.3)',
        borderColor: '#4f98a3',
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: {
          ticks: {
            color: '#3d3d42',
            font: { size: 9 },
            maxRotation: 45,
            minRotation: 45
          },
          grid: { color: '#1a1a1c' }
        },
        y: {
          ticks: {
            color: '#3d3d42',
            font: { size: 9 },
            stepSize: 1
          },
          grid: { color: '#1a1a1c' },
          beginAtZero: true
        }
      }
    }
  });
}

function renderRunsTable() {
  const container = document.getElementById('dashboard-runs');
  if (!container) return;
  
  const runs = store.state.runs || [];
  const workItems = store.state.workItems || [];
  
  if (runs.length === 0) {
    container.innerHTML = `
      <tbody>
        <tr><td colspan="6" style="padding:18px;color:var(--text-muted);text-align:center">Нет данных</td></tr>
      </tbody>
    `;
    return;
  }
  
  const tbody = container.querySelector('tbody');
  if (!tbody) return;
  
  tbody.innerHTML = runs.slice(0, 10).map(run => {
    const wi = workItems.find(w => w.id === run.work_item_id);
    return `
      <tr style="cursor:pointer" onclick="window.viewRunDetail('${run.id}')" title="Клик: детали прогона">
        <td class="td-mono">${run.id?.slice(0, 8) || '—'}...</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(wi?.title || '—')}</td>
        <td><span class="role-badge r-${run.role || 'forge'}">${run.role || 'forge'}</span></td>
        <td><span class="badge s-${run.status === 'completed' ? 'done' : run.status === 'running' ? 'in_progress' : 'failed'}">${run.status || 'unknown'}</span></td>
        <td class="td-mono">${formatTime(run.started_at)}</td>
        <td class="td-mono">${formatDuration(run.started_at, run.finished_at)}</td>
      </tr>
    `;
  }).join('');
}

// Helpers импортируются из utils/helpers.js
