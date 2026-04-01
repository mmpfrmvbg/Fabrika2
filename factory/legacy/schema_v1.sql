-- ⚠️ DEPRECATED — not used in production. For reference only.
-- See AUDIT_REPORT.md for details.

-- ============================================================================
-- FACTORY OS — Data Contract v1.0
-- SQLite DDL · All timestamps ISO-8601 UTC · Foreign keys enforced
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Migration tracking ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO _migrations(version, name) VALUES (1, 'initial_schema');


-- ============================================================================
-- LAYER 1: TASK HIERARCHY
-- ============================================================================

CREATE TABLE work_items (
    id                  TEXT    PRIMARY KEY,              -- wi_{ulid}
    parent_id           TEXT    REFERENCES work_items(id),
    root_id             TEXT    NOT NULL,                 -- top-level vision id
    kind                TEXT    NOT NULL CHECK (kind IN (
                            'vision','initiative','epic','story','task','atom','atm_change'
                        )),
    title               TEXT    NOT NULL,
    description         TEXT,

    -- ── Primary FSM state ──────────────────────────────────────────────────
    status              TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN (
                            'draft','planned','ready_for_judge','judge_rejected',
                            'ready_for_work','in_progress','in_review',
                            'review_rejected','done','cancelled','blocked','archived'
                        )),

    -- ── Blocking support (fix: explicit resume target) ─────────────────────
    blocked_reason      TEXT,
    blocked_since       TEXT,
    resume_to_status    TEXT    CHECK (resume_to_status IS NULL OR resume_to_status IN (
                            'draft','planned','ready_for_judge','judge_rejected',
                            'ready_for_work','in_progress','in_review','review_rejected'
                        )),

    -- ── Parallel sub-states (fix: avoid phantom statuses) ──────────────────
    -- JSON object, e.g. {"awaiting_architect_note":true,"awaiting_hr_review":false}
    sub_states          TEXT    NOT NULL DEFAULT '{}',

    -- ── Ownership & routing ────────────────────────────────────────────────
    priority            INTEGER NOT NULL DEFAULT 100,     -- lower = higher priority
    creator_role        TEXT    NOT NULL,
    owner_role          TEXT,                              -- who holds the ball right now
    origin_work_item_id TEXT    REFERENCES work_items(id), -- if re-derived / reformulated

    -- ── Cached latest decision refs (denormalized for dashboard speed) ─────
    judge_decision_id       TEXT,
    reviewer_decision_id    TEXT,
    architect_decision_id   TEXT,

    -- ── Planning metadata ──────────────────────────────────────────────────
    planning_depth      INTEGER NOT NULL DEFAULT 0,       -- decomposition iterations so far
    max_planning_depth  INTEGER NOT NULL DEFAULT 3,       -- hard cap → needs_human_review
    atomicity_score     REAL,
    needs_human_review  INTEGER NOT NULL DEFAULT 0,

    -- ── Retry / cycle counters ─────────────────────────────────────────────
    forge_attempts      INTEGER NOT NULL DEFAULT 0,
    review_rejections   INTEGER NOT NULL DEFAULT 0,
    judge_rejections    INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_wi_parent   ON work_items(parent_id);
CREATE INDEX idx_wi_root     ON work_items(root_id);
CREATE INDEX idx_wi_status   ON work_items(status);
CREATE INDEX idx_wi_owner    ON work_items(owner_role);
CREATE INDEX idx_wi_kind_st  ON work_items(kind, status);

CREATE TRIGGER trg_wi_updated AFTER UPDATE ON work_items
BEGIN
    UPDATE work_items SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    WHERE id = NEW.id;
END;


-- ============================================================================
-- LAYER 2: LINKS, QUEUES, FILES
-- ============================================================================

CREATE TABLE work_item_links (
    id          TEXT PRIMARY KEY,
    src_id      TEXT NOT NULL REFERENCES work_items(id),
    dst_id      TEXT NOT NULL REFERENCES work_items(id),
    link_type   TEXT NOT NULL CHECK (link_type IN (
                    'depends_on','blocks','duplicates','relates_to',
                    'derived_from','supersedes'
                )),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(src_id, dst_id, link_type)
);

