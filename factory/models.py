"""Доменные модели (Enum) для Фабрики 2.0 — Фаза 1."""

from enum import Enum


class WorkItemKind(str, Enum):
    VISION = "vision"
    INITIATIVE = "initiative"
    EPIC = "epic"
    STORY = "story"
    TASK = "task"
    ATOM = "atom"
    ATM_CHANGE = "atm_change"


class WorkItemStatus(str, Enum):
    DRAFT = "draft"
    PLANNED = "planned"
    READY_FOR_JUDGE = "ready_for_judge"
    JUDGE_REJECTED = "judge_rejected"
    # Штатная «готовность к кузнице»: этот статус + запись в work_item_queue (forge_inbox).
    # owner_role после judge_approved выставляет FSM (forge). См. forge_next_atom.py.
    READY_FOR_WORK = "ready_for_work"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    REVIEW_REJECTED = "review_rejected"
    DONE = "done"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class Role(str, Enum):
    CREATOR = "creator"
    PLANNER = "planner"
    ARCHITECT = "architect"
    JUDGE = "judge"
    REVIEWER = "reviewer"
    FORGE = "forge"
    HR = "hr"
    ORCHESTRATOR = "orchestrator"
    SYSTEM = "system"


class RunType(str, Enum):
    PLAN = "plan"
    ANALYZE = "analyze"
    JUDGE = "judge"
    IMPLEMENT = "implement"
    REVIEW = "review"
    REPAIR = "repair"
    HR_AUDIT = "hr_audit"
    ORCHESTRATE = "orchestrate"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepKind(str, Enum):
    PROMPT = "prompt"
    LLM_REPLY = "llm_reply"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DECISION = "decision"
    FILE_WRITE = "file_write"
    TEST = "test"
    GIT = "git"
    ERROR = "error"


class LinkType(str, Enum):
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    DUPLICATES = "duplicates"
    RELATES_TO = "relates_to"
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"


class QueueName(str, Enum):
    PLANNER_INBOX = "planner_inbox"
    ARCHITECT_INBOX = "architect_inbox"
    JUDGE_INBOX = "judge_inbox"
    FORGE_INBOX = "forge_inbox"
    REVIEW_INBOX = "review_inbox"
    HR_INBOX = "hr_inbox"
    COMPLETION_INBOX = "completion_inbox"


class DecisionVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"
    DEFERRED = "deferred"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"


class FileIntent(str, Enum):
    READ = "read"
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    RENAME = "rename"


class CheckType(str, Enum):
    TESTS = "tests"
    LINT = "lint"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    POLICY = "policy"


class CommentType(str, Enum):
    NOTE = "note"
    ANALYSIS = "analysis"
    DECISION = "decision"
    REJECTION = "rejection"
    INSTRUCTION = "instruction"
    SUMMARY = "summary"


class EventType(str, Enum):
    """Whitelist событий для event_log (единый словарь)."""

    VISION_CREATED = "vision.created"
    PLANNER_DECOMPOSED = "planner.decomposed"
    TASK_CREATED = "task.created"
    CHILD_CREATED = "child.created"
    TASK_STATUS_CHANGED = "task.status_changed"
    COMMENT_ADDED = "comment.added"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    REVIEW_STARTED = "review.started"
    REVIEW_PASSED = "review.passed"
    REVIEW_REJECTED = "review.rejected"
    REVIEW_RESULT = "review.result"
    REVIEW_INVALID_OUTPUT = "review.invalid_output"
    JUDGE_STARTED = "judge.started"
    JUDGE_APPROVED = "judge.approved"
    JUDGE_REJECTED = "judge.rejected"
    JUDGE_VERDICT = "judge.verdict"
    JUDGE_INVALID_OUTPUT = "judge.invalid_output"
    FORGE_STARTED = "forge.started"
    FORGE_STEP = "forge.step"
    FORGE_COMPLETED = "forge.completed"
    FORGE_FAILED = "forge.failed"
    # Qwen CLI / пул аккаунтов (ротация, лимиты, причины для Судьи)
    ACCOUNT_RATE_LIMITED = "account.rate_limited"
    ACCOUNT_MARKED_COOLING_DOWN = "account.marked_cooling_down"
    ACCOUNT_RESTORED = "account.restored"
    ACCOUNT_POOL_EXHAUSTED = "account.pool_exhausted"
    RUN_FAILED_ACCOUNT_EXHAUSTED = "run.failed.account_exhausted"
    RUN_FAILED_ACCOUNT_ROTATION_LIMIT = "run.failed.account_rotation_limit"
    RUN_FAILED_CLI_ERROR = "run.failed.cli_error"
    RUN_FAILED_FORGE_NO_ARTIFACT = "run.failed.forge_no_artifact"
    # Вызов qwen_cli_runner (в т.ч. FACTORY_QWEN_DRY_RUN=1) — аудит «раннер реально вызван»
    QWEN_RUN_INVOCATION = "qwen.run.invocation"
    # Детальный аудит forge / Qwen (дашборд event_log, /api/atoms/{id}/log)
    FORGE_PROMPT_SENT = "forge_prompt_sent"
    FORGE_CLI_INVOKED = "forge_cli_invoked"
    FORGE_CLI_COMPLETED = "forge_cli_completed"
    FORGE_RUN_RESULT = "forge_run_result"
    FORGE_FILE_CHANGED = "forge_file_changed"
    # modify в work_item_files, файла нет в workspace — fallback на create (аудит для introspect)
    FORGE_MODIFY_MISSING_FILE = "forge.modify_missing_file"
    FORGE_SUCCEEDED = "forge.succeeded"
    ACCOUNT_SELECTED = "account.selected"
    # Очередь задач (task manager)
    TASK_ENQUEUED = "task.enqueued"
    TASK_DEQUEUED = "task.dequeued"
    # Worker loop
    WORKER_IDLE = "worker.idle"
    WORKER_STOPPED = "worker.stopped"
    # Дашборд: ручной запуск атома (POST /api/tasks/<id>/run)
    DASHBOARD_TASK_RUN_REQUESTED = "dashboard.task_run_requested"
    DASHBOARD_TASK_RUN_DENIED = "dashboard.task_run_denied"
    DASHBOARD_TASK_RUN_STARTED = "dashboard.task_run_started"
    # Self-improvement loop (introspect → improvement_candidates → Vision)
    INTROSPECT_CANDIDATE_CREATED = "introspect.candidate_created"
    INTROSPECT_AUTO_APPROVED = "introspect.auto_approved"
    INTROSPECT_VISION_CREATED = "introspect.vision_created"
    # API: PATCH title/description (создатель)
    WORK_ITEM_UPDATED = "work_item.updated"
    WORK_ITEM_DELETED = "work_item.deleted"
    API_WORK_ITEM_CANCEL = "api.work_item.cancel_requested"
    API_WORK_ITEM_ARCHIVE = "api.work_item.archive_requested"
    # Автономный оркестратор: фоновые forge/review (FACTORY_ORCHESTRATOR_ASYNC=1)
    ORCHESTRATOR_AUTO_FORGE_STARTED = "orchestrator.auto_forge_started"
    ORCHESTRATOR_AUTO_REVIEW_STARTED = "orchestrator.auto_review_started"

