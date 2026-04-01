/**
 * Factory OS — Agents, Improvements, Judgements Components
 * Страницы: Агенты, Improvements, Решения судьи
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';

// ═══════════════════════════════════════════════════════
// AGENTS COMPONENT
// ═══════════════════════════════════════════════════════

export function AgentsComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'agents') {
        render(state.agents);
      }
    });
  }
  
  function render(agents) {
    if (!container) return;

    // Нормализуем - agents может быть в обёртке { agents: [...] }
    const agentsArray = agents?.agents || Array.isArray(agents) ? agents : [];

    container.innerHTML = `
      <div class="page-header" style="margin-bottom:var(--space-4)">
        <div class="page-title">Агенты</div>
        <div class="page-sub">Реестр агентов, роли, модели, версии промптов</div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:var(--space-4)" id="agents-grid">
        ${!agentsArray || agentsArray.length === 0 ? `
          <div style="color:var(--text-muted);padding:40px;text-align:center">Загрузка агентов...</div>
        ` : agentsArray.map(agent => `
          <div class="card">
            <div style="display:flex;align-items:center;gap:var(--space-2);margin-bottom:var(--space-3)">
              <span class="role-badge r-${agent.role}">${escapeHtml(agent.role)}</span>
              <span class="badge s-${agent.status === 'active' ? 'done' : 'draft'}">${escapeHtml(agent.status)}</span>
              <span class="mono-id" style="margin-left:auto">${escapeHtml(agent.id?.slice(0, 12) || '')}...</span>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:var(--space-3);font-size:var(--text-sm)">
              <div>
                <div style="color:var(--text-faint);font-size:11px">Model</div>
                <div class="mono-id">${escapeHtml(agent.model_name || '—')}</div>
              </div>
              <div>
                <div style="color:var(--text-faint);font-size:11px">Prompt</div>
                <div class="mono-id">${escapeHtml(agent.prompt_version || '—')}</div>
              </div>
              <div>
                <div style="color:var(--text-faint);font-size:11px">Runs today</div>
                <div class="mono-id">${agent.runs_today || 0}</div>
              </div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }
  
  subscribeToStore();
  return () => { if (unsubscribe) unsubscribe(); };
}

// ═══════════════════════════════════════════════════════
// IMPROVEMENTS COMPONENT
// ═══════════════════════════════════════════════════════

export function ImprovementsComponent(container) {
  let unsubscribe = null;
  let selectedId = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'improvements') {
        render(state.improvements);
      }
    });
  }
  
  function render(improvements) {
    if (!container) return;

    // Нормализуем - improvements возвращает {candidates: [...], stats: {...}}
    const improvementsArray = improvements?.candidates || improvements?.items || Array.isArray(improvements) ? improvements : [];
    const stats = improvements?.stats || {};

    const kpiContainer = document.getElementById('improvements-kpi-row');
    const tableBody = document.getElementById('tbl-improvements-body');
    const detailCard = document.getElementById('improvements-detail-card');

    if (!improvements || !improvementsArray) {
      if (tableBody) tableBody.innerHTML = '<tr><td colspan="7" style="padding:18px;color:var(--text-muted)">Загрузка...</td></tr>';
      return;
    }

    // KPI
    if (kpiContainer) {
      kpiContainer.innerHTML = `
        <div class="kpi-card">
          <div class="kpi-label">Total</div>
          <div class="kpi-value">${improvementsArray.length}</div>
          <div class="kpi-delta neutral">Candidates</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Approved</div>
          <div class="kpi-value">${stats.approved || improvementsArray.filter(i => i.status === 'approved').length}</div>
          <div class="kpi-delta up">✓</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Converted</div>
          <div class="kpi-value">${stats.converted || improvementsArray.filter(i => i.status === 'converted').length}</div>
          <div class="kpi-delta neutral">To Vision</div>
        </div>
      `;
    }
    
    // Table
    if (tableBody) {
      tableBody.innerHTML = improvementsArray.map(imp => `
        <tr onclick="window.selectImprovement('${escapeHtml(imp.id)}')" style="cursor:pointer">
          <td>
            <div style="display:flex;align-items:center;gap:6px">
              <div style="width:8px;height:8px;border-radius:50%;background:${getPriorityColor(imp.priority_score)}"></div>
              <span class="mono-id">${(imp.priority_score * 100).toFixed(0)}</span>
            </div>
          </td>
          <td><span class="mono-id" style="font-size:10px">${escapeHtml(imp.source_type)}</span></td>
          <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(imp.title)}</td>
          <td><span class="badge s-planned">${escapeHtml(imp.fix_target)}</span></td>
          <td><span class="badge s-${imp.risk_level === 'high' ? 'failed' : imp.risk_level === 'medium' ? 'blocked' : 'done'}">${escapeHtml(imp.risk_level)}</span></td>
          <td><span class="badge s-${imp.status === 'converted' ? 'done' : imp.status === 'approved' ? 'ready_for_work' : 'draft'}">${escapeHtml(imp.status)}</span></td>
          <td>
            <div style="display:flex;gap:4px">
              ${imp.status === 'proposed' ? `
                <button onclick="event.stopPropagation(); window.approveImprovement('${escapeHtml(imp.id)}')" style="padding:2px 6px;font-size:10px;background:var(--success-dim);border:1px solid var(--success);color:var(--success);border-radius:3px;cursor:pointer">✓</button>
                <button onclick="event.stopPropagation(); window.rejectImprovement('${escapeHtml(imp.id)}')" style="padding:2px 6px;font-size:10px;background:var(--error-dim);border:1px solid var(--error);color:var(--error);border-radius:3px;cursor:pointer">✗</button>
              ` : ''}
              ${imp.status === 'approved' ? `
                <button onclick="event.stopPropagation(); window.convertImprovement('${escapeHtml(imp.id)}')" style="padding:2px 6px;font-size:10px;background:var(--primary-dim);border:1px solid var(--primary);color:var(--primary);border-radius:3px;cursor:pointer">→ Vision</button>
              ` : ''}
            </div>
          </td>
        </tr>
      `).join('');
    }
    
    // Detail pane
    if (detailCard && selectedId) {
      const selected = improvementsArray.find(i => i.id === selectedId);
      if (selected) {
        detailCard.style.display = 'block';
        document.getElementById('improvements-detail-body').innerHTML = `
          <div style="margin-bottom:var(--space-2)">
            <strong style="color:var(--text)">Title:</strong> ${escapeHtml(selected.title)}
          </div>
          <div style="margin-bottom:var(--space-2)">
            <strong style="color:var(--text)">Description:</strong>
            <div style="margin-top:4px;color:var(--text-muted);line-height:1.5">${escapeHtml(selected.description)}</div>
          </div>
          <div style="margin-bottom:var(--space-2)">
            <strong style="color:var(--text)">Evidence:</strong>
            <div style="margin-top:4px;color:var(--text-muted);font-family:var(--font-mono);font-size:11px;white-space:pre-wrap">${escapeHtml(selected.evidence)}</div>
          </div>
        `;
      }
    }
  }
  
  // Глобальные функции
  window.selectImprovement = (id) => {
    selectedId = id;
    const { improvements } = store.state;
    render(improvements);
  };
  
  window.approveImprovement = async (id) => {
    try {
      await api.approveImprovement(id);
      await store.loadImprovements();
    } catch (e) {
      console.error('Failed to approve:', e);
    }
  };
  
  window.rejectImprovement = async (id) => {
    try {
      await api.rejectImprovement(id);
      await store.loadImprovements();
    } catch (e) {
      console.error('Failed to reject:', e);
    }
  };
  
  window.convertImprovement = async (id) => {
    try {
      await api.convertImprovement(id);
      await store.loadImprovements();
    } catch (e) {
      console.error('Failed to convert:', e);
    }
  };
  
  subscribeToStore();
  return () => { if (unsubscribe) unsubscribe(); };
}

// ═══════════════════════════════════════════════════════
// JUDGEMENTS COMPONENT
// ═══════════════════════════════════════════════════════

export function JudgementsComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'judgements') {
        render(state.judgements);
      }
    });
  }
  
  function render(judgements) {
    // Нормализуем - judgements может быть в обёртке { items: [...] } или массивом
    const judgementsArray = judgements?.items || Array.isArray(judgements) ? judgements : [];

    container.innerHTML = `
      <div class="page-header" style="margin-bottom:var(--space-4)">
        <div class="page-title">Решения судьи</div>
        <div class="page-sub">Решения судьи, привязка к задачам, переходам и кластерам сбоев</div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-header-icon">◈</span> Решения</div>
        <div class="tbl-wrap">
          <table id="tbl-judgements">
            <thead>
              <tr>
                <th>ID</th>
                <th>Work Item</th>
                <th>Verdict</th>
                <th>Reason</th>
                <th>Event</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              ${!judgementsArray || judgementsArray.length === 0 ? `
                <tr><td colspan="6" style="padding:40px;text-align:center;color:var(--text-muted)">Загрузка...</td></tr>
              ` : judgementsArray.map(j => `
                <tr>
                  <td class="mono-id">${escapeHtml(j.id?.slice(0, 8) || '')}...</td>
                  <td class="mono-id">${escapeHtml(j.work_item_id || '—')}</td>
                  <td>
                    <span class="badge s-${j.verdict === 'approved' ? 'done' : j.verdict === 'rejected' ? 'failed' : 'blocked'}">
                      ${escapeHtml(j.verdict)}
                    </span>
                  </td>
                  <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(j.reason_code || '—')}</td>
                  <td class="mono-id" style="font-size:10px">${escapeHtml(j.event || '—')}</td>
                  <td class="mono-id" style="font-size:10px;color:var(--text-faint)">${formatTime(j.created_at)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }
  
  subscribeToStore();
  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers
function getPriorityColor(score) {
  if (score > 0.7) return 'var(--error)';
  if (score > 0.4) return 'var(--warning)';
  return 'var(--success)';
}

function formatTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
