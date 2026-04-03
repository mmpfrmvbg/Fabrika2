/**
 * Factory OS — FSM Component
 * Визуализация State Machine и таблицы переходов
 */

import { store, subscribe } from '../state/store.js';
import { escapeHtml, getStatusLabel } from '../utils/helpers.js';

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

    const transitions = fsm?.transitions || Array.isArray(fsm) ? fsm : [];
    const stateCounts = calculateStateCounts(workItems);

    container.innerHTML = `
      <div class="card" style="margin-bottom:var(--space-4)">
        <div class="card-header"><span class="card-header-icon">◈</span> State Summary</div>
        <div style="display:flex;gap:var(--space-2);flex-wrap:wrap">
          ${Object.entries(stateCounts).map(([state, count]) => `
            <span class="badge s-${state}">${getStatusLabel(state)}: ${count}</span>
          `).join('')}
        </div>
      </div>
      <div class="fsm-canvas-wrap" style="margin-bottom:var(--space-4)">
        <svg id="fsm-svg" viewBox="0 0 900 620" width="900" height="620" xmlns="http://www.w3.org/2000/svg" style="font-family:var(--font-mono)"></svg>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-header-icon">◈</span> Transitions Table</div>
        <div class="tbl-wrap">
          <table id="tbl-transitions"></table>
        </div>
      </div>
    `;

    // Заполняем элементы — они уже существуют в DOM
    const tblEl = container.querySelector('#tbl-transitions');
    if (tblEl && transitions && transitions.length > 0) {
      tblEl.innerHTML = renderTransitionsTable({ transitions });
    }

    const svgEl = container.querySelector('#fsm-svg');
    if (svgEl) {
      svgEl.innerHTML = renderFSMSvg({ transitions });
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
    const transitions = fsm?.transitions || [];
    const workItems = store.state.workItems || [];
    
    // Node positions
    const nodes = [
      { id:'draft',            x:80,  y:60,  label:'draft',            color:'#2a2a35', stroke:'#4444aa' },
      { id:'planned',          x:280, y:60,  label:'planned',           color:'#1a2535', stroke:'#5588cc' },
      { id:'ready_for_judge',  x:480, y:60,  label:'→ judge',           color:'#2a2a10', stroke:'#aaaa40' },
      { id:'judge_rejected',   x:680, y:60,  label:'judge ✗',           color:'#2a1020', stroke:'#bb4488' },
      { id:'ready_for_work',   x:280, y:220, label:'ready',             color:'#1a3030', stroke:'#40a0a8' },
      { id:'in_progress',      x:480, y:220, label:'running',           color:'#2a1e10', stroke:'#cc8840' },
      { id:'in_review',        x:680, y:220, label:'in review',         color:'#281838', stroke:'#9060cc' },
      { id:'review_rejected',  x:680, y:380, label:'review ✗',          color:'#2a1520', stroke:'#cc5555' },
      { id:'blocked',          x:80,  y:380, label:'blocked',           color:'#2a1e10', stroke:'#cc7730' },
      { id:'done',             x:480, y:380, label:'done',              color:'#10281a', stroke:'#40aa60' },
      { id:'cancelled',        x:280, y:380, label:'cancelled',         color:'#202022', stroke:'#505055' },
      { id:'archived',         x:480, y:500, label:'archived',          color:'#181818', stroke:'#404040' },
    ];
    const nodeMap = {};
    nodes.forEach(n => nodeMap[n.id] = n);

    // Transitions: [from, to, label, isError, cx_offset, cy_offset]
    const edges = [
      ['draft','planned','submitted',false,0,-20],
      ['planned','ready_for_judge','ready',false,0,-20],
      ['ready_for_judge','judge_rejected','rejected',true,0,-20],
      ['ready_for_judge','ready_for_work','approved↓',false,-60,30],
      ['ready_for_judge','planned','needs decomp',false,-60,-30],
      ['judge_rejected','ready_for_judge','revised',false,0,20],
      ['judge_rejected','cancelled','dropped',true,0,30],
      ['ready_for_work','in_progress','forge_start',false,0,-20],
      ['in_progress','in_review','completed',false,0,-20],
      ['in_progress','ready_for_work','failed/retry',true,0,20],
      ['in_review','done','passed',false,-40,30],
      ['in_review','review_rejected','failed',true,0,-20],
      ['review_rejected','ready_for_work','retry+judge',false,-180,-80],
      ['done','archived','archive',false,0,-20],
    ];

    const W = 900, H = 600;
    const NW = 90, NH = 36;

    let svgContent = `
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#4f98a3"/>
        </marker>
        <marker id="arrow-err" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#d163a7"/>
        </marker>
      </defs>
    `;

    // Draw edges
    edges.forEach(([from, to, label, isErr, cxOff, cyOff]) => {
      const fn = nodeMap[from], tn = nodeMap[to];
      if (!fn || !tn) return;
      const x1 = fn.x + NW/2, y1 = fn.y + NH/2;
      const x2 = tn.x + NW/2, y2 = tn.y + NH/2;
      const mx = (x1+x2)/2 + (cxOff||0);
      const my = (y1+y2)/2 + (cyOff||0);
      const col = isErr ? '#d163a7' : '#4f98a3';
      const dash = isErr ? 'stroke-dasharray="5,3"' : '';
      svgContent += `
        <path d="M${x1},${y1} Q${mx},${my} ${x2},${y2}" stroke="${col}" stroke-width="1.5" fill="none" ${dash} marker-end="url(#arrow${isErr?'-err':''})"/>
        <text x="${mx}" y="${my - 6}" fill="${col}" font-size="9" text-anchor="middle" font-family="JetBrains Mono">${label}</text>
      `;
    });

    // Determine active nodes
    const activeStatuses = new Set(workItems.map(w => w.status));

    // Draw nodes
    nodes.forEach(n => {
      const isActive = activeStatuses.has(n.id);
      const count = workItems.filter(w => w.status === n.id).length;
      svgContent += `
        <g class="fsm-node" onclick='window.highlightFSMStatus("${n.id}")'>
          <rect x="${n.x}" y="${n.y}" width="${NW}" height="${NH}"
            rx="6" fill="${n.color}" stroke="${isActive ? n.stroke : '#333'}"
            stroke-width="${isActive ? 2 : 1}"
            filter="${isActive ? 'drop-shadow(0 0 4px ' + n.stroke + '44)' : 'none'}"/>
          <text x="${n.x + NW/2}" y="${n.y + NH/2 - 3}" fill="${isActive ? '#e2e2e4' : '#606068'}"
            font-size="11" font-weight="${isActive ? '600' : '400'}" text-anchor="middle" font-family="JetBrains Mono">${n.label}</text>
          ${count > 0 ? `<text x="${n.x + NW/2}" y="${n.y + NH/2 + 10}" fill="${n.stroke}"
            font-size="9" text-anchor="middle" font-family="JetBrains Mono">${count} задач${count > 4 ? '' : count > 1 ? 'и' : 'а'}</text>` : ''}
        </g>
      `;
    });

    // Highlight selected work item status
    const selectedId = store.state.selectedWorkItemId;
    const selectedWi = workItems.find(w => w.id === selectedId);
    if (selectedWi && nodeMap[selectedWi.status]) {
      const n = nodeMap[selectedWi.status];
      const cx = n.x + NW / 2, cy = n.y + NH / 2;
      svgContent += `<circle cx="${cx}" cy="${cy}" r="52" fill="none" stroke="#d4a040" stroke-width="2" stroke-dasharray="6 4" opacity="0.9"/>`;
      svgContent += `<text x="${cx}" y="${n.y - 6}" fill="#d4a040" font-size="10" text-anchor="middle" font-family="JetBrains Mono">router → ${selectedWi.status}</text>`;
    }

    return svgContent;
  }

  // Глобальная функция для подсветки статуса
  window.highlightFSMStatus = (status) => {
    // Скроллим к дереву и показываем задачи с этим статусом
    showFactoryToast(`Статус: ${status}`, 'ok');
    // TODO: фильтр дерева по статусу
  };
  
  subscribeToStore();
  
  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers
function calculateStateCounts(workItems) {
  const counts = {};
  if (!workItems || !Array.isArray(workItems)) return counts;
  workItems.forEach(wi => {
    const status = wi.status || 'unknown';
    counts[status] = (counts[status] || 0) + 1;
  });
  return counts;
}

// Helpers импортируются из utils/helpers.js
