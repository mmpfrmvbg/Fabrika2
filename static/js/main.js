/**
 * Factory OS — Main Entry Point
 * Точка входа приложения
 */

import { store } from './state/store.js';
import { ChatComponent } from './components/Chat.js';
import { TreeComponent } from './components/Tree.js';
import { JournalComponent } from './components/Journal.js';
import { DashboardComponent } from './components/Dashboard.js';
import { AnalyticsComponent } from './components/Analytics.js';
import { ForgeComponent } from './components/Forge.js';
import { FSMComponent } from './components/FSM.js';
import { AgentsComponent, ImprovementsComponent, JudgementsComponent, HRComponent, FailuresComponent } from './components/Others.js';
import { api } from './api/client.js';

// ═══════════════════════════════════════════════════════
// GLOBAL STATE
// ═══════════════════════════════════════════════════════

window.WI_INDEX = {};
let componentCleanup = {};

// ═══════════════════════════════════════════════════════
// COMPONENTS INIT
// ═══════════════════════════════════════════════════════

function initComponents() {
  const chatPanel = document.getElementById('chat-panel');
  if (chatPanel) componentCleanup.chat = ChatComponent(chatPanel);
  
  const treeRoot = document.getElementById('task-tree-root');
  if (treeRoot) componentCleanup.tree = TreeComponent(treeRoot);
  
  const logEntries = document.getElementById('log-entries');
  if (logEntries) componentCleanup.journal = JournalComponent(logEntries);
  
  const dashboardContainer = document.getElementById('page-dashboard');
  if (dashboardContainer) componentCleanup.dashboard = DashboardComponent(dashboardContainer);
  
  const analyticsContainer = document.getElementById('page-analytics');
  if (analyticsContainer) componentCleanup.analytics = AnalyticsComponent(analyticsContainer);
  
  const forgeContainer = document.getElementById('forge-queue-list');
  if (forgeContainer) componentCleanup.forge = ForgeComponent(forgeContainer);
  
  const fsmContainer = document.getElementById('page-fsm');
  if (fsmContainer) componentCleanup.fsm = FSMComponent(fsmContainer);
  
  const agentsContainer = document.getElementById('agents-grid');
  if (agentsContainer) componentCleanup.agents = AgentsComponent(agentsContainer);
  
  const improvementsContainer = document.getElementById('page-improvements');
  if (improvementsContainer) componentCleanup.improvements = ImprovementsComponent(improvementsContainer);
  
  const judgementsContainer = document.getElementById('page-judgements');
  if (judgementsContainer) componentCleanup.judgements = JudgementsComponent(judgementsContainer);

  const hrContainer = document.getElementById('page-hr');
  if (hrContainer) componentCleanup.hr = HRComponent(hrContainer);

  const failuresContainer = document.getElementById('page-failures');
  if (failuresContainer) componentCleanup.failures = FailuresComponent(failuresContainer);

  window.toggleChat = () => {
    const workItemId = store.state.selectedWorkItemId;
    if (store.state.chat.isOpen) store.closeChat();
    else store.openChat(workItemId);
  };
}

// ═══════════════════════════════════════════════════════
// LOAD DATA
// ═══════════════════════════════════════════════════════

async function loadInitialData() {
  try {
    await Promise.all([
      store.loadTree(),
      store.loadJournal(),
      store.loadWorkItems(),
      store.loadVisions(),
      store.loadOrchestratorStatus(),
      store.loadWorkersStatus(),
      store.loadAgents(),
      store.loadFsm(),
      store.loadImprovements(),
      store.loadHR(),
      store.loadFailures()
    ]);
    console.log('[Factory] Initial data loaded');
  } catch (error) {
    console.error('[Factory] Failed to load initial data:', error);
  }
}

// ═══════════════════════════════════════════════════════
// RENDER ALL
// ═══════════════════════════════════════════════════════

function renderAll() {
  store.update({ _refreshTrigger: Date.now() });
  renderKPIs();
  renderDashboardJournal();
  updateNavBadges();
}

function renderKPIs() {
  const container = document.getElementById('kpi-grid');
  if (!container) return;
  const { analytics } = store.state;
  if (!analytics) {
    container.innerHTML = '<div style="color:var(--text-muted);padding:20px">Загрузка KPI...</div>';
  }
}