-- ── Queue: one item lives in exactly one queue at a time (conscious constraint) ─
CREATE TABLE work_item_queue (
    work_item_id TEXT PRIMARY KEY REFERENCES work_items(id),
    queue_name   TEXT    NOT NULL CHECK (queue_name IN (
                     'planner_inbox','architect_inbox','judge_inbox',
                     'forge_inbox','review_inbox','hr_inbox'
                 )),
    available_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    lease_owner  TEXT,                                   -- agent_id that holds the lease
    lease_until  TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    last_error   TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_wiq_queue ON work_item_queue(queue_name, available_at);

-- ── Files: declared scope per work item ────────────────────────────────────
CREATE TABLE work_item_files (
    id           TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items(id),
    path         TEXT NOT NULL,
    intent       TEXT NOT NULL CHECK (intent IN ('read','modify','create','delete','rename')),
    required     INTEGER NOT NULL DEFAULT 1,
    UNIQUE(work_item_id, path, intent)
);

CREATE TABLE file_locks (
    path           TEXT PRIMARY KEY,
    work_item_id   TEXT NOT NULL REFERENCES work_items(id),
    run_id         TEXT,
    lock_reason    TEXT NOT NULL,
    acquired_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at     TEXT,
    released_at    TEXT
);

CREATE TABLE file_changes (
    id             TEXT PRIMARY KEY,
    work_item_id   TEXT NOT NULL REFERENCES work_items(id),
    run_id         TEXT NOT NULL,
    path           TEXT NOT NULL,
    change_type    TEXT NOT NULL CHECK (change_type IN ('modified','created','deleted','renamed')),
    old_hash       TEXT,
    new_hash       TEXT,
    diff_ref       TEXT,                                  -- URI to stored diff
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


-- ============================================================================
-- LAYER 3: COMMENTS & DECISIONS
-- ============================================================================

CREATE TABLE comments (
    id                 TEXT PRIMARY KEY,
    work_item_id       TEXT NOT NULL REFERENCES work_items(id),
    author_role        TEXT NOT NULL CHECK (author_role IN (
                           'creator','planner','architect','judge',
                           'reviewer','forge','hr','system'
                       )),
    author_agent_id    TEXT,
    comment_type       TEXT NOT NULL CHECK (comment_type IN (
                           'note','analysis','decision','rejection',
                           'instruction','summary','context_query'
                       )),
    body               TEXT NOT NULL,
    structured_payload TEXT,                               -- JSON for machine-readable parts
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_comments_wi ON comments(work_item_id, created_at);

CREATE TABLE decisions (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    decision_role   TEXT NOT NULL CHECK (decision_role IN (
                        'architect','judge','reviewer','hr'
                    )),
    decision        TEXT NOT NULL CHECK (decision IN (
                        'approved','rejected','needs_changes','deferred'
                    )),
    reason_code     TEXT,                                  -- e.g. conflict/quality/scope/security
    comment_id      TEXT REFERENCES comments(id),
    run_id          TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_decisions_wi ON decisions(work_item_id, created_at);


-- ============================================================================
-- LAYER 4: AGENTS, RUNS, STEPS
-- ============================================================================

CREATE TABLE agents (
    id               TEXT    PRIMARY KEY,
    role             TEXT    NOT NULL CHECK (role IN (
                         'creator','planner','architect','judge',
                         'reviewer','forge','hr','orchestrator'
                     )),
    model_name       TEXT,
    prompt_version   TEXT,
    config           TEXT,                                 -- JSON: temperature, max_tokens, etc.
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE runs (
    id               TEXT PRIMARY KEY,                     -- run_{ulid}
    work_item_id     TEXT REFERENCES work_items(id),
    agent_id         TEXT NOT NULL REFERENCES agents(id),
    role             TEXT NOT NULL,
    run_type         TEXT NOT NULL CHECK (run_type IN (
                         'plan','decompose','analyze','judge','implement',
                         'review','repair','hr_audit','orchestrate','context_build'
                     )),
    status           TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                         'queued','running','waiting_input','completed',
                         'failed','cancelled','timed_out'
                     )),
    started_at       TEXT,
    finished_at      TEXT,
    parent_run_id    TEXT REFERENCES runs(id),
    correlation_id   TEXT,                                 -- ties a chain of retries / sub-runs
    sandbox_ref      TEXT,                                 -- isolated env id
    git_branch       TEXT,
    commit_sha       TEXT,
    error_summary    TEXT,
    token_usage      TEXT,                                 -- JSON: {prompt_tokens, completion_tokens, cost}
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_runs_wi     ON runs(work_item_id);
CREATE INDEX idx_runs_status ON runs(status);
CREATE INDEX idx_runs_corr   ON runs(correlation_id);

-- ── Run steps: low-level trace of a single run ────────────────────────────
-- CONTRACT: run_steps = technical trace of ONE run.
--           event_log = domain-level events across the WHOLE factory.
--           Rule: every FSM transition → event_log entry (not necessarily a run_step).
--           A run_step MAY also produce an event_log entry (e.g. file_write, error).
CREATE TABLE run_steps (
    id               TEXT    PRIMARY KEY,
    run_id           TEXT    NOT NULL REFERENCES runs(id),
    step_no          INTEGER NOT NULL,
    step_kind        TEXT    NOT NULL CHECK (step_kind IN (
                         'prompt','llm_reply','tool_call','tool_result',
                         'decision','file_write','file_read','test',
                         'git','error','checkpoint'
                     )),
    status           TEXT    NOT NULL DEFAULT 'started' CHECK (status IN (
                         'started','completed','failed','skipped'
                     )),
    -- ── Idempotency (fix: prevent double LLM calls on replay) ──────────────
    idempotency_key  TEXT,                                 -- hash(run_id, step_no) or external request_id
    cached_result    INTEGER NOT NULL DEFAULT 0,           -- 1 if replayed from stored result

    payload          TEXT    NOT NULL,                      -- JSON
    duration_ms      INTEGER,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(run_id, step_no)
);


-- ============================================================================
-- LAYER 5: REVIEW & ARTIFACTS
-- ============================================================================

CREATE TABLE review_checks (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(id),
    check_type   TEXT NOT NULL CHECK (check_type IN (
                     'tests','lint','security','architecture','policy','type_check'
                 )),
    status       TEXT NOT NULL CHECK (status IN (
                     'passed','failed','warning','skipped'
                 )),
    is_blocking  INTEGER NOT NULL DEFAULT 1,               -- fix: explicit merge-blocking flag
    summary      TEXT,
    details      TEXT,                                      -- JSON
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_rc_run ON review_checks(run_id);

CREATE TABLE artifacts (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(id),
    work_item_id   TEXT REFERENCES work_items(id),
    artifact_type  TEXT NOT NULL CHECK (artifact_type IN (
                       'patch','log','report','binary','test_result',
                       'prompt_snapshot','diff','context_snapshot'
                   )),
    uri            TEXT NOT NULL,                           -- file path or git blob ref
    content_hash   TEXT,
    size_bytes     INTEGER,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


-- ============================================================================
-- LAYER 6: EVENT LOG (append-only domain journal)
-- ============================================================================
-- CONTRACT: This is the single source of truth for "how we got here".
--           Every FSM transition, every role action, every system event.
--           Судья reads context_snapshots + targeted event_log queries, not raw full scan.
--           Retention policy: events older than N days → archived to external storage,
--           but NEVER deleted from this table without explicit migration.

CREATE TABLE event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    event_type      TEXT    NOT NULL,                      -- canonical event name
    entity_type     TEXT    NOT NULL,                      -- work_item | run | file | decision | prompt | system
    entity_id       TEXT    NOT NULL,
    parent_event_id INTEGER REFERENCES event_log(id),
    run_id          TEXT    REFERENCES runs(id),
    work_item_id    TEXT    REFERENCES work_items(id),
    actor_role      TEXT,
    actor_id        TEXT,
    severity        TEXT    NOT NULL DEFAULT 'info' CHECK (severity IN (
                        'debug','info','warn','error','fatal'
                    )),
    message         TEXT    NOT NULL,
    payload         TEXT,                                   -- JSON
    -- ── Correlation for tracing ────────────────────────────────────────────
    correlation_id  TEXT                                    -- same as runs.correlation_id
);

CREATE INDEX idx_el_entity   ON event_log(entity_type, entity_id);
CREATE INDEX idx_el_run      ON event_log(run_id);
CREATE INDEX idx_el_wi       ON event_log(work_item_id);
CREATE INDEX idx_el_time     ON event_log(event_time);
CREATE INDEX idx_el_type     ON event_log(event_type);
CREATE INDEX idx_el_corr     ON event_log(correlation_id);


-- ============================================================================
-- LAYER 7: CONTEXT SNAPSHOTS & PROMPT VERSIONS
-- ============================================================================

CREATE TABLE context_snapshots (
    id               TEXT PRIMARY KEY,
    work_item_id     TEXT REFERENCES work_items(id),
    run_id           TEXT REFERENCES runs(id),
    snapshot_kind    TEXT NOT NULL CHECK (snapshot_kind IN (
                         'judge_context','planner_context','architect_context','hr_context'
                     )),
    summary          TEXT NOT NULL,
    -- ── Stable references into event_log (fix: not just "latest N") ────────
    source_event_from INTEGER NOT NULL REFERENCES event_log(id),
    source_event_to   INTEGER NOT NULL REFERENCES event_log(id),
    event_count       INTEGER NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE prompt_versions (
    id             TEXT    PRIMARY KEY,
    role           TEXT    NOT NULL,
    version        TEXT    NOT NULL,
    content_ref    TEXT    NOT NULL,                        -- git blob SHA or file URI
    content_hash   TEXT    NOT NULL,                        -- SHA-256 of actual content
    created_by     TEXT    NOT NULL,                        -- agent_id or 'creator'
    approved_by    TEXT,                                    -- decision_id from judge
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    active         INTEGER NOT NULL DEFAULT 0,
    UNIQUE(role, version)
);


-- ============================================================================
-- LAYER 8: FSM TRANSITION TABLE (inspectable, versionable)
-- ============================================================================

CREATE TABLE state_transitions (
    id           TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL DEFAULT 'work_item',
    kind_filter  TEXT,                                      -- NULL = applies to all kinds
    from_state   TEXT NOT NULL,
    event_name   TEXT NOT NULL,                             -- canonical event that triggers transition
    to_state     TEXT NOT NULL,
    guard_name   TEXT,                                      -- Python function name
    action_name  TEXT,                                      -- post-transition action
    description  TEXT,
    UNIQUE(entity_type, kind_filter, from_state, event_name)
);


-- ============================================================================
-- VIEWS: Dashboard helpers
-- ============================================================================

CREATE VIEW v_active_work AS
SELECT
    wi.*,
    wiq.queue_name,
    wiq.lease_owner,
    wiq.attempts,
    (SELECT COUNT(*) FROM work_item_files wf WHERE wf.work_item_id = wi.id) AS file_count,
    (SELECT COUNT(*) FROM work_item_links wl WHERE wl.src_id = wi.id AND wl.link_type = 'depends_on'
        AND wl.dst_id IN (SELECT id FROM work_items WHERE status NOT IN ('done','cancelled','archived'))
    ) AS unresolved_deps
FROM work_items wi
LEFT JOIN work_item_queue wiq ON wiq.work_item_id = wi.id
WHERE wi.status NOT IN ('done','cancelled','archived');

CREATE VIEW v_atom_pipeline AS
SELECT
    wi.id, wi.title, wi.status, wi.owner_role,
    wi.forge_attempts, wi.review_rejections, wi.judge_rejections,
    wi.priority, wi.root_id,
    (SELECT GROUP_CONCAT(wf.path, ', ') FROM work_item_files wf WHERE wf.work_item_id = wi.id) AS files,
    (SELECT COUNT(*) FROM file_locks fl WHERE fl.work_item_id = wi.id AND fl.released_at IS NULL) AS active_locks,
    (SELECT MAX(r.created_at) FROM runs r WHERE r.work_item_id = wi.id) AS last_run_at
FROM work_items wi
WHERE wi.kind = 'atom'
  AND wi.status NOT IN ('done','cancelled','archived')
ORDER BY wi.priority, wi.created_at;

-- ============================================================================
-- v2: Self-improvement candidates (also applied via factory/db.py migration)
-- ============================================================================

CREATE TABLE IF NOT EXISTS improvement_candidates (
    id            TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL CHECK (source_type IN (
        'failure_cluster','review_pattern','judge_pattern',
        'metric_anomaly','hr_proposal','retry_hotspot',
        'manual'
    )),
    source_ref    TEXT,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL,
    evidence      TEXT NOT NULL,
    fix_target    TEXT NOT NULL CHECK (fix_target IN (
        'code','prompt','policy','infra','process'
    )),
    affected_role TEXT,
    affected_files TEXT,
    frequency     INTEGER NOT NULL DEFAULT 1,
    severity_score REAL NOT NULL DEFAULT 0.5,
    impact_score  REAL NOT NULL DEFAULT 0.5,
    confidence    REAL NOT NULL DEFAULT 0.5,
    priority_score REAL GENERATED ALWAYS AS (
        severity_score * 0.4 + impact_score * 0.3 + confidence * 0.2
        + MIN(frequency / 10.0, 1.0) * 0.1
    ) STORED,
    status        TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
        'proposed','approved','rejected','converted','expired'
    )),
    risk_level    TEXT NOT NULL DEFAULT 'low' CHECK (risk_level IN (
        'low','medium','high'
    )),
    vision_id     TEXT REFERENCES work_items(id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    reviewed_at   TEXT,
    reviewed_by   TEXT,
    expires_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_ic_status ON improvement_candidates(status, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_ic_source ON improvement_candidates(source_type);
