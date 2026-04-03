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
import { DetailPanelComponent } from './components/DetailPanel.js';
import { ChildTaskModalComponent } from './components/ChildTaskModal.js';
import { SidebarTreeComponent } from './components/SidebarTree.js';
import { AutonomousModeComponent } from './components/AutonomousMode.js';
import { VisionCreatorComponent } from './components/VisionCreator.js';
import { autoLaunchVision, stopAutoLaunch } from './autonomous/autoLaunch.js';
import { Storage, StorageKeys, initializeStorage } from './storage.js';
import { escapeHtml, formatTime, showFactoryToast, getStatusLabel, isEmpty, getSafe } from './utils/helpers.js';
import { initializeAccessibility } from './utils/accessibility.js';
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

  // Detail Panel
  const detailPanel = document.getElementById('detail-panel');
  if (detailPanel) componentCleanup.detail = DetailPanelComponent(detailPanel);

  // Child Task Modal
  const childTaskModal = document.getElementById('child-task-modal');
  if (childTaskModal) componentCleanup.childTask = ChildTaskModalComponent(childTaskModal);

  // Sidebar Quick Jump
  const sidebarTreeRoot = document.getElementById('sidebar-tree-root');
  if (sidebarTreeRoot) componentCleanup.sidebarTree = SidebarTreeComponent(sidebarTreeRoot);

  // Autonomous Mode
  const autonomousRoot = document.getElementById('autonomous-mode-root');
  if (autonomousRoot) componentCleanup.autonomous = AutonomousModeComponent(autonomousRoot);

  // Vision Creator
  const visionCreatorModal = document.getElementById('vision-creator-modal');
  if (visionCreatorModal) componentCleanup.visionCreator = VisionCreatorComponent(visionCreatorModal);

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
      store.loadRuns(20),
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
  renderKPIs();
  renderDashboardJournal();
  updateNavBadges();
  updateHeaderStatus();
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
  const { journal, workersStatus, workItems, improvements, failures, agents } = store.state;
  
  // Journal: количество новых событий
  const badgeLog = document.getElementById('badge-log');
  if (badgeLog && journal?.items?.length) {
    badgeLog.textContent = Math.min(journal.items.length, 99);
  }
  
  // Tree: количество задач
  const badgeTree = document.getElementById('badge-tree');
  if (badgeTree && workItems?.length) {
    badgeTree.textContent = Math.min(workItems.length, 99);
  }
  
  // Forge: количество активных workers
  const badgeForge = document.getElementById('badge-forge');
  if (badgeForge && workersStatus?.workers) {
    badgeForge.textContent = Math.min(workersStatus.workers.length, 99);
  }
  
  // Judge: количество ready_for_judge
  const badgeJudge = document.getElementById('badge-judge');
  if (badgeJudge && workItems) {
    const readyForJudge = workItems.filter(w => w.status === 'ready_for_judge').length;
    badgeJudge.textContent = Math.min(readyForJudge, 99);
  }
  
  // Failures: количество кластеров
  const badgeFail = document.getElementById('badge-fail');
  if (badgeFail && failures?.clusters?.length) {
    badgeFail.textContent = Math.min(failures.clusters.length, 99);
  }
  
  // Improvements: количество proposed
  const badgeImprovements = document.getElementById('badge-improvements');
  if (badgeImprovements && improvements?.candidates) {
    const proposed = improvements.candidates.filter(i => i.status === 'proposed').length;
    badgeImprovements.textContent = Math.min(proposed, 99);
  }
  
  // HR: количество proposals
  const badgeHr = document.getElementById('badge-hr');
  if (badgeHr && agents?.agents) {
    badgeHr.textContent = Math.min(agents.agents.length, 99);
  }
}

function updateHeaderStatus() {
  const { orchestrator, workersStatus, apiConnected } = store.state;
  // Статус оркестратора в хедере
  const orchStatus = document.getElementById('orch-status-text');
  if (orchStatus) {
    orchStatus.textContent = orchestrator?.running ? 'Running' : '—';
  }
  // Статус соединения
  const connDot = document.getElementById('live-dot-status');
  const connText = document.getElementById('live-status-text');
  if (connDot) {
    connDot.classList.toggle('offline', !apiConnected);
  }
  if (connText) {
    connText.textContent = apiConnected ? 'Live' : 'Offline';
    connText.style.color = apiConnected ? 'var(--success)' : 'var(--text-muted)';
  }
  // Счётчик активных агентов
  const agentsCount = document.getElementById('header-agent-summary');
  if (agentsCount && workersStatus?.workers) {
    agentsCount.textContent = workersStatus.workers.length + ' агентов активны';
  }
}

