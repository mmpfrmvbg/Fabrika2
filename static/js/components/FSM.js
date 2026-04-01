/**
 * Factory OS — FSM Component
 * Визуализация State Machine и таблицы переходов
 */

import { store, subscribe } from '../state/store.js';

// Конфигурация узлов FSM
const STATUS_CONFIG = {
  draft: { color: '#3d5a7a', label: 'Draft' },
  planned: { color: '#3d5a7a', label: 'Planned' },
  ready_for_judge: { color: '#4a3010', label: '→ Judge' },
  judge_rejected: { color: '#4a1020', label: 'Judge ✗' },
  ready_for_work: { color: '#2a3d20', label: 'Ready' },
  in_progress: { color: '#2a3d20', label: 'Running' },
  in_review: { color: '#4a3010', label: 'In Review' },
  review_rejected: { color: '#4a1020', label: 'Review ✗' },
  blocked: { color: '#4a3010', label: 'Blocked' },
  done: { color: '#204a20', label: 'Done' },
  cancelled: { color: '#4a1020', label: 'Cancelled' },
  archived: { color: '#204a20', label: 'Archived' },
};

export function FSMComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'fsm') {
        render(state.fsm, state.workItems);
      }
    });
  }
  
  function render(fsm, workItems) {
    if (!container) return;
    
    // Таблица переходов
    const tableContainer = document.getElementById('tbl-transitions');
    if (tableContainer) {
      tableContainer.innerHTML = renderTransitionsTable(fsm);
    }
    
    // SVG диаграмма (упрощённая)
    const svgContainer = document.getElementById('fsm-svg');
    if (svgContainer) {
      svgContainer.innerHTML = renderFSMSvg(fsm);
    }
  }
  
  function renderTransitionsTable(fsm) {
    if (!fsm || !fsm.transitions) {
      return '<tbody><tr><td colspan="6" style="padding:18px;color:var(--text-muted)">Загрузка...</td></tr></tbody>';
    }
    
    return `
      <thead>
        <tr>
          <th>From</th>
          <th>Event</th>
          <th>To</th>
          <th>Guard</th>
          <th>Action</th>
          <th>Kinds</th>
        </tr>
      </thead>
      <tbody>
        ${fsm.transitions.map(t => `
          <tr>
            <td><span class="badge s-${t.from_state}">${getStatusLabel(t.from_state)}</span></td>
            <td class="mono-id">${escapeHtml(t.event_name)}</td>
            <td><span class="badge s-${t.to_state}">${getStatusLabel(t.to_state)}</span></td>
            <td class="mono-id" style="font-size:10px">${escapeHtml(t.guard_name || '—')}</td>
            <td class="mono-id" style="font-size:10px">${escapeHtml(t.action_name || '—')}</td>
            <td class="mono-id" style="font-size:9px;color:var(--text-faint)">
              ${t.applicable_kinds ? escapeHtml(t.applicable_kinds) : '*'}
            </td>
          </tr>
        `).join('')}
      </tbody>
    `;
  }
  
  function renderFSMSvg(fsm) {
    // Упрощённая SVG визуализация
    // В полной версии здесь была бы сложная диаграмма
    return `
      <g>
        <text x="450" y="30" text-anchor="middle" fill="var(--text)" font-size="14" font-weight="600">
          Factory FSM — State Transitions
        </text>
        <text x="450" y="50" text-anchor="middle" fill="var(--text-muted)" font-size="11">
          ${fsm?.transitions?.length || 0} transitions loaded
        </text>
      </g>
    `;
  }
  
  subscribeToStore();
  
  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers
function getStatusLabel(status) {
  return STATUS_CONFIG[status]?.label || status;
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
