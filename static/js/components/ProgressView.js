/**
 * Factory OS — ProgressView Component
 * Прогресс по этапам Vision и умный ETA
 */

import { store, subscribe } from '../state/store.js';

// Кэширование ETA
let etaCache = {
  visionId: null,
  value: 'Расчёт...',
  timestamp: 0
};

/**
 * ProgressView Component
 * @param {HTMLElement} container - контейнер для компонента
 */
export function ProgressViewComponent(container) {
  if (!container) return null;
  
  let unsubscribe = null;
  let updateInterval = null;

  // ═══════════════════════════════════════════════════════
  // SUBSCRIBE TO STORE
  // ═══════════════════════════════════════════════════════

  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      if (state.activePage === 'autonomous' || state.activePage === 'tree') {
        render();
      }
    });
  }

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render() {
    if (!container) return;
    
    const vision = getCurrentVision();
    if (!vision) {
      container.innerHTML = '<div style="color:var(--text-muted);padding:var(--space-4)">Нет активного Vision</div>';
      return;
    }
    
    const stages = renderStages(vision.id);
    const eta = calculateSmartETA(vision.id);
    
    container.innerHTML = `
      <div class="pv-container">
        <div class="pv-header">
          <span class="pv-header-title">Прогресс выполнения</span>
          <span class="pv-eta">⏱️ ${eta}</span>
        </div>
        
        ${stages}
        
        <div class="pv-summary">
          ${renderSummary(vision.id)}
        </div>
      </div>
    `;
  }

  function renderStages(visionId) {
    const stages = {
      planning: { label: '📋 Планирование', color: 'var(--blue)' },
      forge: { label: '🔨 Forge (код)', color: 'var(--orange)' },
      review: { label: '👁️ Review', color: 'var(--purple)' },
      judge: { label: '⚖️ Judge', color: 'var(--gold)' }
    };
    
    return Object.entries(stages).map(([key, stage]) => {
      const stats = getStageStats(visionId, key);
      const percent = stats.total ? Math.round((stats.done / stats.total) * 100) : 0;
      
      return `
        <div class="pv-stage">
          <div class="pv-stage-header">
            <span class="pv-stage-label">${stage.label}</span>
            <span class="pv-stage-percent">${percent}%</span>
          </div>
          <div class="pv-stage-bar">
            <div class="pv-stage-fill" style="width: ${percent}%; background: ${stage.color}"></div>
          </div>
          <div class="pv-stage-count">${stats.done}/${stats.total}</div>
        </div>
      `;
    }).join('');
  }

  function renderSummary(visionId) {
    const workItems = store.state.workItems || [];
    const descendants = getAllDescendants(visionId, workItems);
    const atoms = descendants.filter(d => d.kind === 'atom');
    
    const total = atoms.length;
    const done = atoms.filter(a => a.status === 'done' || a.status === 'archived').length;
    const inProgress = atoms.filter(a => a.status === 'in_progress' || a.status === 'in_review').length;
    const ready = atoms.filter(a => a.status === 'ready_for_work').length;
    
    return `
      <div class="pv-summary-grid">
        <div class="pv-summary-item">
          <span class="pv-summary-value">${total}</span>
          <span class="pv-summary-label">Всего атомов</span>
        </div>
        <div class="pv-summary-item">
          <span class="pv-summary-value" style="color: var(--success)">${done}</span>
          <span class="pv-summary-label">Готово</span>
        </div>
        <div class="pv-summary-item">
          <span class="pv-summary-value" style="color: var(--orange)">${inProgress}</span>
          <span class="pv-summary-label">В работе</span>
        </div>
        <div class="pv-summary-item">
          <span class="pv-summary-value" style="color: var(--primary)">${ready}</span>
          <span class="pv-summary-label">Готово к запуску</span>
        </div>
      </div>
    `;
  }

  // ═══════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════

  function getCurrentVision() {
    const visions = store.state.visions?.visions || store.state.visions || [];
    return visions.find(v => v.status !== 'done' && v.status !== 'archived') || visions[0];
  }

  function getAllDescendants(rootId, workItems) {
    const descendants = [];
    const queue = [rootId];
    
    while (queue.length > 0) {
      const id = queue.shift();
      const children = workItems.filter(w => w.parent_id === id);
      for (const child of children) {
        descendants.push(child);
        queue.push(child.id);
      }
    }
    
    return descendants;
  }

  function getStageStats(visionId, stageKey) {
    const workItems = store.state.workItems || [];
    const descendants = getAllDescendants(visionId, workItems);
    const atoms = descendants.filter(d => d.kind === 'atom');
    
    // Статусы для каждого этапа (без дубликатов)
    const stageStatuses = {
      planning: ['draft', 'planned', 'ready_for_judge'],
      forge: ['ready_for_work', 'in_progress', 'forge_started'],
      review: ['in_review', 'forge_completed'],
      judge: ['ready_for_judge', 'judge_rejected', 'review_rejected']
    };
    
    const statuses = stageStatuses[stageKey] || [];
    const done = atoms.filter(a => statuses.includes(a.status)).length;
    
    // Для forge/review/judge считаем все атомы как total
    // Для planning — только не начатые
    const total = stageKey === 'planning' 
      ? atoms.filter(a => !['done', 'archived'].includes(a.status)).length
      : atoms.length;
    
    return { done, total };
  }

  function calculateSmartETA(visionId) {
    const now = Date.now();
    
    // Проверка кэша (обновляем каждые 30 секунд)
    if (etaCache.visionId === visionId && (now - etaCache.timestamp) < 30000) {
      return etaCache.value;
    }
    
    const workItems = store.state.workItems || [];
    const descendants = getAllDescendants(visionId, workItems);
    const atoms = descendants.filter(d => d.kind === 'atom');
    
    const total = atoms.length;
    const done = atoms.filter(a => a.status === 'done' || a.status === 'archived').length;
    const progress = total ? (done / total) : 0;
    
    if (progress >= 1) {
      etaCache = { visionId, value: 'Завершено', timestamp: now };
      return 'Завершено';
    }
    
    if (progress < 0.05) {
      etaCache = { visionId, value: 'Расчёт...', timestamp: now };
      return 'Расчёт...';
    }
    
    // 1. История похожих Vision (заглушка — TODO: реальная история)
    const avgTimeMs = 4 * 60 * 60 * 1000; // 4 часа по умолчанию
    
    // 2. Прогноз на основе текущего темпа
    const vision = store.state.visions?.visions?.find(v => v.id === visionId);
    const startTime = vision?.created_at ? new Date(vision.created_at).getTime() : now;
    const elapsed = now - startTime;
    const predictedTotal = elapsed / progress;
    const remaining = predictedTotal - elapsed;
    
    // 3. Минимум из двух оценок
    const eta = Math.min(avgTimeMs * (1 - progress), remaining);
    
    // Форматирование
    const formatted = formatDuration(Math.max(0, eta));
    etaCache = { visionId, value: formatted, timestamp: now };
    
    return formatted;
  }

  function formatDuration(ms) {
    if (ms < 60000) return '< 1 мин';
    if (ms < 3600000) return `~${Math.round(ms / 60000)} мин`;
    if (ms < 86400000) return `~${Math.round(ms / 3600000)} ч`;
    return `~${Math.round(ms / 86400000)} дн`;
  }

  // ═══════════════════════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════════════════════

  subscribeToStore();
  
  // Обновление ETA каждые 30 секунд
  updateInterval = setInterval(() => {
    if (container && store.state.activePage === 'autonomous') {
      render();
    }
  }, 30000);
  
  return () => {
    if (unsubscribe) unsubscribe();
    if (updateInterval) clearInterval(updateInterval);
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS
// ═══════════════════════════════════════════════════════

window.ProgressViewComponent = ProgressViewComponent;