// ═══════════════════════════════════════════════════════
// CONNECTION STATUS
// ═══════════════════════════════════════════════════════

function setApiConnection(ok, detailMsg) {
  store.update({ apiConnected: ok, apiError: detailMsg || null });
  
  const banner = document.getElementById('connection-banner');
  if (banner) {
    if (ok) {
      banner.style.display = 'none';
      banner.textContent = '';
    } else {
      banner.style.display = 'block';
      banner.textContent = detailMsg || 'Нет связи с API — проверьте что сервер запущен';
    }
  }
  
  updateHeaderStatus();
}

window.setApiConnection = setApiConnection;

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
  // Используем компонент из ChildTaskModal.js
  const container = document.getElementById('child-task-modal');
  if (container && window.openChildTaskModalFromComponent) {
    window.openChildTaskModalFromComponent(parentId, childKind);
  }
};

// ═══════════════════════════════════════════════════════
// ROUTER CONTEXT (Task Tree -> Log + Forge + FSM filter)
// ═══════════════════════════════════════════════════════

window.selectWorkItemFromTree = async (id) => {
  // Выбрать work item и открыть detail panel
  store.selectWorkItem(id);
  updateRouterContextBars();
  renderTree();
  renderForge();
  renderFsm();
  renderLog();
};

window.clearRouter = () => {
  store.selectWorkItem(null);
  updateRouterContextBars();
  renderTree();
  renderForge();
  renderFsm();
  renderLog();
  showFactoryToast('Контекст сброшен', 'ok');
};

function updateRouterContextBars() {
  const selectedId = store.state.selectedWorkItemId;
  const wi = store.state.workItems?.find(w => w.id === selectedId);
  const title = wi ? (wi.title || wi.label || selectedId) : '—';
  
  const targets = [
    { wrap: 'router-context-log', title: 'router-context-log-title' },
    { wrap: 'router-context-fsm', title: 'router-context-fsm-title' },
    { wrap: 'router-context-forge', title: 'router-context-forge-title' }
  ];
  
  targets.forEach(t => {
    const wrapEl = document.getElementById(t.wrap);
    const titleEl = document.getElementById(t.title);
    if (wrapEl && titleEl) {
      if (selectedId) {
        titleEl.textContent = title;
        wrapEl.style.display = 'flex';
      } else {
        wrapEl.style.display = 'none';
      }
    }
  });
  
  // Обновляем chip в header если есть
  const chipEl = document.getElementById('router-chip');
  if (chipEl) {
    chipEl.textContent = selectedId ? `Router: ${selectedId.slice(0, 8)}...` : 'Router: —';
  }
}

function descendantIds(rootId) {
  const out = new Set();
  const walk = (id) => {
    out.add(id);
    const children = store.state.workItems?.filter(w => w.parent_id === id) || [];
    children.forEach(ch => walk(ch.id));
  };
  walk(rootId);
  return out;
}

function entityInRouterScope(entityId, scopeIds) {
  if (!scopeIds || entityId == null || entityId === '') return false;
  const eid = String(entityId);
  if (scopeIds.has(eid)) return true;
  
  // Проверяем run_id
  if (eid.startsWith('run_')) {
    // TODO: загрузить runs и проверить work_item_id
    return false;
  }
  
  return false;
}

// ═══════════════════════════════════════════════════════
// DETAIL PANEL
// ═══════════════════════════════════════════════════════

window.openDetail = (workItem) => {
  store.selectWorkItem(workItem?.id);
  updateRouterContextBars();
};

