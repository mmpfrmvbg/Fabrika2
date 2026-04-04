"""SQLite DDL: tables, views, FSM seed data."""
from .config import MAX_ATOM_RETRIES

DDL = """
-- ═══════════════════════════════════════════════════
--  FACTORY DB SCHEMA v0.1.0
--  Единая база: иерархия задач + лог + оркестратор
-- ═══════════════════════════════════════════════════

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;


-- ───────────────────────────────────────────────────
--  1. АГЕНТЫ И АККАУНТЫ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    role            TEXT NOT NULL,
    model_name      TEXT,
    prompt_version  TEXT,
    config_json     TEXT,           -- доп. конфигурация агента
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE TABLE IF NOT EXISTS api_accounts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key         TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'qwen_code_cli',
    daily_limit     INTEGER NOT NULL DEFAULT 3000,
    reset_hour_utc  INTEGER NOT NULL DEFAULT 0,     -- час UTC сброса счётчика (0 = полночь)
    active          INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 0,     -- чем меньше, тем раньше используется
    account_status  TEXT NOT NULL DEFAULT 'active',  -- active|cooling_down|exhausted|disabled (runtime)
    last_error      TEXT,
    cooldown_until  TEXT,                            -- UTC ISO; после истечения → active
    last_used_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE TABLE IF NOT EXISTS api_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL REFERENCES api_accounts(id),
    run_id          TEXT REFERENCES runs(id),
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    request_count   INTEGER NOT NULL DEFAULT 1,
    model_name      TEXT,
    latency_ms      INTEGER,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_api_usage_account_day
    ON api_usage(account_id, created_at);


-- ───────────────────────────────────────────────────
--  2. ИЕРАРХИЯ ЗАДАЧ (work_items)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS work_items (
    id                    TEXT PRIMARY KEY,
    parent_id             TEXT REFERENCES work_items(id),
    root_id               TEXT NOT NULL,                     -- верхний vision
    kind                  TEXT NOT NULL,
    title                 TEXT NOT NULL,
    description           TEXT,
    acceptance_criteria   TEXT,           -- JSON-массив критериев приёмки
    status                TEXT NOT NULL DEFAULT 'draft',
    previous_status       TEXT,          -- для возврата из blocked
    priority              INTEGER NOT NULL DEFAULT 100,
    creator_role          TEXT NOT NULL,
    owner_role            TEXT,          -- кому сейчас "мяч"
    assigned_agent_id     TEXT REFERENCES agents(id),
    origin_work_item_id   TEXT REFERENCES work_items(id),  -- если переформулирована
    planning_depth        INTEGER NOT NULL DEFAULT 0,
    retry_count           INTEGER NOT NULL DEFAULT 0,
    max_retries           INTEGER NOT NULL DEFAULT """ + str(MAX_ATOM_RETRIES) + """,
    needs_human_review    INTEGER NOT NULL DEFAULT 0,
    estimated_complexity  TEXT,          -- simple / medium / complex
    tags                  TEXT,          -- JSON-массив тегов
    metadata              TEXT,          -- JSON произвольный
    last_heartbeat_at     TIMESTAMP,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_wi_parent     ON work_items(parent_id);
CREATE INDEX IF NOT EXISTS idx_wi_root       ON work_items(root_id);
CREATE INDEX IF NOT EXISTS idx_wi_status     ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_wi_owner      ON work_items(owner_role);
CREATE INDEX IF NOT EXISTS idx_wi_kind       ON work_items(kind);
CREATE INDEX IF NOT EXISTS idx_wi_priority   ON work_items(status, priority);

CREATE TRIGGER IF NOT EXISTS trg_wi_updated
    AFTER UPDATE ON work_items
    BEGIN
        UPDATE work_items SET updated_at = strftime('%Y-%m-%dT%H:%M:%f','now')
        WHERE id = NEW.id;
    END;


-- ───────────────────────────────────────────────────
--  3. СВЯЗИ МЕЖДУ ЗАДАЧАМИ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS work_item_links (
    id          TEXT PRIMARY KEY,
    src_id      TEXT NOT NULL REFERENCES work_items(id),
    dst_id      TEXT NOT NULL REFERENCES work_items(id),
    link_type   TEXT NOT NULL,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    UNIQUE(src_id, dst_id, link_type)
);

CREATE INDEX IF NOT EXISTS idx_wil_src ON work_item_links(src_id);
CREATE INDEX IF NOT EXISTS idx_wil_dst ON work_item_links(dst_id);


-- ───────────────────────────────────────────────────
--  4. ОЧЕРЕДЬ ЗАДАЧ (lease-based)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS work_item_queue (
    work_item_id  TEXT PRIMARY KEY REFERENCES work_items(id),
    queue_name    TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 100,
    available_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    lease_owner   TEXT,               -- agent_id, который взял задачу
    lease_until   TEXT,               -- дедлайн lease
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT """ + str(MAX_ATOM_RETRIES) + """,
    last_error    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_wiq_queue
    ON work_item_queue(queue_name, available_at);
CREATE INDEX IF NOT EXISTS idx_wiq_lease
    ON work_item_queue(lease_owner, lease_until);


-- ───────────────────────────────────────────────────
--  5. КОММЕНТАРИИ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS comments (
    id                  TEXT PRIMARY KEY,
    work_item_id        TEXT NOT NULL REFERENCES work_items(id),
    author_role         TEXT NOT NULL,
    author_agent_id     TEXT REFERENCES agents(id),
    comment_type        TEXT NOT NULL,
    body                TEXT NOT NULL,
    structured_payload  TEXT,           -- JSON
    parent_comment_id   TEXT REFERENCES comments(id),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_comments_wi ON comments(work_item_id);


-- ───────────────────────────────────────────────────
--  5b. КОММЕНТАРИИ АРХИТЕКТОРА (история по work_item)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS architect_comments (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    comment         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_architect_comments_wi ON architect_comments(work_item_id);
CREATE INDEX IF NOT EXISTS idx_architect_comments_time ON architect_comments(work_item_id, created_at DESC);


-- ───────────────────────────────────────────────────
--  6. РЕШЕНИЯ (DECISIONS)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    run_id          TEXT REFERENCES runs(id),
    decision_role   TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    reason_code     TEXT,               -- conflict / quality / scope / security / other
    explanation     TEXT,
    suggested_fix   TEXT,
    comment_id      TEXT REFERENCES comments(id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_wi ON decisions(work_item_id);


-- ───────────────────────────────────────────────────
--  7. ФАЙЛЫ — ОБЛАСТИ ИЗМЕНЕНИЙ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS work_item_files (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    path            TEXT NOT NULL,
    intent          TEXT NOT NULL,
    description     TEXT,
    required        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(work_item_id, path, intent)
);

CREATE INDEX IF NOT EXISTS idx_wif_wi   ON work_item_files(work_item_id);
CREATE INDEX IF NOT EXISTS idx_wif_path ON work_item_files(path);

CREATE TABLE IF NOT EXISTS file_locks (
    path            TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    run_id          TEXT REFERENCES runs(id),
    lock_reason     TEXT NOT NULL,
    acquired_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    expires_at      TEXT,
    released_at     TEXT
);

CREATE TABLE IF NOT EXISTS file_changes (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT NOT NULL REFERENCES work_items(id),
    run_id          TEXT NOT NULL REFERENCES runs(id),
    path            TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    old_hash        TEXT,
    new_hash        TEXT,
    diff_summary    TEXT,               -- краткое описание изменений
    diff_ref        TEXT,               -- ссылка на полный diff
    lines_added     INTEGER,
    lines_removed   INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_fc_wi  ON file_changes(work_item_id);
CREATE INDEX IF NOT EXISTS idx_fc_run ON file_changes(run_id);


-- ───────────────────────────────────────────────────
--  8. ПРОГОНЫ (RUNS)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    work_item_id    TEXT REFERENCES work_items(id),
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    account_id      TEXT REFERENCES api_accounts(id),
    role            TEXT NOT NULL,
    run_type        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    source_run_id   TEXT REFERENCES runs(id),
    dry_run         INTEGER NOT NULL DEFAULT 0,
    parent_run_id   TEXT REFERENCES runs(id),
    correlation_id  TEXT,               -- связать цепочку прогонов
    sandbox_ref     TEXT,               -- ссылка на изолированное окружение
    git_branch      TEXT,
    commit_sha      TEXT,
    error_summary   TEXT,
    input_payload   TEXT,               -- JSON: что получил агент на вход
    input_hash      TEXT,
    output_payload  TEXT,               -- JSON: что агент вернул
    agent_version   TEXT,
    prompt_version  TEXT,
    model_name_snapshot TEXT,
    model_params_json TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_wi     ON runs(work_item_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_agent  ON runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_corr   ON runs(correlation_id);
CREATE INDEX IF NOT EXISTS idx_runs_input_hash ON runs(input_hash);


-- ───────────────────────────────────────────────────
--  8b. ВЕРДИКТЫ СУДЬИ (JudgeVerdict, STRICT JSON → FSM)
-- ───────────────────────────────────────────────────

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


-- ───────────────────────────────────────────────────
--  8c. РЕЗУЛЬТАТЫ РЕВЬЮ (ReviewResult, STRICT JSON → FSM)
-- ───────────────────────────────────────────────────

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


-- ───────────────────────────────────────────────────
--  9. ШАГИ ПРОГОНОВ (RUN_STEPS)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS run_steps (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    step_no     INTEGER NOT NULL,
    step_kind   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'started',
    summary     TEXT,                   -- краткое описание для UI
    payload     TEXT NOT NULL,          -- JSON полный
    input_hash  TEXT,
    agent_version TEXT,
    duration_ms INTEGER,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    UNIQUE(run_id, step_no)
);

CREATE INDEX IF NOT EXISTS idx_rs_run ON run_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_rs_input_hash ON run_steps(input_hash);


-- ───────────────────────────────────────────────────
--  10. АРТЕФАКТЫ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    work_item_id    TEXT REFERENCES work_items(id),
    artifact_type   TEXT NOT NULL,
    name            TEXT,
    uri             TEXT NOT NULL,
    content_hash    TEXT,
    size_bytes      INTEGER,
    metadata        TEXT,               -- JSON
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_art_run ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_art_wi  ON artifacts(work_item_id);


-- ───────────────────────────────────────────────────
--  11. ПРОВЕРКИ РЕВЬЮЕРА
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS review_checks (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    check_type  TEXT NOT NULL,
    status      TEXT NOT NULL,
    score       REAL,                   -- 0.0 - 1.0 если применимо
    summary     TEXT,
    details     TEXT,                   -- JSON
    is_blocking INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_rc_run ON review_checks(run_id);


-- ───────────────────────────────────────────────────
--  12. КОНТЕКСТНЫЕ СНАПШОТЫ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS context_snapshots (
    id                  TEXT PRIMARY KEY,
    work_item_id        TEXT REFERENCES work_items(id),
    run_id              TEXT REFERENCES runs(id),
    snapshot_kind       TEXT NOT NULL,
    summary             TEXT NOT NULL,
    relevant_events     TEXT,           -- JSON: массив event_id
    source_event_from   INTEGER,
    source_event_to     INTEGER,
    token_count         INTEGER,        -- сколько токенов займёт контекст
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);


-- ───────────────────────────────────────────────────
--  13. ВЕРСИИ ПРОМПТОВ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS prompt_versions (
    id          TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    version     TEXT NOT NULL,
    content     TEXT NOT NULL,           -- полный текст промпта
    content_ref TEXT,                    -- путь к файлу, если хранится отдельно
    diff_from   TEXT REFERENCES prompt_versions(id),
    created_by  TEXT NOT NULL,
    approved_by TEXT,
    active      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);

CREATE INDEX IF NOT EXISTS idx_pv_role ON prompt_versions(role, active);


-- ───────────────────────────────────────────────────
--  14. ТАБЛИЦА ПЕРЕХОДОВ FSM
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS state_transitions (
    id              TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL DEFAULT 'work_item',
    from_state      TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    to_state        TEXT NOT NULL,
    guard_name      TEXT NOT NULL DEFAULT '',  -- '' = нет guard; ветвление по одному событию
    action_name     TEXT,               -- имя пост-действия в коде
    applicable_kinds TEXT,              -- JSON: к каким kind применимо (null = ко всем)
    description     TEXT,
    UNIQUE(entity_type, from_state, event_name, guard_name)
);


-- ───────────────────────────────────────────────────
--  15. ЕДИНЫЙ ЖУРНАЛ СОБЫТИЙ (append-only)
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    parent_event_id INTEGER REFERENCES event_log(id),
    run_id          TEXT REFERENCES runs(id),
    work_item_id    TEXT REFERENCES work_items(id),
    actor_role      TEXT,
    actor_id        TEXT,
    account_id      TEXT REFERENCES api_accounts(id),
    caused_by_type  TEXT,
    caused_by_id    TEXT,
    severity        TEXT NOT NULL DEFAULT 'info',
    message         TEXT NOT NULL,
    payload         TEXT,               -- JSON
    tags            TEXT                -- JSON-массив для быстрого поиска
);

CREATE INDEX IF NOT EXISTS idx_el_entity    ON event_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_el_run       ON event_log(run_id);
CREATE INDEX IF NOT EXISTS idx_el_wi        ON event_log(work_item_id);
CREATE INDEX IF NOT EXISTS idx_el_time      ON event_log(event_time);
CREATE INDEX IF NOT EXISTS idx_el_type      ON event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_el_severity  ON event_log(severity) WHERE severity IN ('warn','error','fatal');
CREATE INDEX IF NOT EXISTS idx_el_account   ON event_log(account_id);
CREATE INDEX IF NOT EXISTS idx_el_caused_by ON event_log(caused_by_type, caused_by_id);


-- ───────────────────────────────────────────────────
--  16. СИСТЕМНЫЕ МЕТРИКИ И СОСТОЯНИЕ
-- ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS system_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);


-- ───────────────────────────────────────────────────
--  17. НАЧАЛЬНЫЕ ДАННЫЕ: ТАБЛИЦА ПЕРЕХОДОВ FSM
-- ───────────────────────────────────────────────────

-- === ВЕРХНЕУРОВНЕВЫЕ ЗАДАЧИ (vision → story) ===

INSERT OR IGNORE INTO state_transitions VALUES
    ('st_01','work_item','draft','creator_submitted','planned',
     '','action_notify_planner',NULL,
     'Создатель отправил задачу — переходит к планировщику'),

    ('st_02','work_item','draft','architect_submitted','ready_for_judge',
     '','action_notify_judge','["epic","story","task","atom","atm_change"]',
     'Архитектор создал задачу — на ревью судье'),

    ('st_03','work_item','planned','planner_decomposed','planned',
     'guard_has_children',NULL,NULL,
     'Планировщик создал подзадачи — остаётся planned'),

    ('st_04','work_item','planned','ready_for_review','ready_for_judge',
     'guard_has_architect_comment',NULL,NULL,
     'Задача готова к ревью судьи'),

    ('st_05','work_item','ready_for_judge','judge_approved','ready_for_work',
     'guard_ready_for_forge','action_enqueue_forge','["atom","atm_change"]',
     'Судья одобрил атом — в кузницу (нужны файлы и отсутствует успешный forge)'),

    ('st_06','work_item','ready_for_judge','judge_approved_for_planning','planned',
     '','action_notify_planner','["vision","initiative","epic","story","task"]',
     'Судья одобрил концепцию — на декомпозицию'),

    ('st_07','work_item','ready_for_judge','judge_rejected','judge_rejected',
     '','action_return_to_author',NULL,
     'Судья отклонил — возврат автору с комментарием'),

    -- Retry path for atoms AFTER they passed review: judge rejection should go back to forge with feedback.
    ('st_07b','work_item','ready_for_judge','judge_rejected','ready_for_work',
     'guard_has_review_approval','action_enqueue_forge','["atom","atm_change"]',
     'Судья отклонил после ревью — вернуть в кузницу с фидбэком (retry)'),

    -- Retry path for atoms after implementation artifacts exist (e.g. judge sees review_rejected escalation):
    ('st_07c','work_item','ready_for_judge','judge_rejected','ready_for_work',
     'guard_has_file_changes','action_enqueue_forge','["atom","atm_change"]',
     'Судья отклонил после форжа (есть file_changes) — вернуть в кузницу (retry)'),

    ('st_08','work_item','judge_rejected','author_revised','ready_for_judge',
     'guard_has_revision_comment',NULL,NULL,
     'Автор переформулировал — повторно к судье'),

    ('st_09','work_item','judge_rejected','author_cancelled','cancelled',
     '','',NULL,
     'Автор отказался от задачи'),

    ('st_10','work_item','ready_for_work','forge_started','in_progress',
     'guard_files_lockable','action_start_forge_run','["atom","atm_change"]',
     'Кузница начала работу — run + lease в одной транзакции с переходом'),

    ('st_11','work_item','in_progress','forge_completed','in_review',
     '','action_enqueue_reviewer','["atom","atm_change"]',
     'Кузница закончила — на ревью'),

    ('st_12','work_item','in_progress','forge_failed','ready_for_work',
     'guard_can_retry','action_increment_retry',NULL,
     'Кузница упала — повтор если есть попытки'),

    ('st_13','work_item','in_progress','forge_failed','ready_for_judge',
     'guard_over_retry_limit','action_escalate_to_judge',NULL,
     'Кузница упала окончательно — эскалация к судье'),

    ('st_14','work_item','in_review','review_passed','ready_for_judge',
     'guard_all_checks_passed','action_notify_judge','["atom","atm_change"]',
     'Ревью пройдено — к судье на финальное решение'),

    ('st_15','work_item','in_review','review_failed','review_rejected',
     '','action_create_review_comment',NULL,
     'Ревью не пройдено — с комментарием'),

    ('st_16','work_item','review_rejected','sent_to_judge','ready_for_judge',
     '','action_build_judge_context',NULL,
     'Отклонённый ревью идёт к судье для мета-анализа'),

    ('st_18','work_item','ready_for_judge','judge_approved','done',
     'guard_has_review_approval','action_commit_to_git','["atom","atm_change"]',
     'Судья одобрил после ревью — завершение + готовность к коммиту'),

    ('st_17','work_item','done','archive','archived',
     '','',NULL,
     'Завершённая задача уходит в архив'),

    -- === БЛОКИРОВКИ ===

    ('st_20','work_item','ready_for_work','dependency_blocked','blocked',
     '','action_log_block_reason',NULL,
     'Обнаружена неудовлетворённая зависимость'),

    ('st_21','work_item','blocked','dependency_resolved','{previous_status}',
     'guard_all_deps_met','',NULL,
     'Все зависимости удовлетворены — возврат к previous_status'),

    ('st_22','work_item','planned','dependency_blocked','blocked',
     '','action_log_block_reason',NULL,
     'Блокировка на этапе планирования'),

    -- === ОТМЕНА ===

    ('st_30','work_item','draft','cancelled','cancelled',
     '','',NULL,'Отмена из draft'),
    ('st_31','work_item','planned','cancelled','cancelled',
     '','action_cancel_children',NULL,'Отмена из planned — каскад на детей'),
    ('st_32','work_item','ready_for_work','cancelled','cancelled',
     '','action_release_file_locks',NULL,'Отмена из ready_for_work'),

    -- === rollup родителя (очередь completion_inbox) ===

    ('st_40','work_item','*','parent_complete','done',
     'guard_all_children_done','action_propagate_completion',NULL,
     'Все дети завершены — автозавершение родителя');


-- === ФАЗА 2: алиасы событий (те же переходы, канонические имена для агентов) ===

INSERT OR IGNORE INTO state_transitions VALUES
    ('st_50','work_item','draft','planner_assigned','planned',
     '','action_notify_planner',NULL,
     'Фаза 2: алиас creator_submitted / назначение планировщику'),

    ('st_51','work_item','planned','submitted_for_judgment','ready_for_judge',
     'guard_has_architect_comment','',NULL,
     'Фаза 2: алиас ready_for_review — подача на суд'),

    ('st_52','work_item','judge_rejected','reworked','ready_for_judge',
     'guard_has_revision_comment',NULL,NULL,
     'Фаза 2: алиас author_revised — повторная подача'),

    ('st_53','work_item','ready_for_judge','reworked','ready_for_work',
     'guard_has_review_approval','action_enqueue_forge','["atom","atm_change"]',
     'Фаза 2: retry-алиас author_revised после ревью — в кузницу'),

    ('st_54','work_item','ready_for_judge','reworked','ready_for_work',
     'guard_has_file_changes','action_enqueue_forge','["atom","atm_change"]',
     'Фаза 2: retry-алиас author_revised после форжа — в кузницу'),

    ('st_23','work_item','ready_for_judge','judge_deferred','blocked',
     '','action_log_block_reason',NULL,
     'Фаза 2: судья отложил (deferred → blocked)');


-- ───────────────────────────────────────────────────
--  18. НАЧАЛЬНЫЕ ДАННЫЕ: АККАУНТЫ
-- ───────────────────────────────────────────────────
-- (заполняется при init_db)


-- ───────────────────────────────────────────────────
--  19. VIEW: ДАШБОРД — ТЕКУЩЕЕ СОСТОЯНИЕ
-- ───────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS v_dashboard AS
SELECT
    wi.id,
    wi.kind,
    wi.title,
    wi.status,
    wi.priority,
    wi.owner_role,
    wi.retry_count,
    wi.planning_depth,
    wi.created_at,
    wi.updated_at,
    p.title AS parent_title,
    r.title AS root_title,
    (SELECT COUNT(*) FROM work_items c WHERE c.parent_id = wi.id) AS child_count,
    (SELECT COUNT(*) FROM work_items c WHERE c.parent_id = wi.id AND c.status = 'done') AS done_children,
    (SELECT COUNT(*) FROM comments cm WHERE cm.work_item_id = wi.id) AS comment_count,
    (SELECT GROUP_CONCAT(wif.path, ', ')
     FROM work_item_files wif WHERE wif.work_item_id = wi.id) AS files
FROM work_items wi
LEFT JOIN work_items p ON wi.parent_id = p.id
LEFT JOIN work_items r ON wi.root_id = r.id
WHERE wi.status NOT IN ('archived','cancelled')
ORDER BY wi.priority ASC, wi.updated_at DESC;


-- ───────────────────────────────────────────────────
--  20. VIEW: ИСПОЛЬЗОВАНИЕ API
-- ───────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS v_api_usage_today AS
SELECT
    a.id AS account_id,
    a.name AS account_name,
    a.daily_limit,
    COALESCE(u.cnt, 0) AS requests_today,
    a.daily_limit - COALESCE(u.cnt, 0) AS remaining,
    COALESCE(u.total_tokens_in, 0) AS tokens_in_today,
    COALESCE(u.total_tokens_out, 0) AS tokens_out_today,
    a.active,
    a.account_status,
    a.last_error,
    a.cooldown_until,
    a.last_used_at,
    CASE
        WHEN a.active = 0 THEN 'disabled'
        WHEN a.account_status = 'disabled' THEN 'disabled'
        WHEN a.account_status = 'cooling_down'
             AND a.cooldown_until IS NOT NULL
             AND datetime(substr(a.cooldown_until, 1, 19)) > datetime('now') THEN 'cooling_down'
        WHEN a.account_status = 'exhausted' THEN 'exhausted'
        WHEN COALESCE(u.cnt, 0) >= a.daily_limit THEN 'exhausted'
        ELSE 'available'
    END AS availability
FROM api_accounts a
LEFT JOIN (
    SELECT
        account_id,
        COUNT(*) AS cnt,
        SUM(tokens_in) AS total_tokens_in,
        SUM(tokens_out) AS total_tokens_out
    FROM api_usage
    WHERE date(created_at) = date('now')
    GROUP BY account_id
) u ON a.id = u.account_id
ORDER BY a.priority ASC;


-- ───────────────────────────────────────────────────
--  21. VIEW: АКТИВНЫЕ ПРОГОНЫ
-- ───────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS v_active_runs AS
SELECT
    r.id AS run_id,
    r.work_item_id,
    wi.title AS work_item_title,
    r.role,
    r.run_type,
    r.status,
    r.agent_id,
    r.account_id,
    r.started_at,
    (SELECT COUNT(*) FROM run_steps rs WHERE rs.run_id = r.id) AS step_count,
    r.tokens_used
FROM runs r
LEFT JOIN work_items wi ON r.work_item_id = wi.id
WHERE r.status IN ('queued','running','waiting_input')
ORDER BY r.started_at DESC;


-- ───────────────────────────────────────────────────
--  22. VIEW: ОШИБКИ ЗА ПОСЛЕДНИЙ ЧАС
-- ───────────────────────────────────────────────────

CREATE VIEW IF NOT EXISTS v_recent_errors AS
SELECT
    id, event_time, event_type, entity_type, entity_id,
    work_item_id, actor_role, severity, message
FROM event_log
WHERE severity IN ('error','fatal')
  AND event_time > strftime('%Y-%m-%dT%H:%M:%f', 'now', '-1 hour')
ORDER BY event_time DESC;
""";

