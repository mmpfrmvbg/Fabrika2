"""Auxiliary SQLite DDL fragments used by db migrations."""

MIGRATIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
)
"""

# Migration v2: self-improvement candidates (idempotent CREATE TABLE IF NOT EXISTS)
IMPROVEMENT_CANDIDATES_DDL = """
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
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    reviewed_at   TEXT,
    reviewed_by   TEXT,
    expires_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ic_status ON improvement_candidates(status, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_ic_source ON improvement_candidates(source_type);
"""

ARCHITECT_COMMENTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS architect_comments (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    comment         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
)
"""

ARCHITECT_COMMENTS_INDEX_WI_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_architect_comments_wi "
    "ON architect_comments(work_item_id)"
)

ARCHITECT_COMMENTS_INDEX_TIME_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_architect_comments_time "
    "ON architect_comments(work_item_id, created_at DESC)"
)

JUDGE_AND_REVIEW_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS judge_verdicts (
    id                      TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL REFERENCES runs(id),
    work_item_id            TEXT NOT NULL REFERENCES work_items(id),
    item                    TEXT NOT NULL,
    verdict                 TEXT NOT NULL,
    all_passed              INTEGER NOT NULL,
    next_event              TEXT NOT NULL,
    rejection_reason_code   TEXT,
    checked_guards_json     TEXT NOT NULL DEFAULT '[]',
    failed_guards_json      TEXT,
    context_refs_json       TEXT NOT NULL DEFAULT '[]',
    suggested_action        TEXT,
    payload_json            TEXT NOT NULL,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);
CREATE INDEX IF NOT EXISTS idx_jv_wi ON judge_verdicts(work_item_id);
CREATE INDEX IF NOT EXISTS idx_jv_verdict ON judge_verdicts(verdict);
CREATE INDEX IF NOT EXISTS idx_jv_reason ON judge_verdicts(rejection_reason_code);
CREATE INDEX IF NOT EXISTS idx_jv_created ON judge_verdicts(created_at);
CREATE TABLE IF NOT EXISTS review_results (
    id                  TEXT PRIMARY KEY,
    reviewer_run_id     TEXT NOT NULL REFERENCES runs(id),
    work_item_id        TEXT NOT NULL REFERENCES work_items(id),
    subject_run_id      TEXT NOT NULL,
    item                TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    all_passed          INTEGER NOT NULL,
    next_event          TEXT NOT NULL,
    issues_json         TEXT NOT NULL,
    context_refs_json   TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);
CREATE INDEX IF NOT EXISTS idx_rr_wi ON review_results(work_item_id);
CREATE INDEX IF NOT EXISTS idx_rr_verdict ON review_results(verdict);
CREATE INDEX IF NOT EXISTS idx_rr_subject ON review_results(subject_run_id);
CREATE INDEX IF NOT EXISTS idx_rr_created ON review_results(created_at);
"""