window.closeDetail = () => {
  store.selectWorkItem(null);
  updateRouterContextBars();
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
// TREE RUN BUTTON
// ═══════════════════════════════════════════════════════

window.runWorkItemFromTree = async (wiId) => {
  const base = window.FACTORY_API_BASE || document.documentElement.getAttribute('data-api-base') || 'http://127.0.0.1:8000';
  if (!base) {
    showFactoryToast('Нет API base', 'err');
    return;
  }
  
  try {
    const response = await fetch(base + '/api/work-items/' + encodeURIComponent(wiId) + '/run', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(window.FACTORY_API_KEY ? { 'X-API-Key': window.FACTORY_API_KEY } : {})
      },
      body: '{}'
    });
    
    const data = await response.json().catch(() => ({}));
    
    if (response.status === 409) {
      showFactoryToast(data.error || 'Уже есть активный forge-run', 'err');
      return;
    }
    
    if (!response.ok || data.ok === false) {
      let err = data.error || ('HTTP ' + response.status);
      const d = data.detail;
      if (d) {
        if (typeof d === 'string') err = d;
        else if (typeof d === 'object' && d.error) err = d.error;
      }
      throw new Error(err);
    }
    
    showFactoryToast('Forge запущен' + (data.run_id ? ' · ' + data.run_id : ''), 'ok');
    
    // Refresh данных
    await loadInitialData();
    
    // Обновить detail panel если открыта
    if (store.state.selectedWorkItemId === wiId && window.openDetail) {
      const wi = store.state.workItems?.find(w => w.id === wiId);
      if (wi) window.openDetail(wi);
    }
    
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

// ═══════════════════════════════════════════════════════
// ASK QWEN ABOUT ENTITY
// ═══════════════════════════════════════════════════════

window.askQwenAboutEntity = async (type, id) => {
  // Установить контекст в store
  store.setChatContext(type, id);
  
  // Открыть чат
  store.openChat(type === 'work_item' ? id : null);
  
  // Показать toast
  const entity = type === 'work_item' 
    ? store.state.workItems?.find(w => w.id === id)
    : store.state.runs?.find(r => r.id === id);
  
  const title = entity?.title || entity?.id?.slice(0, 8) || id.slice(0, 8);
  showFactoryToast(`Контекст: ${type} ${title}...`, 'ok');
};

window.clearChatContext = () => {
  store.clearChatContext();
  showFactoryToast('Контекст очищен', 'ok');
  // Перерендерить чат
  const chatPanel = document.getElementById('chat-panel');
  if (chatPanel) {
    // Триггерим re-render через store update
    store.update({ _forceRender: true });
  }
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
  const base = window.FACTORY_API_BASE || document.documentElement.getAttribute('data-api-base') || 'http://127.0.0.1:8000';
  if (!base) {
    showFactoryToast('Нет API base', 'err');
    return;
  }
  
  // Считаем Vision в статусе done
  const visions = store.state.visions?.visions || store.state.visions || [];
  const doneVisions = visions.filter(v => v.status === 'done');
  
  if (doneVisions.length === 0) {
    showFactoryToast('Нет Vision в статусе done', 'ok');
    return;
  }
  
  if (!confirm(`Архивировать ${doneVisions.length} Vision в статусе done?`)) return;
  
  try {
    const response = await fetch(base + '/api/bulk/archive', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(window.FACTORY_API_KEY ? { 'X-API-Key': window.FACTORY_API_KEY } : {})
      },
      body: JSON.stringify({ filter: 'all_done_visions' })
    });
    
    const result = await response.json().catch(() => ({}));
    
    if (!response.ok) {
      throw new Error(result.error || ('HTTP ' + response.status));
    }
    
    const archivedCount = result.archived_count || doneVisions.length;
    showFactoryToast(`Архивировано ${archivedCount} Vision`, 'ok');
    
    // Refresh данных
    await loadInitialData();
    
  } catch (error) {
    showFactoryToast(`Ошибка: ${error.message}`, 'err');
  }
};

// ═══════════════════════════════════════════════════════
// JOURNAL HELPERS
// ═══════════════════════════════════════════════════════

window.syncJournalRootFilter = () => {
  const rootSelect = document.getElementById('journal-filter-root');
  const rootId = rootSelect?.value || '';
  
  if (rootId) {
    // Фильтр по поддереву Vision
    store.selectWorkItem(rootId);
    window.goPage('tree');
    showFactoryToast(`Фильтр по Vision: ${rootId.slice(0, 8)}...`, 'ok');
  } else {
    // Сброс фильтра
    window.clearRouter();
  }
};

window.syncJournalManualFilters = () => {
  const wiFilter = document.getElementById('journal-filter-wi')?.value || '';
  const runFilter = document.getElementById('journal-filter-run')?.value || '';
  const kindFilter = document.getElementById('journal-filter-kind')?.value || '';
  const roleFilter = document.getElementById('journal-filter-role')?.value || '';
  
  // Обновляем фильтры в Journal component
  if (window.setJournalFilter) {
    if (wiFilter) window.setJournalFilter('work_item_id', wiFilter);
    if (runFilter) window.setJournalFilter('run_id', runFilter);
    if (kindFilter) window.setJournalFilter('kind', kindFilter);
    if (roleFilter) window.setJournalFilter('role', roleFilter);
  }
  
  showFactoryToast('Фильтры обновлены', 'ok');
};

