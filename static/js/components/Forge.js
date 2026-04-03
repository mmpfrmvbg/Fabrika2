/**
 * Factory OS — Forge Queue Component
 * Очередь задач для Forge (GET /api/queue/forge_inbox)
 */

import { store, subscribe } from '../state/store.js';
import { api } from '../api/client.js';
import { escapeHtml, formatTime } from '../utils/helpers.js';

export function ForgeComponent(container) {
  let unsubscribe = null;
  
  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'forge') {
        render(state.workersStatus, state.workItems);
      }
    });
  }
  
  function render(workersStatus, workItems) {
    if (!container) return;

    const queue = workersStatus?.workers || workersStatus?.active_workers || [];

    container.innerHTML = `
      <div class="page-header" style="margin-bottom:var(--space-4)">
        <div class="page-title">Очередь Forge</div>
        <div class="page-sub">Атомарные задачи, готовые к исполнению</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:var(--space-3)">
        ${!queue || queue.length === 0 ? `
          <div class="empty-state">
            <div class="es-icon">⚡</div>
            <div class="es-title">Очередь Forge пуста</div>
            <div class="es-sub">Нет активных задач в работе</div>
          </div>
        ` : queue.map(worker => renderWorkerCard(worker, workItems)).join('')}
      </div>
    `;
  }
  
  function renderWorkerCard(worker, workItems) {
    const wi = workItems?.find(w => w.id === worker.current_atom);
    const progress = calculateProgress(worker.lease_until);
    
    return `
      <div class="queue-card">
        <div class="queue-card-header">
          <div class="queue-card-title">${escapeHtml(wi?.title || worker.current_atom || 'Unknown')}</div>
          <div class="queue-card-id">${escapeHtml(worker.id?.slice(0, 8) || '')}...</div>
        </div>
        
        <div class="queue-card-files">
          ${wi?.files ? wi.files.map(f => `
            <span class="file-chip ${f.intent || 'modify'}">
              ${f.intent || 'modify'}: ${escapeHtml(f.path)}
            </span>
          `).join('') : '<span style="color:var(--text-faint);font-size:10px">Файлы не указаны</span>'}
        </div>
        
        <div class="queue-card-footer">
          <div class="lease-bar-wrap">
            <div class="lease-bar ${progress.status}" style="width: ${progress.percent}%"></div>
          </div>
          <span style="font-size:10px;color:var(--text-faint);min-width:60px;text-align:right;font-family:var(--font-mono)">
            ${formatTimeRemaining(worker.lease_until)}
          </span>
        </div>
      </div>
    `;
  }
  
  function calculateProgress(leaseUntil) {
    if (!leaseUntil) return { percent: 0, status: 'waiting' };
    
    const now = new Date();
    const end = new Date(leaseUntil);
    const total = end.getTime() - now.getTime();
    
    // Предполагаем 30 минут на задачу
    const maxTime = 30 * 60 * 1000;
    const elapsed = maxTime - total;
    const percent = Math.min(100, Math.max(0, (elapsed / maxTime) * 100));
    
    let status = 'active';
    if (percent > 80) status = 'warn';
    if (percent > 95 || total < 0) status = 'error';
    
    return { percent, status };
  }
  
  function formatTimeRemaining(leaseUntil) {
    if (!leaseUntil) return '—';
    
    const now = new Date();
    const end = new Date(leaseUntil);
    const diff = end - now;
    
    if (diff <= 0) return 'Истекло';
    
    const mins = Math.floor(diff / 60000);
    const secs = Math.floor((diff % 60000) / 1000);
    
    if (mins > 0) return `${mins}м ${secs}с`;
    return `${secs}с`;
  }
  
  subscribeToStore();

  return () => { if (unsubscribe) unsubscribe(); };
}

// Helpers импортируются из utils/helpers.js
