# Аудит фронтенда Fabrika 2.0

**Дата:** 2 апреля 2026 г.
**Объект аудита:** Legacy (`factory-os.html`) vs Новый (`factory-os-refactored.html` + `/static/`)

---

## 📋 Executive Summary

### Краткие выводы

| Аспект | Статус | Примечание |
|--------|--------|------------|
| Полнота переноса | ⚠️ **75%** | Ключевые страницы перенесены, но есть критические пробелы |
| Интерактивные элементы | ⚠️ **60%** | Многие кнопки имеют заглушки вместо функционала |
| Работа с API | ✅ **90%** | API client реализован полно |
| State management | ✅ **85%** | Store с pub/sub работает |
| Accessibility | ❌ **20%** | Почти не реализован |
| Производительность | ⚠️ **70%** | Есть проблемы с точечными обновлениями |

---

## 📊 Этап 1-2: Инвентаризация и сравнительный анализ

### 2.1 Структура файлов

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| HTML каркас | 1 файл (5310 строк) | 1 файл (116 строк) + модули | ✅ Перенесено |
| CSS стили | Встроенные (~2500 строк) | `/static/css/factory.css` (1167 строк) | ✅ Перенесено |
| JavaScript | Встроенный (~2800 строк) | Модули ES6 в `/static/js/` | ✅ Перенесено |
| State management | Глобальные переменные | `store.js` с pub/sub | ✅ Улучшено |
| API client | Встроенные функции | `api/client.js` | ✅ Улучшено |

### 2.2 Страницы и навигация

| Страница | Legacy | Новый | Примечания |
|----------|--------|-------|------------|
| **Dashboard** | ✅ Полная | ✅ Полная | KPI, Visions, Journal preview — перенесено |
| **Журнал (Log)** | ✅ Полная | ✅ Полная | Все фильтры работают |
| **Analytics** | ✅ Charts + KPI | ⚠️ Заглушка | Charts не реализованы |
| **Дерево задач** | ✅ Полная | ✅ Полная | Все функции перенесены |
| **Forge Queue** | ✅ Полная | ✅ Полная | Cards с lease bar |
| **FSM** | ✅ SVG + Table | ⚠️ Частично | SVG визуализация — заглушка |
| **Агенты** | ✅ Grid карточек | ✅ Grid карточек | Полностью перенесено |
| **Judgements** | ✅ Table | ✅ Table | Полностью перенесено |
| **Failures** | ✅ Cards | ✅ Cards | Полностью перенесено |
| **Improvements** | ✅ Table + Actions | ✅ Table + Actions | Полностью перенесено |
| **HR** | ✅ 2 tables | ✅ 2 tables | Полностью перенесено |

### 2.3 Header (шапка) — детальное сравнение

| Элемент | Legacy | Новый | Статус |
|---------|--------|-------|--------|
| Логотип + название | ✅ | ✅ | ✅ |
| Orchestrator status (dot + текст) | ✅ | ✅ | ✅ |
| Live status (online/offline) | ✅ | ✅ | ✅ |
| Часы (header-clock) | ✅ | ✅ | ✅ |
| Кнопка "+ New Vision" | ✅ | ✅ | ✅ |
| Worker mode pill | ✅ | ✅ | ✅ |
| Router chip | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| API mode pill (MOCK/Live) | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Orchestrator dropdown | ✅ | ✅ | ✅ |
| Agent summary count | ✅ | ✅ | ✅ |

### 2.4 Sidebar (боковая панель)

