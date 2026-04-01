/**
 * Factory OS — UI Component Library
 * Premium dark control center components
 */

export const UI = {
  // ═══════════════════════════════════════════════════════
  // PAGE HEADER
  // ═══════════════════════════════════════════════════════
  
  PageHeader({ title, subtitle, actions = '' }) {
    return `
      <div class="page-header">
        <div>
          <div class="page-title">${this.escapeHtml(title)}</div>
          ${subtitle ? `<div class="page-sub">${this.escapeHtml(subtitle)}</div>` : ''}
        </div>
        ${actions ? `<div style="margin-left:auto;display:flex;gap:var(--space-2);align-items:center">${actions}</div>` : ''}
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // SECTION CARD
  // ═══════════════════════════════════════════════════════
  
  SectionCard({ title, icon, children, footer = '' }) {
    return `
      <div class="card">
        ${title ? `
          <div class="card-header">
            ${icon ? `<span class="card-header-icon">${icon}</span>` : ''}
            <span>${this.escapeHtml(title)}</span>
          </div>
        ` : ''}
        <div class="card-body">${children}</div>
        ${footer ? `<div class="card-footer">${footer}</div>` : ''}
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // METRIC CARD
  // ═══════════════════════════════════════════════════════
  
  MetricCard({ label, value, delta, deltaType = 'neutral', icon = '' }) {
    const deltaClass = deltaType === 'up' ? 'up' : deltaType === 'down' ? 'down' : 'neutral';
    const deltaIcon = deltaType === 'up' ? '↑' : deltaType === 'down' ? '↓' : '';
    
    return `
      <div class="kpi-card">
        <div class="kpi-label">${this.escapeHtml(label)}</div>
        <div class="kpi-value">${icon ? `<span style="margin-right:8px">${icon}</span>` : ''}${value}</div>
        ${delta ? `<div class="kpi-delta ${deltaClass}">${deltaIcon} ${this.escapeHtml(delta)}</div>` : ''}
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // EMPTY STATE
  // ═══════════════════════════════════════════════════════
  
  EmptyState({ icon = '📭', title, description, cta = '' }) {
    return `
      <div class="empty-state">
        <div class="es-icon" aria-hidden="true">${icon}</div>
        <div class="es-title">${this.escapeHtml(title)}</div>
        ${description ? `<div class="es-sub">${this.escapeHtml(description)}</div>` : ''}
        ${cta ? `<div class="es-actions">${cta}</div>` : ''}
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // SKELETON LOADER
  // ═══════════════════════════════════════════════════════
  
  Skeleton({ type = 'card', count = 1 }) {
    if (type === 'card') {
      return Array(count).fill(0).map(() => `
        <div class="kpi-card">
          <div class="skeleton" style="height:14px;width:60%;margin-bottom:8px"></div>
          <div class="skeleton" style="height:28px;width:80%"></div>
          <div class="skeleton" style="height:12px;width:50%;margin-top:8px"></div>
        </div>
      `).join('');
    }
    
    if (type === 'table') {
      return `
        <div class="skeleton" style="height:40px;width:100%;margin-bottom:8px"></div>
        ${Array(count).fill(0).map(() => `
          <div class="skeleton" style="height:50px;width:100%;margin-bottom:8px"></div>
        `).join('')}
      `;
    }
    
    return `<div class="skeleton" style="width:100%;height:${type}px"></div>`;
  },

  // ═══════════════════════════════════════════════════════
  // ERROR BANNER
  // ═══════════════════════════════════════════════════════
  
  ErrorBanner({ message, onRetry }) {
    return `
      <div class="error-banner" role="alert">
        <span style="margin-right:8px">⚠️</span>
        <span>${this.escapeHtml(message)}</span>
        ${onRetry ? `
          <button class="btn" onclick="${onRetry}" style="margin-left:12px;padding:4px 12px;font-size:12px">
            ↻ Retry
          </button>
        ` : ''}
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // STATUS BADGE
  // ═══════════════════════════════════════════════════════
  
  StatusBadge({ status, size = 'sm' }) {
    const statusMap = {
      draft: { label: 'Draft', class: 's-draft' },
      planned: { label: 'Planned', class: 's-planned' },
      ready_for_judge: { label: '→ Judge', class: 's-ready_for_judge' },
      judge_rejected: { label: 'Judge ✗', class: 's-judge_rejected' },
      ready_for_work: { label: 'Ready', class: 's-ready_for_work' },
      in_progress: { label: 'Running', class: 's-in_progress' },
      in_review: { label: 'In Review', class: 's-in_review' },
      review_rejected: { label: 'Review ✗', class: 's-review_rejected' },
      blocked: { label: 'Blocked', class: 's-blocked' },
      done: { label: 'Done', class: 's-done' },
      cancelled: { label: 'Cancelled', class: 's-cancelled' },
      active: { label: 'Active', class: 's-done' },
      idle: { label: 'Idle', class: 's-draft' },
      error: { label: 'Error', class: 's-failed' },
      failed: { label: 'Failed', class: 's-failed' },
    };
    
    const config = statusMap[status] || { label: status, class: 's-draft' };
    const sizeClass = size === 'lg' ? 'style="font-size:12px;padding:4px 10px"' : '';
    
    return `<span class="badge ${config.class}" ${sizeClass}><span class="badge-dot"></span>${config.label}</span>`;
  },

  // ═══════════════════════════════════════════════════════
  // KIND BADGE
  // ═══════════════════════════════════════════════════════
  
  KindBadge({ kind }) {
    const kindMap = {
      vision: 'k-vision',
      epic: 'k-epic',
      story: 'k-story',
      task: 'k-task',
      atom: 'k-atom',
      initiative: 'k-initiative',
    };
    
    const kindClass = kindMap[kind] || 'k-task';
    return `<span class="kind-badge ${kindClass}">${this.escapeHtml(kind)}</span>`;
  },

  // ═══════════════════════════════════════════════════════
  // ROLE BADGE
  // ═══════════════════════════════════════════════════════
  
  RoleBadge({ role }) {
    const roleMap = {
      creator: 'r-creator',
      architect: 'r-architect',
      planner: 'r-planner',
      judge: 'r-judge',
      forge: 'r-forge',
      reviewer: 'r-reviewer',
      hr: 'r-hr',
      orchestrator: 'r-orchestrator',
      system: 'r-system',
    };
    
    const roleClass = roleMap[role] || 'r-system';
    return `<span class="role-badge ${roleClass}">${this.escapeHtml(role)}</span>`;
  },

  // ═══════════════════════════════════════════════════════
  // DATA TABLE
  // ═══════════════════════════════════════════════════════
  
  DataTable({ columns, data, emptyMessage = 'No data' }) {
    if (!data || data.length === 0) {
      return `<div style="padding:40px;text-align:center;color:var(--text-muted)">${this.escapeHtml(emptyMessage)}</div>`;
    }
    
    return `
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              ${columns.map(col => `<th>${this.escapeHtml(col.header)}</th>`).join('')}
            </tr>
          </thead>
          <tbody>
            ${data.map(row => `
              <tr>
                ${columns.map(col => `<td>${col.render ? col.render(row) : this.escapeHtml(row[col.key] || '—')}</td>`).join('')}
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // LOADING SPINNER
  // ═══════════════════════════════════════════════════════
  
  LoadingSpinner({ size = 'md' }) {
    const sizePx = size === 'sm' ? '20px' : size === 'lg' ? '60px' : '40px';
    return `
      <div style="display:flex;align-items:center;justify-content:center;padding:40px">
        <div style="width:${sizePx};height:${sizePx};border:3px solid var(--border);border-top-color:var(--primary);border-radius:50%;animation:spin 1s linear infinite"></div>
      </div>
      <style>@keyframes spin{to{transform:rotate(360deg)}}</style>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // PROGRESS BAR
  // ═══════════════════════════════════════════════════════
  
  ProgressBar({ value, max = 100, status = 'active' }) {
    const percent = Math.min(100, Math.max(0, (value / max) * 100));
    const statusClass = status === 'warn' ? 'warn' : status === 'error' ? 'error' : 'active';
    
    return `
      <div class="lease-bar-wrap">
        <div class="lease-bar ${statusClass}" style="width:${percent}%"></div>
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // MESSAGE (for Chat)
  // ═══════════════════════════════════════════════════════
  
  ChatMessage({ role, content, timestamp }) {
    const isUser = role === 'user';
    return `
      <div class="chat-message ${isUser ? 'user' : 'assistant'}">
        <div class="chat-message-header">
          <span class="chat-message-role">${isUser ? 'Вы' : 'Qwen'}</span>
          <span class="chat-message-time">${this.formatTime(timestamp)}</span>
        </div>
        <div class="chat-message-content">${this.escapeHtml(content)}</div>
      </div>
    `;
  },

  // ═══════════════════════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════════════════════
  
  escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  formatTime(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  },

  formatDuration(seconds) {
    if (!seconds || !Number.isFinite(seconds)) return '—';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return mins > 0 ? `${mins}м ${secs}с` : `${secs}с`;
  }
};