function renderDashboardJournal() {
  const container = document.getElementById('dashboard-log-feed');
  if (!container) return;
  const { journal } = store.state;
  if (!journal || !journal.items || journal.items.length === 0) {
    container.innerHTML = '<div style="color:var(--text-muted);padding:20px;font-size:var(--text-xs)">Загрузка журнала...</div>';
    return;
  }
  const items = journal.items.slice(0, 5);
  container.innerHTML = items.map(item => `
    <div class="dashboard-log-row">
      <span class="log-time">${formatTime(item.event_time)}</span>
      <span class="log-msg">${escapeHtml(item.message || item.summary || '')}</span>
      <span class="badge s-${item.status || 'info'}">${item.event_type || ''}</span>
    </div>
  `).join('');
}

function updateNavBadges() {
  const { journal, workersStatus } = store.state;
  const badgeLog = document.getElementById('badge-log');
  if (badgeLog && journal?.items?.length) badgeLog.textContent = Math.min(journal.items.length, 99);
  const badgeForge = document.getElementById('badge-forge');
  if (badgeForge && workersStatus?.workers) badgeForge.textContent = workersStatus.workers.length;
}

// ═══════════════════════════════════════════════════════
// PAGE NAVIGATION
// ═══════════════════════════════════════════════════════

window.showPage = (pageName, btn) => {
  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  const page = document.getElementById(`page-${pageName}`);
  if (page) page.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
  if (btn) btn.classList.add('active');
  store.setActivePage(pageName);
  loadPageData(pageName);
};

window.goPage = (pageName) => {
  const btn = document.getElementById(`nav-${pageName}`);
  window.showPage(pageName, btn);
};

async function loadPageData(pageName) {
  switch (pageName) {
    case 'analytics': await store.loadAnalytics('24h'); break;
    case 'improvements': await store.loadImprovements(); break;
    case 'hr': await store.loadHR(); break;
    case 'failures': await store.loadFailures(); break;
  }
}

// ═══════════════════════════════════════════════════════
// ORCHESTRATOR CONTROLS
// ═══════════════════════════════════════════════════════

