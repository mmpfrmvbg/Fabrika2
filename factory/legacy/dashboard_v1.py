# ⚠️ DEPRECATED — not used in production. For reference only.
# See AUDIT_REPORT.md for details.

"""
Factory OS — Dashboard Server v1.0
Lightweight HTTP server over SQLite. Zero dependencies beyond stdlib.

Usage:
    python factory_dashboard_v1.py [--db factory.db] [--port 8420]

Endpoints:
    GET  /                       → Dashboard HTML
    GET  /api/tree               → Full work item tree
    GET  /api/pipeline           → Atom pipeline view
    GET  /api/events?limit=N&wi= → Event log (filterable)
    GET  /api/item/<id>          → Single work item + comments + decisions
    GET  /api/stats              → Aggregate statistics
    GET  /api/queues             → Current queue state
"""

import json
import sqlite3
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

DB_PATH = "factory.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        routes = {
            "/": self.serve_html,
            "/api/tree": self.api_tree,
            "/api/pipeline": self.api_pipeline,
            "/api/events": self.api_events,
            "/api/stats": self.api_stats,
            "/api/queues": self.api_queues,
        }

        if path.startswith("/api/item/"):
            self.api_item(path.split("/api/item/")[1])
        elif path in routes:
            routes[path](params)
        else:
            self.send_error(404)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def html_response(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── API endpoints ───────────────────────────────────────────────────

    def api_tree(self, params):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT wi.*, 
                   (SELECT COUNT(*) FROM work_items c WHERE c.parent_id = wi.id) AS child_count,
                   (SELECT COUNT(*) FROM work_item_files f WHERE f.work_item_id = wi.id) AS file_count,
                   (SELECT COUNT(*) FROM comments cm WHERE cm.work_item_id = wi.id) AS comment_count
            FROM work_items wi
            ORDER BY wi.root_id, wi.priority, wi.created_at
        """)
        items = rows_to_dicts(cur.fetchall())
        conn.close()

        # Build tree structure
        by_id = {i["id"]: {**i, "children": []} for i in items}
        roots = []
        for item in items:
            node = by_id[item["id"]]
            if item["parent_id"] and item["parent_id"] in by_id:
                by_id[item["parent_id"]]["children"].append(node)
            else:
                roots.append(node)

        self.json_response(roots)

    def api_pipeline(self, params):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                wi.id, wi.title, wi.status, wi.kind, wi.owner_role,
                wi.priority, wi.forge_attempts, wi.review_rejections, 
                wi.judge_rejections, wi.needs_human_review,
                wi.created_at, wi.updated_at,
                wiq.queue_name, wiq.lease_owner, wiq.attempts AS queue_attempts,
                (SELECT GROUP_CONCAT(wf.path, '|') FROM work_item_files wf 
                 WHERE wf.work_item_id = wi.id) AS files,
                (SELECT COUNT(*) FROM file_locks fl 
                 WHERE fl.work_item_id = wi.id AND fl.released_at IS NULL) AS active_locks,
                (SELECT r.status FROM runs r WHERE r.work_item_id = wi.id 
                 ORDER BY r.created_at DESC LIMIT 1) AS last_run_status,
                (SELECT r.created_at FROM runs r WHERE r.work_item_id = wi.id 
                 ORDER BY r.created_at DESC LIMIT 1) AS last_run_at
            FROM work_items wi
            LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
            WHERE wi.kind = 'atom' AND wi.status NOT IN ('archived')
            ORDER BY 
                CASE wi.status 
                    WHEN 'in_progress' THEN 1 WHEN 'in_review' THEN 2
                    WHEN 'ready_for_work' THEN 3 WHEN 'ready_for_judge' THEN 4
                    WHEN 'review_rejected' THEN 5 WHEN 'judge_rejected' THEN 6
                    WHEN 'blocked' THEN 7 WHEN 'done' THEN 8
                    ELSE 9 END,
                wi.priority, wi.created_at
        """)
        self.json_response(rows_to_dicts(cur.fetchall()))
        conn.close()

    def api_events(self, params):
        conn = get_db()
        cur = conn.cursor()
        limit = int(params.get("limit", [200])[0])
        wi_id = params.get("wi", [None])[0]
        severity = params.get("severity", [None])[0]
        event_type = params.get("type", [None])[0]

        query = "SELECT * FROM event_log WHERE 1=1"
        args = []
        if wi_id:
            query += " AND work_item_id = ?"
            args.append(wi_id)
        if severity:
            query += " AND severity = ?"
            args.append(severity)
        if event_type:
            query += " AND event_type LIKE ?"
            args.append(f"%{event_type}%")
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)

        cur.execute(query, args)
        self.json_response(rows_to_dicts(cur.fetchall()))
        conn.close()

    def api_item(self, item_id):
        conn = get_db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM work_items WHERE id = ?", (item_id,))
        wi = cur.fetchone()
        if not wi:
            self.send_error(404)
            conn.close()
            return

        result = dict(wi)

        cur.execute("SELECT * FROM comments WHERE work_item_id = ? ORDER BY created_at DESC", (item_id,))
        result["comments"] = rows_to_dicts(cur.fetchall())

        cur.execute("SELECT * FROM decisions WHERE work_item_id = ? ORDER BY created_at DESC", (item_id,))
        result["decisions"] = rows_to_dicts(cur.fetchall())

        cur.execute("SELECT * FROM work_item_files WHERE work_item_id = ?", (item_id,))
        result["files"] = rows_to_dicts(cur.fetchall())

        cur.execute("SELECT * FROM runs WHERE work_item_id = ? ORDER BY created_at DESC LIMIT 10", (item_id,))
        result["runs"] = rows_to_dicts(cur.fetchall())

        cur.execute("""
            SELECT * FROM work_item_links 
            WHERE src_id = ? OR dst_id = ?
        """, (item_id, item_id))
        result["links"] = rows_to_dicts(cur.fetchall())

        cur.execute("SELECT * FROM work_items WHERE parent_id = ? ORDER BY priority, created_at", (item_id,))
        result["children"] = rows_to_dicts(cur.fetchall())

        self.json_response(result)
        conn.close()

    def api_stats(self, params):
        conn = get_db()
        cur = conn.cursor()

        stats = {}

        cur.execute("""
            SELECT status, COUNT(*) as cnt FROM work_items 
            WHERE status NOT IN ('archived') GROUP BY status
        """)
        stats["by_status"] = {r["status"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("""
            SELECT kind, COUNT(*) as cnt FROM work_items 
            WHERE status NOT IN ('archived','cancelled') GROUP BY kind
        """)
        stats["by_kind"] = {r["kind"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) FROM work_items WHERE needs_human_review = 1")
        stats["needs_human"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM file_locks WHERE released_at IS NULL")
        stats["active_locks"] = cur.fetchone()[0]

        cur.execute("""
            SELECT queue_name, COUNT(*) as cnt, 
                   SUM(CASE WHEN lease_owner IS NOT NULL THEN 1 ELSE 0 END) as leased
            FROM work_item_queue GROUP BY queue_name
        """)
        stats["queues"] = {r["queue_name"]: {"total": r["cnt"], "leased": r["leased"]} 
                          for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) FROM runs 
            WHERE status = 'completed' AND created_at > datetime('now', '-24 hours')
        """)
        stats["runs_24h"] = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM event_log 
            WHERE severity IN ('error','fatal') AND event_time > datetime('now', '-24 hours')
        """)
        stats["errors_24h"] = cur.fetchone()[0]

        self.json_response(stats)
        conn.close()

    def api_queues(self, params):
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT wiq.*, wi.title, wi.kind, wi.priority
            FROM work_item_queue wiq
            JOIN work_items wi ON wi.id = wiq.work_item_id
            ORDER BY wiq.queue_name, wi.priority, wiq.created_at
        """)
        self.json_response(rows_to_dicts(cur.fetchall()))
        conn.close()

    # ── HTML Dashboard ──────────────────────────────────────────────────

    def serve_html(self, params):
        self.html_response(DASHBOARD_HTML)

    def log_message(self, format, *args):
        pass  # Suppress request logging


# ═══════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Factory OS — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0e0e10;--surface:#161618;--surface2:#1c1c1f;--surface3:#232326;
  --border:#2a2a2e;--border-subtle:#222225;
  --text:#e4e4e7;--text-muted:#71717a;--text-faint:#52525b;
  --accent:#3b82f6;--accent-hover:#2563eb;
  --green:#22c55e;--yellow:#eab308;--red:#ef4444;--orange:#f97316;--purple:#a855f7;--teal:#14b8a6;
  --font-sans:'Inter',system-ui,sans-serif;
  --font-mono:'JetBrains Mono',monospace;
  --radius:6px;
}
body{background:var(--bg);color:var(--text);font-family:var(--font-sans);font-size:14px;line-height:1.5;min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

/* Layout */
.app{display:grid;grid-template-columns:260px 1fr;grid-template-rows:48px 1fr;height:100vh}
.topbar{grid-column:1/-1;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 20px;gap:16px;z-index:10}
.topbar h1{font-size:15px;font-weight:700;letter-spacing:-.02em}
.topbar .logo{color:var(--accent);font-family:var(--font-mono);font-weight:700}
.sidebar{background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:12px 0}
.main{overflow-y:auto;padding:20px 24px}

/* Sidebar nav */
.nav-section{padding:8px 16px 4px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint)}
.nav-item{display:flex;align-items:center;gap:8px;padding:6px 16px;cursor:pointer;font-size:13px;color:var(--text-muted);transition:all .15s}
.nav-item:hover,.nav-item.active{background:var(--surface2);color:var(--text)}
.nav-item.active{border-right:2px solid var(--accent)}
.nav-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}

/* Stats bar */
.stats-bar{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
.stat-card{background:var(--surface);border:1px solid var(--border-subtle);border-radius:var(--radius);padding:12px 16px;min-width:140px;flex:1}
.stat-card .label{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.stat-card .value{font-size:24px;font-weight:700;font-family:var(--font-mono);font-variant-numeric:tabular-nums}

/* Status badges */
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.02em;white-space:nowrap}
.badge.draft{background:#27272a;color:#a1a1aa}
.badge.planned{background:#1e3a5f;color:#60a5fa}
.badge.ready_for_judge{background:#3b1f6e;color:#c084fc}
.badge.judge_rejected{background:#4c1d1d;color:#fca5a5}
.badge.ready_for_work{background:#14332a;color:#6ee7b7}
.badge.in_progress{background:#1a3a2a;color:var(--green)}
.badge.in_review{background:#2d2006;color:var(--yellow)}
.badge.review_rejected{background:#3d1a0a;color:var(--orange)}
.badge.done{background:#052e16;color:#4ade80}
.badge.cancelled{background:#1c1c1c;color:#737373}
.badge.blocked{background:#3d0a0a;color:var(--red)}

/* Tree view */
.tree-node{margin-left:0}
.tree-node .children{margin-left:20px;border-left:1px solid var(--border-subtle);padding-left:0}
.tree-item{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;border-radius:var(--radius);transition:background .12s}
.tree-item:hover{background:var(--surface2)}
.tree-toggle{width:16px;height:16px;display:flex;align-items:center;justify-content:center;color:var(--text-faint);font-size:10px;flex-shrink:0;transition:transform .15s}
.tree-toggle.open{transform:rotate(90deg)}
.tree-kind{font-size:10px;font-weight:700;font-family:var(--font-mono);padding:1px 5px;border-radius:3px;text-transform:uppercase;letter-spacing:.05em;flex-shrink:0}
.tree-kind.vision{background:#1e3a5f;color:#93c5fd}
.tree-kind.epic{background:#312e81;color:#a5b4fc}
.tree-kind.story{background:#3b1f6e;color:#c4b5fd}
.tree-kind.task{background:#1a3a2a;color:#86efac}
.tree-kind.atom{background:#2d2006;color:#fde68a}
.tree-kind.atm_change{background:#3d1a0a;color:#fed7aa}
.tree-title{flex:1;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tree-meta{font-size:11px;color:var(--text-faint);font-family:var(--font-mono);display:flex;gap:8px;flex-shrink:0}

/* Pipeline kanban */
.pipeline{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.pipeline-col{background:var(--surface);border:1px solid var(--border-subtle);border-radius:var(--radius);overflow:hidden}
.pipeline-col-header{padding:10px 14px;border-bottom:1px solid var(--border-subtle);display:flex;justify-content:space-between;align-items:center}
.pipeline-col-header h3{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.pipeline-col-header .count{font-size:11px;color:var(--text-muted);font-family:var(--font-mono)}
.pipeline-card{padding:10px 14px;border-bottom:1px solid var(--border-subtle);cursor:pointer;transition:background .12s}
.pipeline-card:hover{background:var(--surface2)}
.pipeline-card:last-child{border-bottom:none}
.pipeline-card .title{font-size:13px;font-weight:500;margin-bottom:4px}
.pipeline-card .meta{font-size:11px;color:var(--text-faint);font-family:var(--font-mono);display:flex;gap:8px;flex-wrap:wrap}

/* Event log */
.event-log{font-family:var(--font-mono);font-size:12px}
.event-row{display:grid;grid-template-columns:160px 60px 140px 1fr;gap:8px;padding:5px 12px;border-bottom:1px solid var(--border-subtle);align-items:start}
.event-row:hover{background:var(--surface2)}
.event-time{color:var(--text-faint)}
.event-severity{font-weight:600}
.event-severity.info{color:var(--accent)}
.event-severity.warn{color:var(--yellow)}
.event-severity.error{color:var(--red)}
.event-severity.fatal{color:var(--red);text-decoration:underline}
.event-severity.debug{color:var(--text-faint)}
.event-type{color:var(--teal)}
.event-msg{color:var(--text-muted);word-break:break-word}

/* Detail panel */
.detail-panel{position:fixed;right:0;top:48px;bottom:0;width:480px;background:var(--surface);border-left:1px solid var(--border);overflow-y:auto;padding:20px;transform:translateX(100%);transition:transform .2s ease;z-index:20}
.detail-panel.open{transform:translateX(0)}
.detail-panel h2{font-size:16px;font-weight:700;margin-bottom:16px}
.detail-section{margin-bottom:16px}
.detail-section h4{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);margin-bottom:8px}
.detail-section .field{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid var(--border-subtle)}
.detail-section .field .k{color:var(--text-muted)}
.comment-item{padding:8px 12px;background:var(--surface2);border-radius:var(--radius);margin-bottom:6px;font-size:12px}
.comment-item .header{display:flex;justify-content:space-between;margin-bottom:4px;color:var(--text-faint)}
.close-btn{position:absolute;top:16px;right:16px;background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:18px;padding:4px 8px}
.close-btn:hover{color:var(--text)}

/* Filters */
.filters{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.filter-btn{padding:4px 12px;border-radius:999px;font-size:12px;font-weight:500;background:var(--surface2);color:var(--text-muted);border:1px solid var(--border-subtle);cursor:pointer;transition:all .12s}
.filter-btn:hover,.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
input[type="text"]{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:6px 12px;color:var(--text);font-size:13px;font-family:var(--font-sans);width:200px}
input[type="text"]::placeholder{color:var(--text-faint)}
input[type="text"]:focus{outline:none;border-color:var(--accent)}

/* Queues */
.queue-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:24px}
.queue-card{background:var(--surface);border:1px solid var(--border-subtle);border-radius:var(--radius);padding:14px}
.queue-card h4{font-size:12px;font-weight:600;margin-bottom:8px;color:var(--text-muted)}
.queue-card .nums{font-size:28px;font-weight:700;font-family:var(--font-mono)}
.queue-card .sub{font-size:11px;color:var(--text-faint);margin-top:4px}

@media(max-width:768px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .event-row{grid-template-columns:1fr;gap:2px}
  .detail-panel{width:100%}
}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <h1><span class="logo">⚙ Factory OS</span></h1>
    <span style="color:var(--text-faint);font-size:12px" id="clock"></span>
    <span style="color:var(--text-faint);font-size:12px;margin-left:auto" id="auto-refresh">↻ 5s</span>
  </header>

  <nav class="sidebar">
    <div class="nav-section">Views</div>
    <div class="nav-item active" data-view="overview">
      <div class="nav-dot" style="background:var(--accent)"></div>Overview
    </div>
    <div class="nav-item" data-view="tree">
      <div class="nav-dot" style="background:var(--purple)"></div>Task Tree
    </div>
    <div class="nav-item" data-view="pipeline">
      <div class="nav-dot" style="background:var(--green)"></div>Atom Pipeline
    </div>
    <div class="nav-item" data-view="events">
      <div class="nav-dot" style="background:var(--yellow)"></div>Event Log
    </div>
    <div class="nav-item" data-view="queues">
      <div class="nav-dot" style="background:var(--teal)"></div>Queues
    </div>
    <div class="nav-section" style="margin-top:16px">Quick Filters</div>
    <div class="nav-item" data-filter="blocked">
      <div class="nav-dot" style="background:var(--red)"></div>Blocked
    </div>
    <div class="nav-item" data-filter="human">
      <div class="nav-dot" style="background:var(--orange)"></div>Needs Human
    </div>
    <div class="nav-item" data-filter="errors">
      <div class="nav-dot" style="background:var(--red)"></div>Errors (24h)
    </div>
  </nav>

  <main class="main" id="content"></main>
</div>

<div class="detail-panel" id="detail-panel">
  <button class="close-btn" onclick="closeDetail()">✕</button>
  <div id="detail-content"></div>
</div>

<script>
const API = '';
let currentView = 'overview';
let refreshTimer;

// ── Navigation ─────────────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-view]').forEach(el => {
  el.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
    currentView = el.dataset.view;
    loadView(currentView);
  });
});

document.querySelectorAll('.nav-item[data-filter]').forEach(el => {
  el.addEventListener('click', () => {
    currentView = 'events';
    loadView('events', {filter: el.dataset.filter});
  });
});

// ── Auto-refresh ───────────────────────────────────────────────────────
function startRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => loadView(currentView, {silent: true}), 5000);
}

// ── Views ──────────────────────────────────────────────────────────────
async function loadView(view, opts = {}) {
  const content = document.getElementById('content');
  if (!opts.silent) content.style.opacity = '0.6';

  try {
    switch(view) {
      case 'overview': await renderOverview(content); break;
      case 'tree': await renderTree(content); break;
      case 'pipeline': await renderPipeline(content); break;
      case 'events': await renderEvents(content, opts); break;
      case 'queues': await renderQueues(content); break;
    }
  } catch(e) {
    content.innerHTML = `<p style="color:var(--red)">Error: ${e.message}. Is the server running?</p>`;
  }
  content.style.opacity = '1';
}

async function renderOverview(el) {
  const [stats, pipeline, events] = await Promise.all([
    fetch(API+'/api/stats').then(r=>r.json()),
    fetch(API+'/api/pipeline').then(r=>r.json()),
    fetch(API+'/api/events?limit=20').then(r=>r.json()),
  ]);

  const statCards = Object.entries(stats.by_status||{})
    .map(([k,v]) => `<div class="stat-card"><div class="label">${k}</div><div class="value">${v}</div></div>`)
    .join('');

  const inProgress = pipeline.filter(p => p.status === 'in_progress').length;
  const inReview = pipeline.filter(p => p.status === 'in_review').length;

  el.innerHTML = `
    <h2 style="font-size:18px;font-weight:700;margin-bottom:16px">System Overview</h2>
    <div class="stats-bar">
      <div class="stat-card"><div class="label">In Progress</div><div class="value" style="color:var(--green)">${inProgress}</div></div>
      <div class="stat-card"><div class="label">In Review</div><div class="value" style="color:var(--yellow)">${inReview}</div></div>
      <div class="stat-card"><div class="label">Needs Human</div><div class="value" style="color:${stats.needs_human?'var(--red)':'var(--text-faint)'}">${stats.needs_human||0}</div></div>
      <div class="stat-card"><div class="label">Active Locks</div><div class="value">${stats.active_locks||0}</div></div>
      <div class="stat-card"><div class="label">Runs (24h)</div><div class="value">${stats.runs_24h||0}</div></div>
      <div class="stat-card"><div class="label">Errors (24h)</div><div class="value" style="color:${stats.errors_24h?'var(--red)':'var(--text-faint)'}">${stats.errors_24h||0}</div></div>
    </div>
    <h3 style="font-size:14px;font-weight:600;margin-bottom:12px">Status Distribution</h3>
    <div class="stats-bar" style="margin-bottom:24px">${statCards}</div>
    <h3 style="font-size:14px;font-weight:600;margin-bottom:12px">Recent Events</h3>
    <div class="event-log">${events.map(renderEventRow).join('')}</div>
  `;
}

async function renderTree(el) {
  const tree = await fetch(API+'/api/tree').then(r=>r.json());
  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2 style="font-size:18px;font-weight:700">Task Tree</h2>
      <input type="text" placeholder="Search..." oninput="filterTree(this.value)">
    </div>
    <div id="tree-container">${tree.map(n => renderTreeNode(n, true)).join('')}</div>
  `;
}

function renderTreeNode(node, expanded=false) {
  const hasChildren = node.children && node.children.length > 0;
  const toggle = hasChildren 
    ? `<span class="tree-toggle ${expanded?'open':''}" onclick="toggleNode(this,event)">▶</span>`
    : `<span class="tree-toggle"></span>`;

  const children = hasChildren 
    ? `<div class="children" style="${expanded?'':'display:none'}">${node.children.map(c => renderTreeNode(c, false)).join('')}</div>`
    : '';

  return `<div class="tree-node">
    <div class="tree-item" onclick="openDetail('${node.id}')">
      ${toggle}
      <span class="tree-kind ${node.kind}">${node.kind}</span>
      <span class="badge ${node.status}">${node.status}</span>
      <span class="tree-title">${esc(node.title)}</span>
      <span class="tree-meta">
        ${node.file_count?`📄${node.file_count}`:''}
        ${node.comment_count?`💬${node.comment_count}`:''}
        ${node.child_count?`↳${node.child_count}`:''}
      </span>
    </div>
    ${children}
  </div>`;
}

async function renderPipeline(el) {
  const atoms = await fetch(API+'/api/pipeline').then(r=>r.json());
  const columns = {};
  const order = ['in_progress','in_review','ready_for_work','ready_for_judge','review_rejected','judge_rejected','blocked','planned','draft','done','cancelled'];

  atoms.forEach(a => {
    if (!columns[a.status]) columns[a.status] = [];
    columns[a.status].push(a);
  });

  const cols = order.filter(s => columns[s]).map(status => {
    const items = columns[status];
    const cards = items.map(a => `
      <div class="pipeline-card" onclick="openDetail('${a.id}')">
        <div class="title">${esc(a.title)}</div>
        <div class="meta">
          ${a.files?`📄${a.files.split('|').length}`:''}
          ${a.active_locks?`🔒${a.active_locks}`:''}
          ${a.forge_attempts?`⚒${a.forge_attempts}`:''}
          ${a.review_rejections?`✗${a.review_rejections}`:''}
          ${a.needs_human_review?'🚨':''}
          ${a.lease_owner?`👤${a.lease_owner}`:''}
        </div>
      </div>
    `).join('');

    return `<div class="pipeline-col">
      <div class="pipeline-col-header">
        <h3><span class="badge ${status}">${status}</span></h3>
        <span class="count">${items.length}</span>
      </div>
      ${cards}
    </div>`;
  }).join('');

  el.innerHTML = `
    <h2 style="font-size:18px;font-weight:700;margin-bottom:16px">Atom Pipeline</h2>
    <div class="pipeline">${cols || '<p style="color:var(--text-faint)">No atoms yet.</p>'}</div>
  `;
}

async function renderEvents(el, opts={}) {
  let url = API+'/api/events?limit=500';
  if (opts.filter === 'errors') url += '&severity=error';

  const events = await fetch(url).then(r=>r.json());
  let filtered = events;
  if (opts.filter === 'blocked') filtered = events.filter(e => e.event_type.includes('block'));
  if (opts.filter === 'human') filtered = events.filter(e => e.message.includes('human') || e.message.includes('needs_human'));

  const severities = ['all','debug','info','warn','error','fatal'];

  el.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2 style="font-size:18px;font-weight:700">Event Log</h2>
      <div class="filters">
        ${severities.map(s => `<button class="filter-btn ${s==='all'?'active':''}" onclick="filterEvents('${s}',this)">${s}</button>`).join('')}
        <input type="text" placeholder="Filter by type..." oninput="filterEventText(this.value)">
      </div>
    </div>
    <div class="event-log" id="event-list">
      <div class="event-row" style="font-weight:600;color:var(--text-faint);border-bottom:1px solid var(--border)">
        <span>Time</span><span>Sev</span><span>Type</span><span>Message</span>
      </div>
      ${filtered.map(renderEventRow).join('')}
    </div>
  `;
}

function renderEventRow(e) {
  const time = e.event_time ? e.event_time.replace('T',' ').slice(0,19) : '—';
  return `<div class="event-row" data-severity="${e.severity}" data-type="${e.event_type}" 
    ${e.work_item_id ? `onclick="openDetail('${e.work_item_id}')" style="cursor:pointer"` : ''}>
    <span class="event-time">${time}</span>
    <span class="event-severity ${e.severity}">${e.severity}</span>
    <span class="event-type">${e.event_type}</span>
    <span class="event-msg">${esc(e.message)}</span>
  </div>`;
}

async function renderQueues(el) {
  const queues = await fetch(API+'/api/queues').then(r=>r.json());
  const grouped = {};
  queues.forEach(q => {
    if (!grouped[q.queue_name]) grouped[q.queue_name] = [];
    grouped[q.queue_name].push(q);
  });

  const cards = Object.entries(grouped).map(([name, items]) => {
    const leased = items.filter(i => i.lease_owner).length;
    const itemList = items.map(i => `
      <div class="pipeline-card" onclick="openDetail('${i.work_item_id}')">
        <div class="title">${esc(i.title)}</div>
        <div class="meta">
          <span>${i.kind}</span>
          <span>pri:${i.priority}</span>
          ${i.lease_owner ? `<span>👤${i.lease_owner}</span>` : ''}
          <span>att:${i.attempts}</span>
        </div>
      </div>
    `).join('');

    return `<div class="pipeline-col">
      <div class="pipeline-col-header">
        <h3>${name.replace('_',' ')}</h3>
        <span class="count">${items.length} (${leased} leased)</span>
      </div>
      ${itemList || '<div style="padding:12px;color:var(--text-faint);font-size:12px">Empty</div>'}
    </div>`;
  }).join('');

  el.innerHTML = `
    <h2 style="font-size:18px;font-weight:700;margin-bottom:16px">Agent Queues</h2>
    <div class="pipeline">${cards || '<p style="color:var(--text-faint)">All queues empty.</p>'}</div>
  `;
}

// ── Detail panel ───────────────────────────────────────────────────────
async function openDetail(id) {
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');

  try {
    const data = await fetch(API+`/api/item/${id}`).then(r=>r.json());

    const fields = [
      ['Kind', data.kind], ['Status', `<span class="badge ${data.status}">${data.status}</span>`],
      ['Owner', data.owner_role||'—'], ['Creator', data.creator_role],
      ['Priority', data.priority], ['Planning Depth', data.planning_depth],
      ['Forge Attempts', data.forge_attempts], ['Review Rejections', data.review_rejections],
      ['Judge Rejections', data.judge_rejections], ['Needs Human', data.needs_human_review?'⚠ YES':'No'],
    ].map(([k,v]) => `<div class="field"><span class="k">${k}</span><span>${v}</span></div>`).join('');

    const files = (data.files||[]).map(f => 
      `<div style="font-size:12px;font-family:var(--font-mono);padding:2px 0">
        <span style="color:var(--teal)">${f.intent}</span> ${f.path}
      </div>`
    ).join('') || '<span style="color:var(--text-faint)">None</span>';

    const comments = (data.comments||[]).map(c => `
      <div class="comment-item">
        <div class="header">
          <span><strong>${c.author_role}</strong> · ${c.comment_type}</span>
          <span>${(c.created_at||'').slice(0,16)}</span>
        </div>
        <div>${esc(c.body).slice(0,500)}</div>
      </div>
    `).join('') || '<span style="color:var(--text-faint)">No comments</span>';

    const decisions = (data.decisions||[]).map(d => `
      <div class="comment-item">
        <div class="header">
          <span><strong>${d.decision_role}</strong>: <span class="badge ${d.decision==='approved'?'done':'judge_rejected'}">${d.decision}</span></span>
          <span>${(d.created_at||'').slice(0,16)}</span>
        </div>
        ${d.reason_code ? `<div style="color:var(--text-faint)">Reason: ${d.reason_code}</div>` : ''}
      </div>
    `).join('') || '<span style="color:var(--text-faint)">No decisions</span>';

    const children = (data.children||[]).map(c => `
      <div class="pipeline-card" onclick="openDetail('${c.id}')">
        <div style="display:flex;gap:8px;align-items:center">
          <span class="tree-kind ${c.kind}">${c.kind}</span>
          <span class="badge ${c.status}">${c.status}</span>
          <span class="tree-title">${esc(c.title)}</span>
        </div>
      </div>
    `).join('');

    content.innerHTML = `
      <h2>${esc(data.title)}</h2>
      <p style="color:var(--text-muted);font-size:12px;margin:8px 0 16px;font-family:var(--font-mono)">${data.id}</p>
      ${data.description ? `<p style="margin-bottom:16px;color:var(--text-muted)">${esc(data.description).slice(0,600)}</p>` : ''}
      <div class="detail-section"><h4>Properties</h4>${fields}</div>
      <div class="detail-section"><h4>Files</h4>${files}</div>
      ${children ? `<div class="detail-section"><h4>Children</h4>${children}</div>` : ''}
      <div class="detail-section"><h4>Decisions</h4>${decisions}</div>
      <div class="detail-section"><h4>Comments</h4>${comments}</div>
    `;
    panel.classList.add('open');
  } catch(e) {
    content.innerHTML = `<p style="color:var(--red)">Failed to load: ${e.message}</p>`;
    panel.classList.add('open');
  }
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
}

// ── Utilities ──────────────────────────────────────────────────────────
function esc(s) { 
  if (!s) return ''; 
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); 
}

function toggleNode(el, evt) {
  evt.stopPropagation();
  const children = el.closest('.tree-node').querySelector('.children');
  if (children) {
    const open = children.style.display !== 'none';
    children.style.display = open ? 'none' : 'block';
    el.classList.toggle('open', !open);
  }
}

function filterTree(query) {
  const q = query.toLowerCase();
  document.querySelectorAll('.tree-item').forEach(el => {
    const title = el.querySelector('.tree-title')?.textContent?.toLowerCase() || '';
    const node = el.closest('.tree-node');
    node.style.display = (!q || title.includes(q)) ? '' : 'none';
  });
}

function filterEvents(severity, btn) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('#event-list .event-row[data-severity]').forEach(row => {
    row.style.display = (severity === 'all' || row.dataset.severity === severity) ? '' : 'none';
  });
}

function filterEventText(query) {
  const q = query.toLowerCase();
  document.querySelectorAll('#event-list .event-row[data-type]').forEach(row => {
    const type = row.dataset.type || '';
    const msg = row.querySelector('.event-msg')?.textContent?.toLowerCase() || '';
    row.style.display = (!q || type.includes(q) || msg.includes(q)) ? '' : 'none';
  });
}

// ── Clock ──────────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ru-RU');
}
setInterval(updateClock, 1000);
updateClock();

// ── Init ───────────────────────────────────────────────────────────────
loadView('overview');
startRefresh();

// Close detail on Escape
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });
</script>
</body>
</html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Factory OS Dashboard")
    parser.add_argument("--db", default="factory.db", help="SQLite database path")
    parser.add_argument("--port", type=int, default=8420, help="HTTP port")
    args = parser.parse_args()

    DB_PATH = args.db
    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"Factory OS Dashboard → http://localhost:{args.port}")
    print(f"Database: {args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