window.clearLogApiFilters = () => {
  // Сброс всех фильтров
  const wiFilter = document.getElementById('journal-filter-wi');
  const runFilter = document.getElementById('journal-filter-run');
  const kindFilter = document.getElementById('journal-filter-kind');
  const roleFilter = document.getElementById('journal-filter-role');
  const searchFilter = document.getElementById('log-search');
  
  if (wiFilter) wiFilter.value = '';
  if (runFilter) runFilter.value = '';
  if (kindFilter) kindFilter.value = '';
  if (roleFilter) roleFilter.value = '';
  if (searchFilter) searchFilter.value = '';
  
  // Сброс severity
  const allSevBtn = document.querySelector('.log-sev-btn[data-sev="all"]');
  if (allSevBtn && window.setSevFilter) {
    window.setSevFilter(allSevBtn, 'all');
  }
  
  // Сброс router контекста
  window.clearRouter();
  
  showFactoryToast('Все фильтры сброшены', 'ok');
};

window.navigateToWorkItem = (id) => {
  store.selectWorkItem(id);
  window.goPage('tree');
  showFactoryToast(`Переход к ${id.slice(0, 8)}...`, 'ok');
};

window.navigateLogPage = () => {
  window.goPage('log');
  showFactoryToast('Переход к журналу', 'ok');
};

window.showResultView = (visionId) => {
  const vision = store.state.visions?.find(v => v.id === visionId);
  if (vision) {
    store.selectWorkItem(visionId);
    // Переключаемся на autonomous mode и показываем ResultView
    window.switchToAutonomousMode();
  }
};

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

// showFactoryToast импортируется из utils/helpers.js
// Глобальный экспорт:
window.showFactoryToast = showFactoryToast;

window.openVisionCreator = () => {
  // TODO: открыть Vision Creator modal
  showFactoryToast('Vision Creator в разработке', 'ok');
};

window.switchToAutonomousMode = () => {
  store.update({ activePage: 'autonomous' });
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-autonomous')?.classList.add('active');
  showFactoryToast('Автономный режим включён', 'ok');
};

// Инициализация factoryPaused
window.factoryPaused = false;

// ═══════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════

// Helpers импортируются из utils/helpers.js
// escapeHtml, formatTime, showFactoryToast

// ═══════════════════════════════════════════════════════
// ERROR BOUNDARIES
// ═══════════════════════════════════════════════════════

/**
 * Безопасный рендеринг с обработкой ошибок
 * @param {Function} fn - функция рендеринга
 * @param {string} fallbackMessage - сообщение об ошибке
 * @returns {string} HTML или fallback
 */
function safeRender(fn, fallbackMessage = 'Ошибка рендеринга') {
  try {
    return fn();
  } catch (error) {
    console.error('[Render Error]', error);
    return `
      <div style="padding:20px;color:var(--error);background:var(--error-dim);border:1px solid var(--error);border-radius:var(--radius-md)">
        <div style="font-weight:600;margin-bottom:8px">⚠️ ${escapeHtml(fallbackMessage)}</div>
        <pre style="margin:0;padding:10px;background:var(--surface-2);border-radius:var(--radius-sm);font-size:10px;white-space:pre-wrap;overflow:auto;max-height:200px;color:var(--text-muted)">${escapeHtml(error.message)}</pre>
        <button type="button" onclick="window.location.reload()" style="margin-top:12px;padding:6px 12px;background:var(--error);color:white;border:none;border-radius:var(--radius-sm);cursor:pointer;font-size:12px">Перезагрузить</button>
      </div>
    `;
  }
}

/**
 * Error Boundary для компонентов
 * @param {Function} Component - Компонент
 * @param {string} name - Имя компонента
 * @returns {Function} Обёрнутый компонент
 */
function withErrorBoundary(Component, name) {
  return function(...args) {
    try {
      return Component(...args);
    } catch (error) {
      console.error(`[${name}] Error:`, error);
      return `<div style="padding:20px;color:var(--error)">⚠️ Ошибка в ${name}</div>`;
    }
  };
}

