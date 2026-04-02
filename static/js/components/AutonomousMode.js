/**
 * Factory OS — Autonomous Mode Component
 * Главный экран автономной фабрики для пользователей
 */

import { store, subscribe } from '../state/store.js';

// Состояние компонента
let currentVision = null;
let activeTasks = [];
let queuedTasks = [];

/**
 * Autonomous Mode Component
 * @param {HTMLElement} container - контейнер для компонента
 */
export function AutonomousModeComponent(container) {
  if (!container) return null;
  
  let unsubscribe = null;

  // ═══════════════════════════════════════════════════════
  // SUBSCRIBE TO STORE
  // ═══════════════════════════════════════════════════════

  function subscribeToStore() {
    unsubscribe = subscribe((state) => {
      updateData(state);
      render();
    });
  }

  // ═══════════════════════════════════════════════════════
  // UPDATE DATA
  // ═══════════════════════════════════════════════════════

  function updateData(state) {
    const { workItems, visions, runs, orchestrator } = state;
    
    // Находим активный Vision (первый не completed)
    const visionsArray = visions?.visions || visions || [];
    currentVision = visionsArray.find(v => v.status !== 'done' && v.status !== 'archived') || visionsArray[0];
    
    // Считаем задачи
    if (workItems && currentVision) {
      const visionTasks = getAllDescendants(currentVision.id, workItems);
      activeTasks = visionTasks.filter(t => t.status === 'in_progress' || t.status === 'in_review');
      queuedTasks = visionTasks.filter(t => t.status === 'ready_for_work' || t.status === 'planned');
    }
    
    // Сохраняем в глобальное состояние для других компонентов
    window.autonomousModeState = {
      vision: currentVision,
      activeTasks,
      queuedTasks,
      orchestrator: orchestrator || {}
    };
  }

  function getAllDescendants(rootId, workItems) {
    // Итеративный обход вместо рекурсии (защита от stack overflow)
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

  // ═══════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════

  function render() {
    if (!container) return;
    
    if (!currentVision) {
      renderEmptyState();
      return;
    }
    
    const progress = calculateProgress(currentVision);
    const eta = calculateETA(progress);
    const recentActivity = getRecentActivity();
    
    container.innerHTML = `
      <div class="autonomous-mode-container">
        ${renderHeader()}
        ${renderMainCard(progress, eta, recentActivity)}
        ${renderOtherProjects()}
        ${renderCreateNewButton()}
      </div>
    `;
    
    attachEventListeners();
  }

  function renderEmptyState() {
    container.innerHTML = `
      <div class="autonomous-mode-container">
        ${renderHeader()}
        <div class="am-empty-state">
          <div class="am-empty-icon">🏭</div>
          <div class="am-empty-title">Фабрика готова к работе</div>
          <div class="am-empty-sub">Создайте свою первую идею и фабрика выполнит её автономно</div>
          <button type="button" class="am-btn-primary" onclick="window.openVisionCreator()">
            ✨ Создать идею
          </button>
          <div style="margin-top: var(--space-4); font-size: var(--text-sm); color: var(--text-muted)">
            или <button type="button" class="am-link" onclick="window.switchToDeveloperMode()">перейти в режим разработчика</button>
          </div>
        </div>
      </div>
    `;
  }

  function renderHeader() {
    return `
      <div class="am-header">
        <div class="am-header-title">
          <span class="am-header-icon">🏭</span>
          <h1>Fabrika2 — Автономная фабрика кода</h1>
        </div>
        <div class="am-mode-switcher">
          <span class="am-mode-label">Режим:</span>
          <span class="am-mode-badge am-mode-active">🤖 Автономный</span>
          <button type="button" class="am-mode-toggle" onclick="window.switchToDeveloperMode()" title="Переключиться в режим разработчика">
            🔧 Developer Mode
          </button>
        </div>
      </div>
    `;
  }

  function renderMainCard(progress, eta, recentActivity) {
    const statusClass = getFactoryStatusClass();
    
    return `
      <div class="am-main-card">
        <div class="am-status-row">
          <span class="am-status-dot ${statusClass}"></span>
          <span class="am-status-text">${getFactoryStatusText()}</span>
        </div>
        
        <div class="am-progress-section">
          <div class="am-progress-label">Прогресс: ${progress.percent}%</div>
          <div class="am-progress-bar">
            <div class="am-progress-fill" style="width: ${progress.percent}%"></div>
          </div>
          <div class="am-progress-details">
            <span>📦 ${progress.done}/${progress.total} задач готово</span>
            <span>⏱️ ${eta}</span>
          </div>
        </div>
        
        <div class="am-vision-info">
          <div class="am-vision-label">Vision:</div>
          <div class="am-vision-title">${escapeHtml(currentVision?.title || 'Без названия')}</div>
        </div>
        
        <div class="am-activity-section">
          <div class="am-activity-label">Активность:</div>
          <div class="am-activity-list">
            ${recentActivity.map(activity => `
              <div class="am-activity-item">
                <span class="am-activity-icon">${activity.icon}</span>
                <span class="am-activity-text">${activity.text}</span>
                <span class="am-activity-time">${activity.time}</span>
              </div>
            `).join('')}
          </div>
        </div>
        
        <div class="am-actions-row">
          <button type="button" class="am-btn-secondary" onclick="window.toggleFactoryPause()">
            ${window.factoryPaused ? '▶️ Продолжить' : '⏸️ Пауза'}
          </button>
          <button type="button" class="am-btn-secondary" onclick="window.showAutonomousDetails()">
            📊 Детали
          </button>
          <button type="button" class="am-btn-primary" onclick="window.openChatFromAutonomous()">
            💬 Спросить Qwen
          </button>
        </div>
      </div>
    `;
  }

  function renderOtherProjects() {
    const visions = store.state.visions?.visions || store.state.visions || [];
    const otherVisions = visions.filter(v => v.id !== currentVision?.id && v.status !== 'done');
    
    if (otherVisions.length === 0) return '';
    
    return `
      <div class="am-divider"></div>
      <div class="am-other-projects">
        <div class="am-section-label">Другие проекты:</div>
        ${otherVisions.slice(0, 3).map(v => {
          const vProgress = calculateProgress(v);
          return `
            <div class="am-project-item" onclick="window.switchToVision('${v.id}')">
              <span class="am-project-icon">📁</span>
              <span class="am-project-title">${escapeHtml(v.title)}</span>
              <span class="am-project-progress">${vProgress.percent}%</span>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  function renderCreateNewButton() {
    return `
      <div class="am-create-new-section">
        <button type="button" class="am-btn-create" onclick="window.openVisionCreator()">
          + Создать новую идею
        </button>
      </div>
    `;
  }

  function attachEventListeners() {
    // Event listeners будут добавлены через глобальные функции
  }

  // ═══════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════

  function calculateProgress(vision) {
    const workItems = store.state.workItems || [];
    const descendants = getAllDescendants(vision.id, workItems);
    const atoms = descendants.filter(d => d.kind === 'atom');
    
    const total = atoms.length;
    const done = atoms.filter(a => a.status === 'done' || a.status === 'archived').length;
    const percent = total ? Math.round((done / total) * 100) : 0;
    
    return { total, done, percent };
  }

  function calculateETA(progress) {
    if (progress.percent >= 100) return 'Завершено';
    if (progress.percent === 0 || progress.percent < 5) return 'Расчёт...';
    
    // Простая эвристика: если 10% за 30 минут, то 100% за 5 часов
    const elapsedMinutes = 30; // TODO: реальное время от начала
    const totalMinutes = Math.round(elapsedMinutes / (progress.percent / 100));
    const remainingMinutes = totalMinutes - elapsedMinutes;
    
    if (remainingMinutes < 0) return 'Почти готово';
    if (remainingMinutes < 60) return `~${remainingMinutes} мин`;
    const hours = Math.round(remainingMinutes / 60);
    return `~${hours} ч`;
  }

  function getRecentActivity() {
    const journal = store.state.journal?.items || [];
    const recent = journal.slice(0, 5);
    
    return recent.map(item => {
      let icon = '📝';
      if (item.event_type?.includes('forge')) icon = '⚡';
      if (item.event_type?.includes('review')) icon = '👁️';
      if (item.event_type?.includes('judge')) icon = '⚖️';
      if (item.status === 'completed') icon = '✅';
      if (item.status === 'failed') icon = '❌';
      
      return {
        icon,
        text: item.message || item.event_type || 'Событие',
        time: formatTimeAgo(item.event_time)
      };
    });
  }

  function getFactoryStatusClass() {
    const { orchestrator } = window.autonomousModeState || {};
    if (window.factoryPaused) return 'paused';
    if (orchestrator?.running) return 'active';
    return 'idle';
  }

  function getFactoryStatusText() {
    if (window.factoryPaused) return 'На паузе';
    const { orchestrator } = window.autonomousModeState || {};
    if (orchestrator?.running) return 'Фабрика работает';
    return 'Ожидание';
  }

  function formatTimeAgo(iso) {
    if (!iso) return '';
    const seconds = Math.floor((new Date() - new Date(iso)) / 1000);
    if (seconds < 60) return `${seconds}с назад`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}м назад`;
    const hours = Math.floor(minutes / 60);
    return `${hours}ч назад`;
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

  subscribeToStore();
  
  return () => {
    if (unsubscribe) unsubscribe();
  };
}

// ═══════════════════════════════════════════════════════
// GLOBAL FUNCTIONS
// ═══════════════════════════════════════════════════════

window.factoryPaused = false;

window.toggleFactoryPause = () => {
  window.factoryPaused = !window.factoryPaused;
  const status = window.factoryPaused ? 'paused' : 'running';
  showFactoryToast(`Фабрика ${window.factoryPaused ? 'на паузе' : 'продолжает работу'}`, 'ok');
  
  // Сохранение в localStorage
  if (window.Storage && window.StorageKeys) {
    window.Storage.set(window.StorageKeys.PAUSED, window.factoryPaused);
  }
  
  // TODO: API вызов для паузы/продолжения
  // await api.pauseFactory(status);
};

window.showAutonomousDetails = () => {
  // Переход на страницу Tree с фокусом на текущий Vision
  const { vision } = window.autonomousModeState || {};
  if (vision) {
    store.selectWorkItem(vision.id);
    window.goPage('tree');
  }
};

window.openChatFromAutonomous = () => {
  store.openChat();
};

window.switchToVision = (visionId) => {
  store.selectWorkItem(visionId);
  window.goPage('tree');
  showFactoryToast('Переключение на проект', 'ok');
};

window.switchToDeveloperMode = () => {
  store.update({ activePage: 'dashboard' });
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-dashboard')?.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('nav-dashboard')?.classList.add('active');
  
  // Сохранение в localStorage
  if (window.Storage && window.StorageKeys) {
    window.Storage.set(window.StorageKeys.MODE, 'developer');
  }
  
  showFactoryToast('Режим разработчика включён', 'ok');
};

window.switchToAutonomousMode = () => {
  store.update({ activePage: 'autonomous' });
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-autonomous')?.classList.add('active');
  
  // Сохранение в localStorage
  if (window.Storage && window.StorageKeys) {
    window.Storage.set(window.StorageKeys.MODE, 'autonomous');
  }
  
  showFactoryToast('Автономный режим включён', 'ok');
};

function showFactoryToast(message, kind = 'ok') {
  const el = document.getElementById('factory-toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'factory-toast visible ' + (kind === 'err' ? 'err' : 'ok');
  clearTimeout(el._hideT);
  el._hideT = setTimeout(() => { el.classList.remove('visible'); }, 3000);
}