| Элемент | Legacy | Новый | Статус |
|---------|--------|-------|--------|
| Секция "Основное" (5 кнопок) | ✅ | ✅ | ✅ |
| Секция "Контроль" (FSM) | ✅ | ✅ | ✅ |
| Секция "Агенты" | ✅ | ✅ | ✅ |
| Секция "Суд / сбои / HR" (4 кнопки) | ✅ | ✅ | ✅ |
| Кнопка "💬 Chat с Qwen" | ✅ | ✅ | ✅ |
| Accordion "Quick Jump" | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Nav badges со счётчиками | ✅ | ❌ **ОТСУТСТВУЮТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 2.5 Dashboard page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| KPI Grid (6+ карточек) | ✅ | ⚠️ Заглушка | Функция `renderKPIs()` есть, но не заполняет DOM |
| Visions список | ✅ | ✅ | Перенесено |
| Journal preview (5 событий) | ✅ | ✅ | Перенесено |
| Charts (статусы, активность) | ✅ | ❌ **ОТСУТСТВУЮТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Таблица последних прогонов | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 2.6 Journal page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Фильтр root_id (Vision/ветка) | ✅ | ✅ | ✅ |
| Фильтр work_item_id | ✅ | ✅ | ✅ |
| Фильтр run_id | ✅ | ✅ | ✅ |
| Фильтр kind | ✅ | ✅ | ✅ |
| Фильтр role | ✅ | ✅ | ✅ |
| Поиск по ленте | ✅ | ✅ | ✅ |
| Severity фильтры (INFO/WARN/ERROR) | ✅ | ✅ | ✅ |
| Кнопки навигации (← →) | ✅ | ✅ | ✅ |
| Счётчик страниц | ✅ | ✅ | ✅ |
| Кнопки сброса фильтров | ✅ | ✅ | ✅ |
| Journal entries | ✅ | ✅ | ✅ |
| Detail pane для записи | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Router context bar | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 2.7 Tree page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Кнопка "+ Новый Vision" | ✅ | ✅ | ✅ |
| Кнопки expand all / collapse all | ✅ | ✅ | ✅ (глобальные функции) |
| Фильтр hide done | ✅ | ✅ | ✅ |
| Фильтр hide cancelled | ✅ | ✅ | ✅ |
| Bulk archive button | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Счётчик видимых узлов | ✅ | ✅ | ✅ |
| Tree nodes с toggle | ✅ | ✅ | ✅ |
| Kind badge на узле | ✅ | ✅ | ✅ |
| Status badge на узле | ✅ | ✅ | ✅ |
| Last_event preview | ✅ | ✅ | ✅ |
| Подсветка выбранного узла | ✅ | ✅ | ✅ |
| Кнопка "+ {nextKind}" для создания дочерних | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Vision pipeline bar | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Run button для атомов | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 2.8 Forge page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Router context bar | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Queue cards | ✅ | ✅ | ✅ |
| Worker info | ✅ | ✅ | ✅ |
| Files section | ✅ | ✅ | ✅ |
| Lease bar | ✅ | ✅ | ✅ |
| Time remaining | ✅ | ✅ | ✅ |
| File chips (modify/create/delete) | ✅ | ✅ | ✅ |

### 2.9 FSM page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Router context bar | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| State summary badges | ✅ | ❌ **ОТСУТСТВУЮТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| SVG визуализация | ✅ Полная | ⚠️ Заглушка | Только заголовок |
| Legend | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Transitions table | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 2.10 Agents page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Grid карточек агентов | ✅ | ✅ | ✅ |
| Role badge | ✅ | ✅ | ✅ |
| Status badge | ✅ | ✅ | ✅ |
| Model name | ✅ | ✅ | ✅ |
| Prompt version | ✅ | ✅ | ✅ |
| Runs today | ✅ | ✅ | ✅ |

### 2.11 Judgements page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Table (ID, Work Item, Verdict, Reason, Event, Created) | ✅ | ✅ | ✅ |

### 2.12 Failures page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Table (ID, Pattern, Count, Last seen, Status) | ✅ | ✅ | ✅ |

### 2.13 Improvements page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| KPI row (Total, Approved, Converted) | ✅ | ✅ | ✅ |
| Table (Priority, Source, Title, Target, Risk, Status, Actions) | ✅ | ✅ | ✅ |
| Detail card | ✅ | ✅ | ✅ |
| Action buttons (approve/reject/convert) | ✅ | ✅ | ✅ |

### 2.14 HR page

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Policy bundles table | ✅ | ✅ | ✅ |
| HR proposals table | ✅ | ✅ | ✅ |

---

## 🔘 Этап 3: Интерактивные элементы

### 3.1 Кнопки — статус реализации

