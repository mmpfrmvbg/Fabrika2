/**
 * Factory OS — API Client
 * Единая точка доступа к API фабрики
 */

// ═══════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════

// API сервер всегда на 8000 порту (python -m factory.api_server)
const API_BASE = 'http://127.0.0.1:8000';

// ═══════════════════════════════════════════════════════
// HEADERS
// ═══════════════════════════════════════════════════════

function getApiHeaders(extra = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...extra
  };

  // Если задан API ключ — добавляем для мутаций
  if (window.FACTORY_API_KEY) {
    headers['X-API-Key'] = window.FACTORY_API_KEY;
  }

  return headers;
}

// ═══════════════════════════════════════════════════════
// FETCH HELPERS
// ═══════════════════════════════════════════════════════

/**
 * Fetch JSON с API сервера
 * @param {string} url - Путь к API (начинается с /)
 * @param {RequestInit} options - fetch options
 */
async function fetchJson(url, options = {}) {
  // Если url не абсолютный — добавляем API_BASE
  if (!url.startsWith('http')) {
    // Гарантируем что путь начинается с /
    if (!url.startsWith('/')) url = '/' + url;
    url = API_BASE + url;
  }

  const response = await fetch(url, {
    ...options,
    headers: getApiHeaders(options.headers)
  });

  if (!response.ok) {
    const error = await response.text().catch(() => response.statusText);
    throw new Error(`API Error: ${response.status} ${error}`);
  }

  // Пустой ответ
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return null;
  }

  return response.json();
}

async function fetchJsonPost(url, body) {
  return fetchJson(url, {
    method: 'POST',
    body: JSON.stringify(body)
  });
}

async function fetchJsonPatch(url, body) {
  return fetchJson(url, {
    method: 'PATCH',
    body: JSON.stringify(body)
  });
}

async function fetchJsonDelete(url) {
  return fetchJson(url, { method: 'DELETE' });
}

// ═══════════════════════════════════════════════════════
// API CLIENT
// ═══════════════════════════════════════════════════════