// ═══════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
  console.log('[Factory] Initializing...');
  
  // Инициализация accessibility
  initializeAccessibility();
  
  // Инициализация хранилища
  initializeStorage();
  
  // Загрузка предпочтений
  const mode = Storage.get(StorageKeys.MODE, 'autonomous');
  const paused = Storage.get(StorageKeys.PAUSED, false);
  window.factoryPaused = paused;
  
  initComponents();
  await loadInitialData();
  renderAll();
  
  // Переключение в сохранённый режим
  if (mode === 'developer') {
    window.switchToDeveloperMode();
  } else {
    window.switchToAutonomousMode();
  }

  // ═══════════════════════════════════════════════════════
  // KEYBOARD SHORTCUTS
  // ═══════════════════════════════════════════════════════
  
  document.addEventListener('keydown', (e) => {
    // Ctrl+K → фокус на поиск журнала
    if (e.ctrlKey && e.key === 'k') {
      e.preventDefault();
      const searchInput = document.getElementById('log-search');
      if (searchInput) {
        searchInput.focus();
        showFactoryToast('Фокус на поиск журнала', 'ok');
      }
    }
    
    // Escape → закрыть modal/panel/chat
    if (e.key === 'Escape') {
      window.closeDetail?.();
      window.closeJournalDetail?.();
      window.closeVisionModal?.();
      window.closeChildTaskModal?.();
      window.closeVisionCreator?.();
      store.closeChat?.();
      
      const detailPanel = document.getElementById('detail-panel');
      if (detailPanel && detailPanel.classList.contains('open')) {
        detailPanel.classList.remove('open');
        store.selectWorkItem(null);
      }
    }
    
    // ? → показать shortcuts help (если не в input)
    if (e.key === '?' && !e.ctrlKey && !e.metaKey && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
      e.preventDefault();
      showShortcutsHelp();
    }
    
    // Ctrl+Enter → отправить форму (Vision, Child Task)
    if (e.ctrlKey && e.key === 'Enter') {
      const visionModal = document.getElementById('vision-modal');
      const childModal = document.getElementById('child-task-modal');
      
      if (visionModal && visionModal.classList.contains('open')) {
        window.submitNewVision?.();
      } else if (childModal && childModal.classList.contains('open')) {
        window.submitChildTaskModal?.();
      }
    }
  });
  
  // Polling
  setInterval(async () => {
    try {
      await Promise.all([store.loadJournal(), store.loadOrchestratorStatus(), store.loadWorkersStatus()]);
      // НЕ вызываем renderAll() — только точечные DOM-апдейты
      updateNavBadges();
      updateHeaderStatus();
    } catch (error) {
      console.error('[Factory] Polling error:', error);
    }
  }, 5000);

  console.log('[Factory] Initialized');
});

// ═══════════════════════════════════════════════════════
// SHORTCUTS HELP
// ═══════════════════════════════════════════════════════

function showShortcutsHelp() {
  const helpHtml = `
    <div style="position:fixed;inset:0;z-index:400;display:flex;align-items:center;justify-content:center">
      <div class="vision-modal-backdrop" onclick="this.parentElement.remove()"></div>
      <div class="vision-modal-card card" style="padding:var(--space-4);max-width:420px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
          <div style="font-weight:600;font-size:var(--text-base)">⌨️ Горячие клавиши</div>
          <button type="button" onclick="this.closest('.vision-modal-wrap')?.remove()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:18px">×</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-2);font-size:var(--text-sm)">
          <div style="display:flex;align-items:center;gap:8px"><kbd style="padding:2px 6px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:var(--font-mono);font-size:10px">Ctrl+K</kbd> Поиск</div>
          <div style="display:flex;align-items:center;gap:8px"><kbd style="padding:2px 6px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:var(--font-mono);font-size:10px">Escape</kbd> Закрыть</div>
          <div style="display:flex;align-items:center;gap:8px"><kbd style="padding:2px 6px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:var(--font-mono);font-size:10px">?</kbd> Помощь</div>
          <div style="display:flex;align-items:center;gap:8px"><kbd style="padding:2px 6px;background:var(--surface-3);border:1px solid var(--border);border-radius:var(--radius-sm);font-family:var(--font-mono);font-size:10px">Ctrl+↵</kbd> Отправить</div>
        </div>
        <div style="margin-top:var(--space-3);padding-top:var(--space-3);border-top:1px solid var(--border);font-size:10px;color:var(--text-faint)">
          Нажмите <kbd style="font-family:var(--font-mono)">Escape</kbd> или кликните вне окна для закрытия
        </div>
      </div>
    </div>
  `;
  
  // Создаём временный контейнер
  const container = document.createElement('div');
  container.innerHTML = helpHtml;
  document.body.appendChild(container);
}

// ═══════════════════════════════════════════════════════
// CLEANUP
// ═══════════════════════════════════════════════════════

window.addEventListener('beforeunload', () => {
  Object.values(componentCleanup).forEach(cleanup => { if (typeof cleanup === 'function') cleanup(); });
});