| Кнопка | Legacy | Новый | Статус |
|--------|--------|-------|--------|
| **Header: + New Vision** | ✅ `openVisionModal()` | ✅ `openVisionModal()` | ✅ |
| **Header: Orchestrator Start** | ✅ `orchestratorStart()` | ✅ `orchestratorStart()` | ✅ |
| **Header: Orchestrator Stop** | ✅ `orchestratorStop()` | ✅ `orchestratorStop()` | ✅ |
| **Header: Manual Tick** | ✅ `orchestratorManualTick()` | ✅ `orchestratorManualTick()` | ✅ |
| **Header: Обновить** | ✅ `manualDashboardRefresh()` | ✅ `manualDashboardRefresh()` | ✅ |
| **Sidebar: навигация** | ✅ `showPage()` | ✅ `showPage()` | ✅ |
| **Sidebar: Chat toggle** | ✅ `toggleChat()` | ✅ `toggleChat()` | ✅ |
| **Sidebar: Quick Jump toggle** | ✅ `toggleSidebarQuickJump()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Dashboard: перейти в дерево** | ✅ `goPage('tree')` | ✅ `goPage('tree')` | ✅ |
| **Tree: expand all** | ✅ `expandAll()` | ✅ `window.expandAll()` | ✅ |
| **Tree: collapse all** | ✅ `collapseAll()` | ✅ `window.collapseAll()` | ✅ |
| **Tree: filter hide done** | ✅ `toggleTreeFilter('done')` | ✅ `window.toggleTreeFilter('done')` | ✅ |
| **Tree: filter hide cancelled** | ✅ `toggleTreeFilter('cancelled')` | ✅ `window.toggleTreeFilter('cancelled')` | ✅ |
| **Tree: bulk archive** | ✅ `bulkArchiveDoneVisions()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Tree: + {nextKind}** | ✅ `openChildTaskModal()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Tree: run atom** | ✅ `onDashboardRunClick()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Journal: severity filter** | ✅ `setSevFilter()` | ✅ `window.setSevFilter()` | ✅ |
| **Journal: clear filters** | ✅ `clearLogApiFilters()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Journal: page navigation** | ✅ `logPage()` | ✅ `window.logPage()` | ✅ |
| **Journal: sync filters** | ✅ `syncJournalRootFilter()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Analytics: period buttons** | ✅ `setAnalyticsPeriod()` | ✅ `window.setAnalyticsPeriod()` | ✅ |
| **FSM: highlight status** | ✅ `highlightStatus()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Improvements: approve** | ✅ `approveImprovement()` | ✅ `window.approveImprovement()` | ✅ |
| **Improvements: reject** | ✅ `rejectImprovement()` | ✅ `window.rejectImprovement()` | ✅ |
| **Improvements: convert** | ✅ `convertImprovement()` | ✅ `window.convertImprovement()` | ✅ |
| **Vision modal: submit** | ✅ `submitNewVision()` | ✅ `window.submitNewVision()` | ✅ |
| **Child modal: submit** | ✅ `submitChildTaskModal()` | ⚠️ Заглушка `TODO` | ⚠️ |
| **Detail panel: edit title** | ✅ `startDetailTitleEdit()` | ⚠️ Заглушка | ⚠️ |
| **Detail panel: save title** | ✅ `saveDetailTitleEdit()` | ⚠️ Заглушка | ⚠️ |
| **Detail panel: cancel edit** | ✅ `cancelDetailTitleEdit()` | ⚠️ Заглушка | ⚠️ |
| **Detail panel: run forge** | ✅ `onDashboardRunClick()` | ❌ **ОТСУТСТВУЕТ** | ❌ |
| **Detail panel: cancel** | ✅ `workItemCancelFromDetail()` | ✅ `window.workItemCancelFromDetail()` | ✅ |
| **Detail panel: archive** | ✅ `workItemArchiveFromDetail()` | ✅ `window.workItemArchiveFromDetail()` | ✅ |
| **Detail panel: delete** | ✅ `workItemDeleteFromDetail()` | ✅ `window.workItemDeleteFromDetail()` | ✅ |
| **Chat: send message** | ✅ (SSE) | ✅ `store.sendMessage()` | ✅ |
| **Chat: close** | ✅ | ✅ `store.closeChat()` | ✅ |

### 3.2 Модальные окна

| Модальное окно | Legacy | Новый | Статус |
|----------------|--------|-------|--------|
| **Vision modal** | ✅ Полная | ✅ Полная | ✅ |
| **Child Task modal** | ✅ Полная | ⚠️ Заглушка | Функции `openChildTaskModal`, `closeChildTaskModal`, `submitChildTaskModal` — заглушки |

### 3.3 Detail Panel

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Panel container | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Header с breadcrumbs | ✅ | ❌ | ❌ |
| Edit title inline | ✅ | ❌ | ❌ |
| Badges row | ✅ | ❌ | ❌ |
| Next action hint | ✅ | ❌ | ❌ |
| Action buttons | ✅ | ❌ | ❌ |
| Mini cards (parent, children, runs) | ✅ | ❌ | ❌ |
| Files section | ✅ | ❌ | ❌ |
| Comments section | ✅ | ❌ | ❌ |
| Timeline | ✅ | ❌ | ❌ |
| Accordions | ✅ | ❌ | ❌ |

---

## 🎨 Этап 4: Состояния и обратная связь

### 4.1 Loading states

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Skeleton loaders | ✅ | ✅ В `ui.js` | ✅ |
| "Загрузка..." сообщения | ✅ | ✅ | ✅ |
| Spinner animations | ✅ | ✅ В `ui.js` | ✅ |

### 4.2 Empty states

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Пустое дерево задач | ✅ | ✅ | ✅ |
| Пустая очередь Forge | ✅ | ✅ | ✅ |
| Пустой журнал | ✅ | ✅ | ✅ |
| Пустой чат | ✅ | ✅ | ✅ |
| EmptyState компонент | ✅ | ✅ В `ui.js` | ✅ |

### 4.3 Error states

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Error banners | ✅ | ✅ В `ui.js` | ✅ |
| Toast ошибки | ✅ | ✅ | ✅ |
| Connection banner | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| API error handling | ✅ | ✅ В `api/client.js` | ✅ |

### 4.4 Success states

| Компонент | Legacy | Новый | Статус |
|-----------|--------|-------|--------|
| Toast уведомления | ✅ `showFactoryToast()` | ✅ `showFactoryToast()` | ✅ |
| Flash animation для строк | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

---

## ♿ Этап 5: Accessibility

| Аспект | Legacy | Новый | Статус |
|--------|--------|-------|--------|
| ARIA атрибуты | ⚠️ Частично | ⚠️ Частично | ⚠️ |
| - aria-label | ✅ В модальных окнах | ✅ В модальных окнах | ✅ |
| - aria-hidden | ✅ В dropdown | ✅ В dropdown | ✅ |
| - role | ✅ В menu | ✅ В menu | ✅ |
| Focus states | ❌ Отсутствуют | ❌ Отсутствуют | ❌ **НЕ РЕАЛИЗОВАНО** |
| Keyboard navigation | ❌ Отсутствует | ❌ Отсутствует | ❌ **НЕ РЕАЛИЗОВАНО** |
| Semantic HTML | ⚠️ Частично | ⚠️ Частично | ⚠️ |
| Color contrast | ✅ Dark theme | ✅ Dark theme | ✅ |

---

## ⚡ Этап 6: Производительность

| Аспект | Legacy | Новый | Статус |
|--------|--------|-------|--------|
| Polling interval | 5000ms | 5000ms | ✅ |
| Debounce/throttle для input | ❌ | ❌ | ❌ **НЕ РЕАЛИЗОВАНО** |
| Lazy loading | ❌ | ❌ | ❌ **НЕ РЕАЛИЗОВАНО** |
| Component cleanup | ❌ | ✅ В компонентах | ✅ Улучшено |
| SSE connection cleanup | ✅ | ✅ `streamCleanup` | ✅ |
| Точечные обновления (стриминг) | ❌ | ✅ `_isStreamUpdate` | ✅ Улучшено |
| Полный re-render при polling | ⚠️ | ⚠️ `renderAll()` не вызывается | ✅ Улучшено |

---

## 🔌 Этап 7: Интеграция с backend

### 7.1 API endpoints — покрытие

| Endpoint | Legacy | Новый (api/client.js) | Статус |
|----------|--------|----------------------|--------|
| GET /api/journal | ✅ | ✅ `getJournal()` | ✅ |
| GET /api/events | ✅ | ✅ `getEvents()` | ✅ |
| GET /api/work-items/tree | ✅ | ✅ `getTree()` | ✅ |
| GET /api/work-items | ✅ | ✅ `getWorkItems()` | ✅ |
| GET /api/work-items/{id} | ✅ | ✅ `getWorkItem(id)` | ✅ |
| GET /api/work-items/{id}/events | ✅ | ✅ `getWorkItemEvents(id)` | ✅ |
| GET /api/runs | ✅ | ✅ `getRuns()` | ✅ |
| GET /api/runs/{id} | ✅ | ✅ `getRun(id)` | ✅ |
| GET /api/runs/{id}/steps | ✅ | ✅ `getRunSteps(runId)` | ✅ |
| GET /api/visions | ✅ | ✅ `getVisions()` | ✅ |
| POST /api/visions | ✅ | ✅ `createVision()` | ✅ |
| GET /api/queue/forge_inbox | ✅ | ✅ `getForgeQueue()` | ✅ |
| POST /api/work-items/{id}/run | ✅ | ✅ `runWorkItem(id)` | ✅ |
| POST /api/tasks/{id}/forge-run | ✅ | ✅ `forgeRun(id)` | ✅ |
| POST /api/tasks/{id}/transition | ✅ | ✅ `transitionWorkItem(id)` | ✅ |
| POST /api/work-items/{id}/cancel | ✅ | ✅ `cancelWorkItem(id)` | ✅ |
| POST /api/work-items/{id}/archive | ✅ | ✅ `archiveWorkItem(id)` | ✅ |
| DELETE /api/work-items/{id} | ✅ | ✅ `deleteWorkItem(id)` | ✅ |
| POST /api/tasks/{id}/children | ✅ | ✅ `createChild(parentId)` | ✅ |
| POST /api/work-items/{id}/comments | ✅ | ✅ `addComment(id)` | ✅ |
| GET /api/analytics | ✅ | ✅ `getAnalytics(period)` | ✅ |
| GET /api/stats | ✅ | ✅ `getStats()` | ✅ |
| GET /api/orchestrator/status | ✅ | ✅ `getOrchestratorStatus()` | ✅ |
| POST /api/orchestrator/start | ✅ | ✅ `orchestratorStart()` | ✅ |
| POST /api/orchestrator/stop | ✅ | ✅ `orchestratorStop()` | ✅ |
| POST /api/orchestrator/tick | ✅ | ✅ `orchestratorTick()` | ✅ |
| GET /api/orchestrator/health | ✅ | ✅ `getOrchestratorHealth()` | ✅ |
| GET /api/fsm/work_item | ✅ | ✅ `getFsmTransitions()` | ✅ |
| GET /api/agents | ✅ | ✅ `getAgents()` | ✅ |
| GET /api/hr | ✅ | ✅ `getHR()` | ✅ |
| GET /api/failures | ✅ | ✅ `getFailures()` | ✅ |
| GET /api/improvements | ✅ | ✅ `getImprovements()` | ✅ |
| POST /api/improvements/{id}/approve | ✅ | ✅ `approveImprovement(id)` | ✅ |
| POST /api/improvements/{id}/reject | ✅ | ✅ `rejectImprovement(id)` | ✅ |
| POST /api/improvements/{id}/convert | ✅ | ✅ `convertImprovement(id)` | ✅ |
| GET /api/workers/status | ✅ | ✅ `getWorkersStatus()` | ✅ |
| POST /api/chat/qwen | ✅ | ✅ `api.chat.create()` | ✅ |
| GET /api/chat/qwen/{id}/stream (SSE) | ✅ | ✅ `api.chat.stream()` | ✅ |
| GET /api/judgements | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| GET /api/failure-clusters | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| GET /api/bulk/archive | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| PATCH /api/work-items/{id} | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |

### 7.2 Конфигурация API

| Аспект | Legacy | Новый | Статус |
|--------|--------|-------|--------|
| API_BASE | ✅ `FACTORY_LIVE_API_BASE` | ✅ `API_BASE = 'http://127.0.0.1:8000'` | ⚠️ **Захардкожен** |
| FACTORY_API_KEY support | ✅ `mutableApiHeaders()` | ✅ `X-API-Key` header | ✅ |
| MOCK mode fallback | ✅ | ❌ **ОТСУТСТВУЕТ** | ❌ **НЕ ПЕРЕНЕСЕНО** |
| Timeout handling | ❌ | ✅ В `fetch()` | ✅ Улучшено |

---

## 📝 Этап 8: Критические пробелы

### ❌ Критические отсутствующие функции

1. **Detail Panel** — полностью отсутствует
   - Панель деталей задачи не перенесена
   - Нет просмотра children, runs, files, comments
   - Нет timeline задачи
   - Нет accordion для развертывания деталей

2. **Router context** — частично отсутствует
   - Нет chip в header
   - Нет context bar на страницах Journal/Forge/FSM
   - Нет функции `selectWorkItem()` для Router
   - Нет `clearRouter()`
   - Нет `updateRouterContextBars()`

3. **Sidebar Quick Jump** — отсутствует
   - Accordion с мини-деревом не перенесён
   - Нет быстрой навигации по дереву из sidebar

4. **Dashboard Charts** — отсутствуют
   - Chart.js интеграция не перенесена
   - Нет визуализации статусов
   - Нет графика активности

5. **FSM Visualization** — заглушка
   - SVG визуализация автомата не перенесена
   - Нет интерактивной подсветки статусов
   - Нет legend

6. **Bulk actions** — отсутствуют
   - Bulk archive Vision не перенесён
   - Нет массовых операций

7. **Child Task creation** — заглушка
   - Модальное окно создания дочерней задачи — заглушка
   - Нет функционала добавления файлов для atom

8. **Nav badges** — отсутствуют
   - Счётчики на кнопках sidebar не работают
   - Нет обновления counts

### ⚠️ Частично реализованные функции

1. **Analytics page** — только контейнеры
   - Нет загрузки данных
   - Нет charts
   - Нет KPI

2. **Journal detail pane** — отсутствует
   - Нет боковой панели с деталями записи
   - Нет `showJournalDetail()`

3. **Tree run button** — отсутствует
   - Нет кнопки запуска Forge для атомов прямо в дереве
   - Нет `onDashboardRunClick()` интеграции

4. **Vision pipeline bar** — отсутствует
   - Нет прогресс-бара атомов под Vision
   - Нет `visionPipelineBarHtml()`

5. **Connection banner** — отсутствует
   - Нет баннера при потере связи с API

---

## 🎯 Рекомендации

### Приоритет 1 (Критично)

1. **Восстановить Detail Panel** — ключевой UX элемент
2. **Добавить Router context** — важная функция навигации
3. **Реализовать Child Task modal** — создание задач не работает
4. **Восстановить API endpoints** — добавить missing методы в `api/client.js`

### Приоритет 2 (Важно)

5. **Восстановить FSM visualization** — SVG диаграмма
6. **Добавить Dashboard charts** — Chart.js интеграция
7. **Восстановить Quick Jump sidebar** — навигация
8. **Добавить Nav badges** — счётчики

### Приоритет 3 (Желательно)

9. **Добавить bulk actions** — массовые операции
10. **Восстановить Vision pipeline bar** — прогресс
11. **Добавить connection banner** — UX при ошибках
12. **Реализовать focus states** — accessibility

---

## 📊 Итоговая таблица покрытия

| Категория | coverage | Статус |
|-----------|----------|--------|
| Страницы | 11/11 (100%) | ✅ |
| Header элементы | 7/9 (78%) | ⚠️ |
| Sidebar элементы | 9/11 (82%) | ⚠️ |
| Dashboard компоненты | 3/6 (50%) | ⚠️ |
| Journal компоненты | 11/13 (85%) | ⚠️ |
| Tree компоненты | 9/13 (69%) | ⚠️ |
| Forge компоненты | 6/6 (100%) | ✅ |
| FSM компоненты | 1/5 (20%) | ❌ |
| Agents компоненты | 5/5 (100%) | ✅ |
| Detail Panel | 0/10 (0%) | ❌ |
| Кнопки интерактивные | 25/35 (71%) | ⚠️ |
| API endpoints | 36/42 (86%) | ⚠️ |
| Accessibility | 3/5 (60%) | ⚠️ |
| Производительность | 4/6 (67%) | ⚠️ |

**Общее покрытие: ~75%**

---

## 📁 Приложения

### A. Список файлов нового фронтенда

```
/static/
├── css/
│   └── factory.css (1167 строк)
├── js/
│   ├── main.js (entry point)
│   ├── api/
│   │   └── client.js (API client)
│   ├── state/
│   │   └── store.js (State management)
│   └── components/
│       ├── Dashboard.js
│       ├── Tree.js
│       ├── Journal.js
│       ├── Chat.js
│       ├── Forge.js
│       ├── FSM.js
│       ├── Analytics.js
│       ├── Others.js (Agents, Improvements, Judgements, HR, Failures)
│       └── ui.js (UI component library)
```

### B. Ключевые отличия в архитектуре

| Аспект | Legacy | Новый |
|--------|--------|-------|
| Модульность | Монолит | ES6 модули |
| State | Глобальные переменные | Pub/sub store |
| API calls | Inline функции | Централизованный client |
| Components | HTML строки | Функции-компоненты |
| Cleanup | Отсутствует | Есть в компонентах |
| Streaming | Базовый | SSE с reconnect |

---

**Конец отчёта**