# Существующая БД: CREATE VIEW IF NOT EXISTS не обновляет определение — пересоздаём в migrate_schema.
V_API_USAGE_TODAY_RECREATE = """
DROP VIEW IF EXISTS v_api_usage_today;
CREATE VIEW v_api_usage_today AS
SELECT
    a.id AS account_id,
    a.name AS account_name,
    a.daily_limit,
    COALESCE(u.cnt, 0) AS requests_today,
    a.daily_limit - COALESCE(u.cnt, 0) AS remaining,
    COALESCE(u.total_tokens_in, 0) AS tokens_in_today,
    COALESCE(u.total_tokens_out, 0) AS tokens_out_today,
    a.active,
    a.account_status,
    a.last_error,
    a.cooldown_until,
    a.last_used_at,
    CASE
        WHEN a.active = 0 THEN 'disabled'
        WHEN a.account_status = 'disabled' THEN 'disabled'
        WHEN a.account_status = 'cooling_down'
             AND a.cooldown_until IS NOT NULL
             AND datetime(substr(a.cooldown_until, 1, 19)) > datetime('now') THEN 'cooling_down'
        WHEN a.account_status = 'exhausted' THEN 'exhausted'
        WHEN COALESCE(u.cnt, 0) >= a.daily_limit THEN 'exhausted'
        ELSE 'available'
    END AS availability
FROM api_accounts a
LEFT JOIN (
    SELECT
        account_id,
        COUNT(*) AS cnt,
        SUM(tokens_in) AS total_tokens_in,
        SUM(tokens_out) AS total_tokens_out
    FROM api_usage
    WHERE date(created_at) = date('now')
    GROUP BY account_id
) u ON a.id = u.account_id
ORDER BY a.priority ASC;
"""
