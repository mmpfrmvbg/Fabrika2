/**
 * Factory OS — State Store
 * Единый store с pub/sub (как Pinia, но vanilla JS)
 */

import { api } from '../api/client.js';

const listeners = new Set();

export const store = {
  // ═══════════════════════════════════════════════════════
  // STATE
  // ═══════════════════════════════════════════════════════
  
  state: {
    // Data
    tree: [],
    journal: [],
    workItems: [],
    visions: [],
    runs: [],
    analytics: null,
    agents: null,
    fsm: null,
    improvements: [],
    workersStatus: null,

    // Chat state
    chat: {
      isOpen: false,
      messages: [],
      isLoading: false,
      currentChatId: null,
      contextWorkItemId: null,
      context: null,
      streamCleanup: null
    },

    // UI
    selectedWorkItemId: null,
    activePage: 'dashboard',
    apiConnected: false,
    apiError: null,

    // Orchestrator
    orchestrator: {
      running: false,
      lastTick: null,
      ticksTotal: 0
    }
  },
  
  // ═══════════════════════════════════════════════════════
  // PUB/SUB
  // ═══════════════════════════════════════════════════════
  
  /**
   * Подписаться на изменения состояния
   * @param {Function} fn - (state, changes) => void
   * @returns {Function} unsubscribe
   */
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  
  /**
   * Обновить состояние с уведомлением слушателей
   * @param {Object} changes - изменения для state
   */
  update(changes) {
    // Флаг для стриминг-апдейтов (чтобы UI не перерисовывался полностью)
    if (changes._streamChunk) {
      changes._isStreamUpdate = true;
    }
    Object.assign(this.state, changes);
    listeners.forEach(fn => fn(this.state, changes));
  },
  
  // ═══════════════════════════════════════════════════════
  // ACTIONS — методы store напрямую (не вложенные)
  // ═══════════════════════════════════════════════════════

  /**
   * Нормализовать ответ API — вернуть массив
   */
  _normalizeArray(value, key) {
    if (Array.isArray(value)) return value;
    if (!value || typeof value !== 'object') return [];
    
    // Проверяем известные ключи
    const keysToTry = [key, 'items', 'data', 'candidates', 'visions', 'work_items', 'tree', 'nodes', 'results', 'agents', 'transitions', 'workers', 'clusters', 'policies', 'proposals'];
    for (const k of keysToTry) {
      if (k && Array.isArray(value[k])) return value[k];
    }
    
    // Если ничего не подошло — пустой массив
    console.warn('[Store] API вернул не массив:', key, value);
    return [];
  },

  /**
   * Загрузить дерево задач
   */
  async loadTree() {
    try {
      const tree = await api.getTree();
      this.update({ 
        tree: this._normalizeArray(tree),
        apiConnected: true, 
        apiError: null 
      });
    } catch (error) {
      this.update({ apiConnected: false, apiError: error.message });
      console.error('[Store] Failed to load tree:', error);
    }
  },

  /**
   * Загрузить журнал
   */
  async loadJournal(params = {}) {
    try {
      const journal = await api.getJournal(params);
      this.update({ 
        journal: {
          items: this._normalizeArray(journal?.items || journal),
          total: journal?.total || journal?.length || 0
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load journal:', error);
    }
  },

  /**
   * Загрузить work items
   */
  async loadWorkItems() {
    try {
      const workItems = await api.getWorkItems();
      this.update({ workItems: this._normalizeArray(workItems) });
    } catch (error) {
      console.error('[Store] Failed to load work items:', error);
    }
  },

  /**
   * Загрузить visions
   */
  async loadVisions() {
    try {
      const visions = await api.getVisions();
      this.update({ visions: this._normalizeArray(visions) });
    } catch (error) {
      console.error('[Store] Failed to load visions:', error);
    }
  },

  /**
   * Загрузить runs
   */
  async loadRuns(limit = 20) {
    try {
      const runs = await api.getRuns({ limit });
      this.update({
        runs: this._normalizeArray(runs)
      });
    } catch (error) {
      console.error('[Store] Failed to load runs:', error);
    }
  },

  /**
   * Загрузить аналитику
   */
  async loadAnalytics(period = '24h') {
    try {
      const analytics = await api.getAnalytics(period);
      this.update({ analytics });
    } catch (error) {
      console.error('[Store] Failed to load analytics:', error);
    }
  },

  /**
   * Загрузить статус оркестратора
   */
  async loadOrchestratorStatus() {
    try {
      const status = await api.getOrchestratorStatus();
      this.update({
        orchestrator: {
          running: status?.running || false,
          lastTick: status?.last_tick || null,
          ticksTotal: status?.ticks_total || 0
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load orchestrator status:', error);
    }
  },
  
  /**
   * Загрузить статус workers
   */
  async loadWorkersStatus() {
    try {
      const workersStatus = await api.getWorkersStatus();
      this.update({ 
        workersStatus: {
          workers: this._normalizeArray(workersStatus?.workers || workersStatus),
          active: workersStatus?.active || workersStatus?.workers?.length || 0
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load workers status:', error);
    }
  },

  /**
   * Загрузить агентов
   */
  async loadAgents() {
    try {
      const agents = await api.getAgents();
      this.update({ 
        agents: {
          agents: this._normalizeArray(agents?.agents || agents)
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load agents:', error);
    }
  },

  /**
   * Загрузить FSM transitions
   */
  async loadFsm() {
    try {
      const fsm = await api.getFsmTransitions();
      this.update({ 
        fsm: {
          transitions: this._normalizeArray(fsm?.transitions || fsm)
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load FSM:', error);
    }
  },

  /**
   * Загрузить improvements
   */
  async loadImprovements() {
    try {
      const improvements = await api.getImprovements();
      this.update({
        improvements: {
          candidates: this._normalizeArray(improvements?.candidates || improvements),
          stats: improvements?.stats || {}
        }
      });
    } catch (error) {
      console.error('[Store] Failed to load improvements:', error);
    }
  },

  /**
   * Загрузить HR (roles, policies, proposals)
   */
  async loadHR() {
    try {
      const hr = await api.getHR();
      this.update({ hr });
    } catch (error) {
      console.error('[Store] Failed to load HR:', error);
    }
  },

  /**
   * Загрузить failure clusters
   */
  async loadFailures() {
    try {
      const failures = await api.getFailures();
      this.update({ failures });
    } catch (error) {
      console.error('[Store] Failed to load failures:', error);
    }
  },
  
  /**
   * Выбрать work item
   */
  selectWorkItem(id) {
    this.update({ selectedWorkItemId: id });
  },
  
  /**
   * Установить активную страницу
   */
  setActivePage(page) {
    this.update({ activePage: page });
  },
  
  // ═══════════════════════════════════════════════════════
  // CHAT ACTIONS
  // ═══════════════════════════════════════════════════════
  
  /**
   * Открыть чат
   */
  openChat(workItemId = null) {
    const context = workItemId ? this._getWorkItemContext(workItemId) : null;
    this.update({ 
      chat: { 
        ...this.state.chat, 
        isOpen: true, 
        contextWorkItemId: workItemId,
        context 
      } 
    });
  },
  
  /**
   * Закрыть чат
   */
  closeChat() {
    // Закрыть SSE поток при закрытии
    if (this.state.chat.streamCleanup) {
      this.state.chat.streamCleanup();
    }
    this.update({ chat: { ...this.state.chat, isOpen: false } });
  },
  
  /**
   * Отправить сообщение в чат
   */
  async sendMessage(prompt) {
    const { contextWorkItemId, context } = this.state.chat;

    // Добавить сообщение пользователя
    const userMessage = {
      role: 'user',
      content: prompt,
      timestamp: new Date().toISOString(),
      work_item_id: contextWorkItemId
    };

    this.update({
      chat: {
        ...this.state.chat,
        messages: [...this.state.chat.messages, userMessage],
        isLoading: true
      }
    });

    try {
      // Создать сессию чата (POST)
      const { chat_id } = await api.chat.create(prompt, {
        work_item_id: contextWorkItemId,
        work_item_context: context
      });

      this.update({ chat: { ...this.state.chat, currentChatId: chat_id } });

      // ═══════════════════════════════════════════════════════
      // ИСПРАВЛЕНИЕ: Reconnect logic для SSE
      // ═══════════════════════════════════════════════════════
      let fullResponse = '';
      let reconnectAttempts = 0;
      const maxReconnects = 3;
      
      const assistantMessage = {
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        work_item_id: contextWorkItemId
      };

      this.update({
        chat: {
          ...this.state.chat,
          messages: [...this.state.chat.messages, assistantMessage]
        }
      });

      const connectSSE = () => {
        const cleanup = api.chat.stream(chat_id, {
          onChunk: (content) => {
            fullResponse += content;
            reconnectAttempts = 0;  // Сброс при успешном получении данных
            
            // Стриминг-апдейт — не полный re-render
            this.update({
              _streamChunk: fullResponse,
              chat: {
                ...this.state.chat,
                messages: this.state.chat.messages.map((m, i) =>
                  i === this.state.chat.messages.length - 1
                    ? { ...m, content: fullResponse }
                    : m
                )
              }
            });
          },
          onDone: () => {
            this.update({
              chat: { ...this.state.chat, isLoading: false, streamCleanup: null },
              _streamChunk: null
            });
          },
          onError: (error) => {
            // Попытка reconnect
            if (reconnectAttempts < maxReconnects) {
              reconnectAttempts++;
              console.log(`[Chat] Reconnect attempt ${reconnectAttempts}/${maxReconnects}`);
              setTimeout(connectSSE, 1000 * reconnectAttempts);
            } else {
              // Заменить пустое сообщение ассистента на текст ошибки
              const msgs = this.state.chat.messages;
              const updatedMsgs = msgs.map((m, i) =>
                i === msgs.length - 1 && m.role === 'assistant' && !m.content
                  ? { ...m, content: `⚠️ Ошибка соединения с Qwen. Проверьте что сервер запущен.` }
                  : m
              );
              this.update({
                chat: { ...this.state.chat, isLoading: false, streamCleanup: null, messages: updatedMsgs },
                _streamChunk: null
              });
              console.error('[Chat] Connection lost after retries:', error);
            }
          }
        });

        this.update({ chat: { ...this.state.chat, streamCleanup: cleanup } });
      };

      connectSSE();

    } catch (error) {
      console.error('[Chat] Failed to send message:', error);
      // Добавить сообщение об ошибке вместо пустого ответа
      const errorMessage = {
        role: 'assistant',
        content: `⚠️ Не удалось отправить сообщение: ${error.message}`,
        timestamp: new Date().toISOString()
      };
      this.update({
        chat: {
          ...this.state.chat,
          isLoading: false,
          messages: [...this.state.chat.messages, errorMessage]
        },
        _streamChunk: null
      });
    }
  },
  
  /**
   * Собрать контекст задачи
   */
  _getWorkItemContext(workItemId) {
    const workItem = this.state.workItems.find(w => w.id === workItemId);
    if (!workItem) return null;
    
    return {
      id: workItem.id,
      kind: workItem.kind,
      title: workItem.title,
      description: workItem.description,
      status: workItem.status,
      parent_id: workItem.parent_id,
    };
  },
  
  // ═══════════════════════════════════════════════════════
  // ORCHESTRATOR ACTIONS
  // ═══════════════════════════════════════════════════════
  
  async orchestratorStart() {
    try {
      await api.orchestratorStart();
      await this.loadOrchestratorStatus();
    } catch (error) {
      console.error('[Store] Failed to start orchestrator:', error);
      throw error;
    }
  },
  
  async orchestratorStop() {
    try {
      await api.orchestratorStop();
      await this.loadOrchestratorStatus();
    } catch (error) {
      console.error('[Store] Failed to stop orchestrator:', error);
      throw error;
    }
  },
  
  async orchestratorTick() {
    try {
      await api.orchestratorTick();
      await this.loadOrchestratorStatus();
    } catch (error) {
      console.error('[Store] Failed to tick orchestrator:', error);
      throw error;
    }
  }
};

// ═══════════════════════════════════════════════════════
// EXPORTS
// ═══════════════════════════════════════════════════════

export const { state, subscribe, update } = store;