window.orchestratorStart = async () => {
  try {
    await store.orchestratorStart();
    showFactoryToast('Оркестратор запущен', 'ok');
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.orchestratorStop = async () => {
  try {
    await store.orchestratorStop();
    showFactoryToast('Оркестратор остановлен', 'ok');
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.orchestratorManualTick = async () => {
  try {
    await store.orchestratorTick();
    showFactoryToast('Tick выполнен', 'ok');
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.manualDashboardRefresh = async () => {
  await loadInitialData();
  renderAll();
  showFactoryToast('Данные обновлены', 'ok');
};

// ═══════════════════════════════════════════════════════
// MODAL WINDOWS
// ═══════════════════════════════════════════════════════

window.openVisionModal = () => {
  const modal = document.getElementById('vision-modal');
  if (modal) {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }
};

window.closeVisionModal = () => {
  const modal = document.getElementById('vision-modal');
  if (modal) {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }
};

window.submitNewVision = async () => {
  const titleEl = document.getElementById('vision-modal-title');
  const descEl = document.getElementById('vision-modal-desc');
  const title = titleEl?.value || '';
  const desc = descEl?.value || '';
  if (!title) { showFactoryToast('Введите заголовок Vision', 'err'); return; }
  try {
    const result = await api.createVision({ title, description: desc || null });
    showFactoryToast(`Vision создан: ${result.id}`, 'ok');
    window.closeVisionModal();
    await loadInitialData();
    renderAll();
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

window.openChildTaskModal = (parentId, childKind) => {
  console.log('openChildTaskModal', parentId, childKind);
  showFactoryToast('Создание задачи: ' + childKind, 'ok');
};

window.closeChildTaskModal = () => { console.log('closeChildTaskModal'); };
window.submitChildTaskModal = async () => { showFactoryToast('TODO: создать задачу', 'ok'); };

// ═══════════════════════════════════════════════════════
// DETAIL PANEL
// ═══════════════════════════════════════════════════════

window.openDetail = (workItem) => {
  console.log('openDetail', workItem);
  store.selectWorkItem(workItem?.id);
  showFactoryToast('Детали: ' + workItem?.title, 'ok');
};

window.closeDetail = () => {
  const panel = document.getElementById('detail-panel');
  if (panel) panel.classList.remove('open');
};

window.startDetailTitleEdit = () => { showFactoryToast('Редактирование', 'ok'); };
window.cancelDetailTitleEdit = () => { showFactoryToast('Отменено', 'ok'); };
window.saveDetailTitleEdit = async () => { showFactoryToast('Сохранено', 'ok'); };

window.workItemCancelFromDetail = async (wid) => {
  if (!confirm('Отменить задачу?')) return;
  try { await api.cancelWorkItem(wid); showFactoryToast('Отменено', 'ok'); await loadInitialData(); }
  catch (error) { showFactoryToast(`Ошибка: ${error.message}`, 'err'); }
};

window.workItemArchiveFromDetail = async (wid) => {
  try { await api.archiveWorkItem(wid); showFactoryToast('Архивировано', 'ok'); await loadInitialData(); }
  catch (error) { showFactoryToast(`Ошибка: ${error.message}`, 'err'); }
};

window.workItemDeleteFromDetail = async (wid) => {
  if (!confirm('Удалить безвозвратно?')) return;
  try { await api.deleteWorkItem(wid); showFactoryToast('Удалено', 'ok'); window.closeDetail(); await loadInitialData(); }
  catch (error) { showFactoryToast(`Ошибка: ${error.message}`, 'err'); }
};

// ═══════════════════════════════════════════════════════
// TREE HELPERS
// ═══════════════════════════════════════════════════════

window.expandAll = () => {
  document.querySelectorAll('.tree-children').forEach(el => el.classList.add('open'));
  document.querySelectorAll('.tree-toggle').forEach(el => el.classList.add('open'));
  showFactoryToast('Развёрнуто всё', 'ok');
};

window.collapseAll = () => {
  document.querySelectorAll('.tree-children').forEach(el => el.classList.remove('open'));
  document.querySelectorAll('.tree-toggle').forEach(el => el.classList.remove('open'));
  showFactoryToast('Свёрнуто', 'ok');
};

window.onTreeRowClick = (event, id) => {
  event.stopPropagation();
  store.selectWorkItem(id);
};

window.bulkArchiveDoneVisions = async () => {
  if (!confirm('Архивировать все Vision в статусе done?')) return;
  showFactoryToast('TODO: массовая архивация', 'ok');
};

// ═══════════════════════════════════════════════════════
// JOURNAL HELPERS
// ═══════════════════════════════════════════════════════

window.syncJournalRootFilter = () => { console.log('syncJournalRootFilter'); };
window.syncJournalManualFilters = () => { console.log('syncJournalManualFilters'); };
window.clearLogApiFilters = () => { console.log('clearLogApiFilters'); };
window.clearRouter = () => { showFactoryToast('Контекст сброшен', 'ok'); };
window.navigateLogPage = () => { console.log('navigateLogPage'); };

// ═══════════════════════════════════════════════════════
// ANALYTICS
// ═══════════════════════════════════════════════════════

window.setAnalyticsPeriod = (period, btn) => {
  document.querySelectorAll('.analytics-period-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  store.loadAnalytics(period);
};

// ═══════════════════════════════════════════════════════
// ORCHESTRATOR MENU
// ═══════════════════════════════════════════════════════

window.toggleOrchestratorMenu = (event) => {
  event?.stopPropagation?.();
  const menu = document.getElementById('orch-dropdown');
  if (menu) menu.classList.toggle('open');
};

window.closeOrchestratorMenu = () => {
  const menu = document.getElementById('orch-dropdown');
  if (menu) menu.classList.remove('open');
};

// ═══════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════

function showFactoryToast(message, kind = 'ok') {
  const el = document.getElementById('factory-toast');
  if (!el) return;
  el.textContent = message;
  el.className = 'factory-toast visible ' + (kind === 'err' ? 'err' : 'ok');
  clearTimeout(el._hideT);
  el._hideT = setTimeout(() => { el.classList.remove('visible'); }, 3000);
}

// ═══════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════

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

// ═══════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
  console.log('[Factory] Initializing...');
  initComponents();
  await loadInitialData();
  renderAll();
  
  setInterval(async () => {
    try {
      await Promise.all([store.loadJournal(), store.loadOrchestratorStatus(), store.loadWorkersStatus()]);
      renderAll();
    } catch (error) {
      console.error('[Factory] Polling error:', error);
    }
  }, 5000);
  
  console.log('[Factory] Initialized');
});

// ═══════════════════════════════════════════════════════
// CLEANUP
// ═══════════════════════════════════════════════════════

window.addEventListener('beforeunload', () => {
  Object.values(componentCleanup).forEach(cleanup => { if (typeof cleanup === 'function') cleanup(); });
});