export const api = {
  // ── Journal / Events ─────────────────────────────────
  async getJournal(params = {}) {
    const url = new URL('/api/journal', API_BASE);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined) {
        url.searchParams.set(k, v);
      }
    });
    return fetchJson(url.toString());
  },

  async getEvents(params = {}) {
    const url = new URL('/api/events', API_BASE);
    Object.entries(params).forEach(([k, v]) => {
      if (v !== null && v !== undefined) {
        url.searchParams.set(k, v);
      }
    });
    return fetchJson(url.toString());
  },

  // ── Work Items Tree ──────────────────────────────────
  async getTree() {
    return fetchJson('/api/work-items/tree');
  },

  async getWorkItems(filters = {}) {
    const url = new URL('/api/work-items', API_BASE);
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== null && v !== undefined) {
        url.searchParams.set(k, v);
      }
    });
    return fetchJson(url.toString());
  },
  
  async getWorkItem(id) {
    return fetchJson(`/api/work-items/${id}`);
  },
  
  async getWorkItemEvents(id) {
    return fetchJson(`/api/work-items/${id}/events`);
  },
  
  // ── Runs ─────────────────────────────────────────────
  async getRuns(filters = {}) {
    const url = new URL('/api/runs', API_BASE);
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== null && v !== undefined) {
        url.searchParams.set(k, v);
      }
    });
    return fetchJson(url.toString());
  },
  
  async getRun(id) {
    return fetchJson(`/api/runs/${id}`);
  },
  
  async getRunSteps(runId) {
    return fetchJson(`/api/runs/${runId}/steps`);
  },
  
  // ── Visions ──────────────────────────────────────────
  async getVisions() {
    return fetchJson('/api/visions');
  },
  
  async createVision(data) {
    return fetchJsonPost('/api/visions', data);
  },
  
  // ── Forge Queue ──────────────────────────────────────
  async getForgeQueue() {
    return fetchJson('/api/queue/forge_inbox');
  },
  
  // ── Actions ──────────────────────────────────────────
  async runWorkItem(id, data = {}) {
    return fetchJsonPost(`/api/work-items/${id}/run`, data);
  },
  
  async forgeRun(id) {
    return fetchJsonPost(`/api/tasks/${id}/forge-run`, {});
  },
  
  async transitionWorkItem(id, event) {
    return fetchJsonPost(`/api/tasks/${id}/transition`, { event });
  },
  
  async cancelWorkItem(id) {
    return fetchJsonPost(`/api/work-items/${id}/cancel`, {});
  },
  
  async archiveWorkItem(id) {
    return fetchJsonPost(`/api/work-items/${id}/archive`, {});
  },
  
  async deleteWorkItem(id) {
    return fetchJsonDelete(`/api/work-items/${id}`);
  },
  
  async createChild(parentId, childData) {
    return fetchJsonPost(`/api/tasks/${parentId}/children`, childData);
  },
  
  async addComment(workItemId, comment) {
    return fetchJsonPost(`/api/work-items/${workItemId}/comments`, { comment });
  },
  
  // ── Analytics ────────────────────────────────────────
  async getAnalytics(period = '24h') {
    return fetchJson(`/api/analytics?period=${period}`);
  },
  
  async getStats() {
    return fetchJson('/api/stats');
  },
  
  // ── Orchestrator ─────────────────────────────────────
  async getOrchestratorStatus() {
    return fetchJson('/api/orchestrator/status');
  },
  
  async orchestratorStart() {
    return fetchJsonPost('/api/orchestrator/start', {});
  },
  
  async orchestratorStop() {
    return fetchJsonPost('/api/orchestrator/stop', {});
  },
  
  async orchestratorTick() {
    return fetchJsonPost('/api/orchestrator/tick', {});
  },
  
  async getOrchestratorHealth() {
    return fetchJson('/api/orchestrator/health');
  },
  
  // ── FSM / Agents ─────────────────────────────────────
  async getFsmTransitions() {
    return fetchJson('/api/fsm/work_item');
  },
  
  async getAgents() {
    return fetchJson('/api/agents');
  },
  
  // ── Improvements ─────────────────────────────────────
  async getImprovements() {
    return fetchJson('/api/improvements');
  },
  
  async approveImprovement(id) {
    return fetchJsonPost(`/api/improvements/${id}/approve`, {});
  },
  
  async rejectImprovement(id) {
    return fetchJsonPost(`/api/improvements/${id}/reject`, {});
  },
  
  async convertImprovement(id) {
    return fetchJsonPost(`/api/improvements/${id}/convert`, {});
  },
  
  // ── Workers Status ───────────────────────────────────
  async getWorkersStatus() {
    return fetchJson('/api/workers/status');
  },
  
  // ── Chat (Qwen SSE) ──────────────────────────────────
  chat: {
    /**
     * POST: Создать сессию чата, вернуть chat_id
     */
    async create(prompt, context = {}) {
      return fetchJsonPost('/api/chat/qwen', { prompt, context });
    },
    
    /**
     * GET SSE: Подключиться к потоку по chat_id
     * callbacks: { onChunk, onDone, onError }
     * Returns: cleanup function
     */
    stream(chatId, callbacks) {
      const url = `${API_BASE}/api/chat/qwen/${chatId}/stream`;
      const eventSource = new EventSource(url);
      
      eventSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === 'chunk') {
            callbacks.onChunk?.(data.content);
          } else if (data.type === 'done') {
            callbacks.onDone?.(data.full_response);
            eventSource.close();
          } else if (data.type === 'error') {
            callbacks.onError?.(data.error);
            eventSource.close();
          }
        } catch (err) {
          console.error('[Chat SSE] Parse error:', err);
        }
      };
      
      eventSource.onerror = () => {
        callbacks.onError?.('Connection lost');
        eventSource.close();
      };
      
      // Return cleanup function
      return () => eventSource.close();
    }
  }
};

// ═══════════════════════════════════════════════════════
// EXPORTS
// ═══════════════════════════════════════════════════════

export { fetchJson, fetchJsonPost, fetchJsonPatch, fetchJsonDelete, API_BASE };
