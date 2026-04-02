/**
 * Factory OS — ResultView Component
 * Экран завершённого Vision с итоговой статистикой
 */

import { store } from '../state/store.js';

/**
 * ResultView Component
 * @param {HTMLElement} container - контейнер для компонента
 * @param {Object} vision - Объект Vision
 */
export function ResultViewComponent(container, vision) {
  if (!container || !vision) return null;

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render() {
    if (!container) return;
    
    const stats = calculateVisionStats(vision.id);
    
    container.innerHTML = `
      <div class="rv-container">
        ${renderHeader()}
        ${renderStats(stats)}
        ${renderFiles(stats)}
        ${renderActions()}
        ${renderNextSteps()}
      </div>
    `;
  }

  function renderHeader() {
    return `
      <div class="rv-header">
        <span class="rv-icon">✅</span>
        <div class="rv-header-text">
          <h1>Vision завершён!</h1>
          <div class="rv-vision-title">"${escapeHtml(vision.title)}"</div>
        </div>
      </div>
    `;
  }

  function renderStats(stats) {
    return `
      <div class="rv-stats-grid">
        <div class="rv-stat">
          <span class="rv-stat-value">${stats.tasks}</span>
          <span class="rv-stat-label">задач выполнено</span>
        </div>
        <div class="rv-stat">
          <span class="rv-stat-value">${stats.files}</span>
          <span class="rv-stat-label">файлов изменено</span>
        </div>
        <div class="rv-stat">
          <span class="rv-stat-value">${formatDuration(stats.timeMs)}</span>
          <span class="rv-stat-label">времени заняло</span>
        </div>
        <div class="rv-stat">
          <span class="rv-stat-value">${stats.atomsDone}/${stats.atomsTotal}</span>
          <span class="rv-stat-label">атомов готово</span>
        </div>
      </div>
    `;
  }

  function renderFiles(stats) {
    if (!stats.changedFiles || stats.changedFiles.length === 0) {
      return '<div class="rv-no-files">Файлы не изменялись</div>';
    }
    
    return `
      <div class="rv-files-section">
        <div class="rv-files-title">Изменения:</div>
        <div class="rv-files-list">
          ${stats.changedFiles.slice(0, 10).map(f => `
            <div class="rv-file ${f.type}">
              <span class="rv-file-icon">${f.type === 'new' ? '+' : '~'}</span>
              <span class="rv-file-path">${escapeHtml(f.path)}</span>
              <span class="rv-file-lines">${f.linesChanged} строк</span>
            </div>
          `).join('')}
          ${stats.changedFiles.length > 10 ? `
            <div class="rv-file-more">
              + ещё ${stats.changedFiles.length - 10} файлов
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }

  function renderActions() {
    return `
      <div class="rv-actions">
        <button onclick="window.viewDiff()" class="rv-btn">
          📄 Посмотреть diff
        </button>
        <button onclick="window.commitAndPush()" class="rv-btn-primary">
          💾 Commit и push
        </button>
      </div>
    `;
  }

  function renderNextSteps() {
    return `
      <div class="rv-next">
        <div class="rv-next-title">Что дальше?</div>
        <div class="rv-next-actions">
          <button onclick="window.createNewVision()" class="rv-btn">
            + Создать новую идею
          </button>
          <button onclick="window.viewOtherProjects()" class="rv-btn">
            📁 Другие проекты
          </button>
        </div>
      </div>
    `;
  }

  // ═══════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════

  function calculateVisionStats(visionId) {
    const workItems = store.state.workItems || [];
    const descendants = getAllDescendants(visionId, workItems);
    const atoms = descendants.filter(d => d.kind === 'atom');
    
    const total = atoms.length;
    const done = atoms.filter(a => a.status === 'done' || a.status === 'archived').length;
    
    // Подсчёт файлов (заглушка — TODO: реальные данные из runs)
    const changedFiles = [
      { type: 'new', path: 'auth/errors.py', linesChanged: 45 },
      { type: 'modify', path: 'auth/handlers.py', linesChanged: 28 },
      { type: 'modify', path: 'auth/models.py', linesChanged: 15 },
      { type: 'new', path: 'tests/test_auth_errors.py', linesChanged: 67 }
    ];
    
    // Время (заглушка — TODO: реальное время из vision.created_at)
    const timeMs = 3 * 60 * 60 * 1000; // 3 часа
    
    return {
      tasks: descendants.length,
      files: changedFiles.length,
      timeMs,
      atomsTotal: total,
      atomsDone: done,
      changedFiles
    };
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

  function formatDuration(ms) {
    if (ms < 60000) return '< 1 мин';
    if (ms < 3600000) return `${Math.round(ms / 60000)} мин`;
    if (ms < 86400000) return `${Math.round(ms / 3600000)} ч`;
    return `${Math.round(ms / 86400000)} дн`;
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // ═══════════════════════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════════════════════

  render();
  
  return () => {
    // Cleanup если нужно
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS
// ═══════════════════════════════════════════════════════

window.viewDiff = () => {
  window.showFactoryToast('Diff viewer в разработке', 'ok');
};

window.commitAndPush = () => {
  window.showFactoryToast('Commit & Push в разработке', 'ok');
};

window.createNewVision = () => {
  if (window.openVisionCreator) {
    window.openVisionCreator();
  }
};

window.viewOtherProjects = () => {
  window.goPage('tree');
  window.showFactoryToast('Другие проекты', 'ok');
};
