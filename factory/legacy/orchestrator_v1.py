# ⚠️ DEPRECATED — not used in production. For reference only.
# See AUDIT_REPORT.md for details.

"""
Factory OS — Orchestrator v1.0
Deterministic dispatcher. No LLM. Pure state machine + queue management.

Self-improvement introspect (collect_signals → improvement_candidates → Vision) lives in
``factory/orchestrator_core.py`` (``Orchestrator.tick`` → ``run_introspect_tick``), not in this
legacy loop. Environment: ``FACTORY_INTROSPECT_TICKS`` (default 20).

Architecture:
    Orchestrator is the ONLY writer of FSM transitions.
    Agents propose events; orchestrator validates guards and commits transitions.
    Single-writer model: one orchestrator process, WAL mode for concurrent readers.

Recovery:
    On startup, orchestrator scans for:
    - Expired leases → release and re-queue
    - Runs stuck in 'running' → mark failed, trigger retry logic
    - Blocked items with resolved deps → unblock
"""

import sqlite3
import time
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OrchestratorConfig:
    db_path: str = "factory.db"
    poll_interval_sec: float = 2.0
    lease_duration_sec: int = 300          # 5 min default lease
    max_forge_attempts: int = 5
    max_review_rejections: int = 3
    max_judge_rejections: int = 3
    max_planning_depth: int = 3
    stale_lease_sec: int = 600             # 10 min → consider lease expired
    context_window_events: int = 200       # max events in a context snapshot


# ═══════════════════════════════════════════════════════════════════════════
# ID GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def gen_id(prefix: str) -> str:
    """Generate a prefixed unique id. In production, use ULID."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ═══════════════════════════════════════════════════════════════════════════
# EVENT LOG — append-only domain journal
# ═══════════════════════════════════════════════════════════════════════════

def emit_event(
    cur: sqlite3.Cursor,
    event_type: str,
    entity_type: str,
    entity_id: str,
    message: str,
    *,
    severity: str = "info",
    actor_role: str = "orchestrator",
    actor_id: str = None,
    run_id: str = None,
    work_item_id: str = None,
    parent_event_id: int = None,
    correlation_id: str = None,
    payload: dict = None,
) -> int:
    """Write one event to event_log. Returns the new event id."""
    cur.execute("""
        INSERT INTO event_log
            (event_time, event_type, entity_type, entity_id,
             parent_event_id, run_id, work_item_id,
             actor_role, actor_id, severity, message, payload, correlation_id)
        VALUES (?,?,?,?, ?,?,?, ?,?,?,?,?, ?)
    """, (
        utc_now(), event_type, entity_type, entity_id,
        parent_event_id, run_id, work_item_id,
        actor_role, actor_id, severity, message,
        json.dumps(payload) if payload else None,
        correlation_id,
    ))
    return cur.lastrowid


# ═══════════════════════════════════════════════════════════════════════════
# GUARDS — pure predicates, no side effects
# ═══════════════════════════════════════════════════════════════════════════

class Guards:
    """All guard functions take (cursor, work_item_row) → bool."""

    @staticmethod
    def has_children(cur, wi) -> bool:
        cur.execute("SELECT 1 FROM work_items WHERE parent_id = ?", (wi["id"],))
        return cur.fetchone() is not None

    @staticmethod
    def has_non_atom_children(cur, wi) -> bool:
        cur.execute(
            "SELECT 1 FROM work_items WHERE parent_id = ? AND kind != 'atom'",
            (wi["id"],)
        )
        return cur.fetchone() is not None

    @staticmethod
    def all_children_atoms(cur, wi) -> bool:
        cur.execute(
            "SELECT COUNT(*) FROM work_items WHERE parent_id = ? AND kind != 'atom'",
            (wi["id"],)
        )
        return cur.fetchone()[0] == 0

    @staticmethod
    def all_children_done(cur, wi) -> bool:
        cur.execute("""
            SELECT COUNT(*) FROM work_items
            WHERE parent_id = ? AND status NOT IN ('done','cancelled','archived')
        """, (wi["id"],))
        return cur.fetchone()[0] == 0

    @staticmethod
    def has_files_declared(cur, wi) -> bool:
        cur.execute(
            "SELECT 1 FROM work_item_files WHERE work_item_id = ?", (wi["id"],)
        )
        return cur.fetchone() is not None

    @staticmethod
    def can_acquire_locks(cur, wi) -> bool:
        """Check no conflicting active locks on declared files."""
        cur.execute("""
            SELECT fl.path FROM file_locks fl
            JOIN work_item_files wf ON wf.path = fl.path
            WHERE wf.work_item_id = ?
              AND fl.released_at IS NULL
              AND fl.work_item_id != ?
        """, (wi["id"], wi["id"]))
        return cur.fetchone() is None

    @staticmethod
    def has_unresolved_dep(cur, wi) -> bool:
        cur.execute("""
            SELECT 1 FROM work_item_links wl
            JOIN work_items dep ON dep.id = wl.dst_id
            WHERE wl.src_id = ? AND wl.link_type = 'depends_on'
              AND dep.status NOT IN ('done','cancelled','archived')
        """, (wi["id"],))
        return cur.fetchone() is not None

    @staticmethod
    def all_deps_resolved(cur, wi) -> bool:
        return not Guards.has_unresolved_dep(cur, wi)

    @staticmethod
    def under_retry_limit(cur, wi, config: "OrchestratorConfig") -> bool:
        return wi["forge_attempts"] < config.max_forge_attempts

    @staticmethod
    def over_retry_limit(cur, wi, config: "OrchestratorConfig") -> bool:
        return not Guards.under_retry_limit(cur, wi, config)

    @staticmethod
    def no_blocking_failures(cur, wi) -> bool:
        """Check that the latest run has no blocking review_checks with status='failed'."""
        cur.execute("""
            SELECT 1 FROM review_checks rc
            JOIN runs r ON r.id = rc.run_id
            WHERE r.work_item_id = ? AND r.status = 'completed'
              AND rc.is_blocking = 1 AND rc.status = 'failed'
            ORDER BY r.created_at DESC LIMIT 1
        """, (wi["id"],))
        return cur.fetchone() is None

    @staticmethod
    def has_rework_comment(cur, wi) -> bool:
        cur.execute("""
            SELECT 1 FROM comments
            WHERE work_item_id = ? AND comment_type IN ('note','analysis','instruction')
            ORDER BY created_at DESC LIMIT 1
        """, (wi["id"],))
        return cur.fetchone() is not None

    # Registry for lookup by name from state_transitions table
    _registry: dict[str, Callable] = {}

    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        if not cls._registry:
            cls._registry = {
                k: v for k, v in vars(cls).items()
                if callable(v) and not k.startswith("_") and k != "get"
            }
        return cls._registry.get(name)


# ═══════════════════════════════════════════════════════════════════════════
# ACTIONS — side effects after transition commit
# ═══════════════════════════════════════════════════════════════════════════

class Actions:
    """Post-transition actions. Each takes (cursor, work_item_row, config)."""

    @staticmethod
    def enqueue_forge(cur, wi, config):
        cur.execute("""
            INSERT OR REPLACE INTO work_item_queue
                (work_item_id, queue_name, available_at, attempts)
            VALUES (?, 'forge_inbox', ?, 0)
        """, (wi["id"], utc_now()))

    @staticmethod
    def enqueue_review(cur, wi, config):
        cur.execute("""
            INSERT OR REPLACE INTO work_item_queue
                (work_item_id, queue_name, available_at, attempts)
            VALUES (?, 'review_inbox', ?, 0)
        """, (wi["id"], utc_now()))

    @staticmethod
    def enqueue_forge_with_feedback(cur, wi, config):
        """Re-queue to forge with incremented attempt counter."""
        cur.execute("""
            INSERT OR REPLACE INTO work_item_queue
                (work_item_id, queue_name, available_at, attempts)
            VALUES (?, 'forge_inbox', ?, ?)
        """, (wi["id"], utc_now(), wi["forge_attempts"]))

    @staticmethod
    def acquire_file_locks(cur, wi, config):
        cur.execute(
            "SELECT path, intent FROM work_item_files WHERE work_item_id = ?",
            (wi["id"],)
        )
        for row in cur.fetchall():
            if row[1] in ("modify", "create", "delete", "rename"):
                cur.execute("""
                    INSERT OR REPLACE INTO file_locks (path, work_item_id, lock_reason, acquired_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    row[0], wi["id"], f"atom_{wi['id']}",
                    utc_now(),
                    (datetime.now(timezone.utc) + timedelta(seconds=config.lease_duration_sec))
                        .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ))

    @staticmethod
    def release_locks(cur, wi, config):
        cur.execute("""
            UPDATE file_locks SET released_at = ?
            WHERE work_item_id = ? AND released_at IS NULL
        """, (utc_now(), wi["id"]))

    @staticmethod
    def commit_and_release(cur, wi, config):
        """Mark locks released; actual git commit handled by forge/review agent."""
        Actions.release_locks(cur, wi, config)
        # Remove from queue
        cur.execute("DELETE FROM work_item_queue WHERE work_item_id = ?", (wi["id"],))

    @staticmethod
    def release_locks_and_retry(cur, wi, config):
        Actions.release_locks(cur, wi, config)
        cur.execute(
            "UPDATE work_items SET forge_attempts = forge_attempts + 1 WHERE id = ?",
            (wi["id"],)
        )
        Actions.enqueue_forge(cur, wi, config)

    @staticmethod
    def save_resume_state(cur, wi, config):
        """Save current status as resume target before blocking."""
        cur.execute("""
            UPDATE work_items
            SET resume_to_status = ?, blocked_since = ?
            WHERE id = ?
        """, (wi["status"], utc_now(), wi["id"]))

    @staticmethod
    def restore_from_block(cur, wi, config):
        """Restore status from resume_to_status after unblock."""
        resume = wi["resume_to_status"] or "planned"
        cur.execute("""
            UPDATE work_items
            SET status = ?, blocked_reason = NULL, blocked_since = NULL, resume_to_status = NULL
            WHERE id = ?
        """, (resume, wi["id"]))

    @staticmethod
    def block_needs_human(cur, wi, config):
        cur.execute("""
            UPDATE work_items
            SET needs_human_review = 1, blocked_reason = 'exceeded_retry_limit', blocked_since = ?
            WHERE id = ?
        """, (utc_now(), wi["id"]))

    @staticmethod
    def build_judge_context(cur, wi, config):
        """Build a context snapshot for the judge from recent events."""
        cur.execute("""
            SELECT id FROM event_log
            WHERE work_item_id = ?
            ORDER BY id DESC LIMIT ?
        """, (wi["id"], config.context_window_events))
        rows = cur.fetchall()
        if rows:
            event_to = rows[0][0]
            event_from = rows[-1][0]
            cur.execute("""
                INSERT INTO context_snapshots
                    (id, work_item_id, snapshot_kind, summary,
                     source_event_from, source_event_to, event_count)
                VALUES (?, ?, 'judge_context', ?, ?, ?, ?)
            """, (
                gen_id("ctx"), wi["id"],
                f"Auto-context for judge review of {wi['id']}",
                event_from, event_to, len(rows),
            ))

    @staticmethod
    def notify_planner(cur, wi, config):
        cur.execute("""
            INSERT OR REPLACE INTO work_item_queue
                (work_item_id, queue_name, available_at)
            VALUES (?, 'planner_inbox', ?)
        """, (wi["id"], utc_now()))

    @staticmethod
    def propagate_completion(cur, wi, config):
        """Check if parent can now be marked done."""
        if wi["parent_id"]:
            cur.execute("""
                SELECT COUNT(*) FROM work_items
                WHERE parent_id = ? AND status NOT IN ('done','cancelled','archived')
            """, (wi["parent_id"],))
            if cur.fetchone()[0] == 0:
                # Parent eligible for completion → enqueue event for next cycle
                emit_event(
                    cur, "all_children_done", "work_item", wi["parent_id"],
                    f"All children of {wi['parent_id']} completed",
                    work_item_id=wi["parent_id"],
                )

    @staticmethod
    def cascade_cancel(cur, wi, config):
        cur.execute("""
            UPDATE work_items SET status = 'cancelled', updated_at = ?
            WHERE parent_id = ? AND status NOT IN ('done','cancelled','archived')
        """, (utc_now(), wi["id"]))
        Actions.release_locks(cur, wi, config)

    @staticmethod
    def increment_judge_rejections(cur, wi, config):
        cur.execute(
            "UPDATE work_items SET judge_rejections = judge_rejections + 1 WHERE id = ?",
            (wi["id"],)
        )
        new_count = wi["judge_rejections"] + 1
        if new_count >= config.max_judge_rejections:
            cur.execute(
                "UPDATE work_items SET needs_human_review = 1 WHERE id = ?",
                (wi["id"],)
            )

    @staticmethod
    def return_to_initiator(cur, wi, config):
        """Route rejected item back based on who created it."""
        queue_map = {
            "architect": "architect_inbox",
            "planner": "planner_inbox",
            "hr": "hr_inbox",
        }
        target = queue_map.get(wi["creator_role"], "planner_inbox")
        cur.execute("""
            INSERT OR REPLACE INTO work_item_queue
                (work_item_id, queue_name, available_at)
            VALUES (?, ?, ?)
        """, (wi["id"], target, utc_now()))

    # Registry
    _registry: dict[str, Callable] = {}

    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        if not cls._registry:
            # Strip "action_" prefix when looking up
            cls._registry = {
                f"action_{k}": v for k, v in vars(cls).items()
                if callable(v) and not k.startswith("_") and k != "get"
            }
        return cls._registry.get(name)


# ═══════════════════════════════════════════════════════════════════════════
# TRANSITION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransitionResult:
    success: bool
    from_state: str
    to_state: str = ""
    event_name: str = ""
    guard_failed: str = ""
    error: str = ""


class TransitionEngine:
    """Loads FSM rules from DB, evaluates guards, applies transitions."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self._transitions: list[dict] = []

    def load_transitions(self, cur: sqlite3.Cursor):
        cur.execute("SELECT * FROM state_transitions")
        cols = [d[0] for d in cur.description]
        self._transitions = [dict(zip(cols, row)) for row in cur.fetchall()]

    def find_transition(self, cur, wi: dict, event_name: str) -> Optional[dict]:
        """Find the first matching transition for (kind, status, event)."""
        for t in self._transitions:
            # Match entity type
            if t["entity_type"] != "work_item":
                continue
            # Match kind filter
            if t["kind_filter"] and t["kind_filter"] != wi["kind"]:
                continue
            # Match from_state (* = wildcard)
            if t["from_state"] != "*" and t["from_state"] != wi["status"]:
                continue
            # Match event
            if t["event_name"] != event_name:
                continue
            # Evaluate guard
            if t["guard_name"]:
                guard_fn = Guards.get(t["guard_name"])
                if guard_fn and not guard_fn(cur, wi):
                    continue
            return t
        return None

    def apply(self, conn: sqlite3.Connection, wi_id: str, event_name: str) -> TransitionResult:
        """Atomic: guard check + state change + event log + action, all in one transaction."""
        cur = conn.cursor()
        try:
            # Fetch current state
            cur.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return TransitionResult(False, "", error=f"work_item {wi_id} not found")
            wi = dict(zip(cols, row))

            # Find transition
            t = self.find_transition(cur, wi, event_name)
            if not t:
                return TransitionResult(
                    False, wi["status"],
                    guard_failed=f"No valid transition for ({wi['kind']}, {wi['status']}, {event_name})"
                )

            old_status = wi["status"]
            new_status = t["to_state"]

            # Handle dynamic to_state (e.g. {resume_to_status})
            if new_status.startswith("{") and new_status.endswith("}"):
                field_name = new_status[1:-1]
                new_status = wi.get(field_name) or "planned"

            # ── BEGIN ATOMIC BLOCK ──
            # Update status
            cur.execute(
                "UPDATE work_items SET status = ?, owner_role = NULL WHERE id = ?",
                (new_status, wi_id)
            )

            # Write event
            emit_event(
                cur, event_name, "work_item", wi_id,
                f"{wi_id}: {old_status} → {new_status} via {event_name}",
                work_item_id=wi_id,
                payload={"from": old_status, "to": new_status, "transition_id": t["id"]},
            )

            # Execute post-action
            if t["action_name"]:
                action_fn = Actions.get(t["action_name"])
                if action_fn:
                    # Refresh wi with new status for action context
                    wi["status"] = new_status
                    action_fn(cur, wi, self.config)

            conn.commit()
            return TransitionResult(True, old_status, new_status, event_name)

        except Exception as e:
            conn.rollback()
            return TransitionResult(False, "", error=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    Deterministic poll-based orchestrator.
    Cycle: scan → evaluate → transition → enqueue → sleep → repeat.
    """

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.engine = TransitionEngine(config)
        self.conn: Optional[sqlite3.Connection] = None
        self.running = False

    def connect(self):
        delays = (1.0, 2.0, 4.0)
        last_err: Exception | None = None
        for attempt, d in enumerate((0.0,) + delays, start=1):
            if d:
                time.sleep(d)
            try:
                self.conn = sqlite3.connect(self.config.db_path, timeout=30.0)
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA foreign_keys=ON")
                self.conn.execute("PRAGMA busy_timeout=10000")
                self.conn.row_factory = sqlite3.Row
                self.engine.load_transitions(self.conn.cursor())
                return
            except sqlite3.OperationalError as e:
                last_err = e
                if "locked" not in str(e).lower():
                    raise
        print(f"[orchestrator] DB connect failed after {attempt} attempts: {last_err}", flush=True)
        raise SystemExit(2)

    def run(self):
        """Main loop."""
        self.connect()
        self.running = True
        self._recover_on_startup()

        while self.running:
            try:
                changed = 0
                changed += self._process_expired_leases()
                changed += self._process_stuck_runs()
                changed += self._process_unblocked_items()
                changed += self._process_parent_completions()
                changed += self._process_pending_events()

                if changed == 0:
                    time.sleep(self.config.poll_interval_sec)
            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                # Log error but don't crash — orchestrator must be resilient
                emit_event(
                    self.conn.cursor(),
                    "orchestrator_error", "system", "orchestrator",
                    str(e), severity="error"
                )
                self.conn.commit()
                time.sleep(self.config.poll_interval_sec * 2)

    # ── Recovery on startup ─────────────────────────────────────────────

    def _recover_on_startup(self):
        """Clean up after potential crash."""
        cur = self.conn.cursor()
        now = utc_now()

        # 1. Expire stale leases
        stale_cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self.config.stale_lease_sec)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        cur.execute("""
            UPDATE work_item_queue
            SET lease_owner = NULL, lease_until = NULL
            WHERE lease_until IS NOT NULL AND lease_until < ?
        """, (stale_cutoff,))

        # 2. Mark stuck runs as failed
        cur.execute("""
            UPDATE runs SET status = 'failed', error_summary = 'Recovered: stuck after restart'
            WHERE status = 'running' AND started_at < ?
        """, (stale_cutoff,))

        # 3. Release orphaned file locks
        cur.execute("""
            UPDATE file_locks SET released_at = ?
            WHERE released_at IS NULL AND expires_at IS NOT NULL AND expires_at < ?
        """, (now, now))

        affected = self.conn.total_changes
        if affected > 0:
            emit_event(
                cur, "orchestrator_recovery", "system", "orchestrator",
                f"Recovered {affected} stale entities on startup",
                severity="warn",
            )
        self.conn.commit()

    # ── Periodic checks ─────────────────────────────────────────────────

    def _process_expired_leases(self) -> int:
        """Release leases that have timed out."""
        cur = self.conn.cursor()
        now = utc_now()
        cur.execute("""
            SELECT work_item_id FROM work_item_queue
            WHERE lease_until IS NOT NULL AND lease_until < ?
        """, (now,))
        rows = cur.fetchall()
        for row in rows:
            cur.execute("""
                UPDATE work_item_queue
                SET lease_owner = NULL, lease_until = NULL, attempts = attempts + 1
                WHERE work_item_id = ?
            """, (row[0],))
            emit_event(
                cur, "lease_expired", "work_item", row[0],
                f"Lease expired for {row[0]}", severity="warn",
                work_item_id=row[0],
            )
        if rows:
            self.conn.commit()
        return len(rows)

    def _process_stuck_runs(self) -> int:
        """Detect runs that exceeded time limit."""
        cur = self.conn.cursor()
        stale = (
            datetime.now(timezone.utc) - timedelta(seconds=self.config.stale_lease_sec)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        cur.execute("""
            SELECT id, work_item_id FROM runs
            WHERE status = 'running' AND started_at < ?
        """, (stale,))
        rows = cur.fetchall()
        for row in rows:
            cur.execute(
                "UPDATE runs SET status = 'timed_out', finished_at = ? WHERE id = ?",
                (utc_now(), row[0])
            )
            if row[1]:
                self.engine.apply(self.conn, row[1], "forge_failed")
        return len(rows)

    def _process_unblocked_items(self) -> int:
        """Check blocked items whose dependencies are now resolved."""
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM work_items WHERE status = 'blocked'")
        changed = 0
        for row in cur.fetchall():
            result = self.engine.apply(self.conn, row[0], "dependency_resolved")
            if result.success:
                changed += 1
        return changed

    def _process_parent_completions(self) -> int:
        """Roll up: parent → done только если у него нет детей не в done/cancelled/archived."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT p.id FROM work_items p
            WHERE p.status NOT IN ('done', 'cancelled', 'archived')
              AND EXISTS (SELECT 1 FROM work_items c WHERE c.parent_id = p.id)
              AND NOT EXISTS (
                  SELECT 1 FROM work_items c2
                  WHERE c2.parent_id = p.id
                    AND c2.status NOT IN ('done', 'cancelled', 'archived')
              )
        """)
        changed = 0
        for row in cur.fetchall():
            result = self.engine.apply(self.conn, row[0], "all_children_done")
            if result.success:
                changed += 1
        return changed

    def _process_pending_events(self) -> int:
        """
        Process events that agents have posted (via API/queue).
        Agents don't change state directly — they submit events,
        and the orchestrator validates and applies them.
        """
        # In production: read from an event inbox table or message queue.
        # Here: scan for items in specific states that need automatic advancement.
        cur = self.conn.cursor()
        changed = 0

        # Auto-advance: atoms in ready_for_work with no queue entry → enqueue
        cur.execute("""
            SELECT wi.id FROM work_items wi
            WHERE wi.kind = 'atom' AND wi.status = 'ready_for_work'
              AND wi.id NOT IN (SELECT work_item_id FROM work_item_queue)
              AND wi.needs_human_review = 0
        """)
        for row in cur.fetchall():
            cur.execute("""
                INSERT OR IGNORE INTO work_item_queue
                    (work_item_id, queue_name, available_at)
                VALUES (?, 'forge_inbox', ?)
            """, (row[0], utc_now()))
            changed += 1
        if changed:
            self.conn.commit()

        return changed

    # ── Public API for agents ───────────────────────────────────────────

    def submit_event(self, work_item_id: str, event_name: str, agent_id: str = None) -> TransitionResult:
        """
        Called by agent adapters to propose a state change.
        Orchestrator validates guards and commits atomically.
        """
        result = self.engine.apply(self.conn, work_item_id, event_name)
        return result

    def lease_work(self, queue_name: str, agent_id: str) -> Optional[str]:
        """
        Lease the highest-priority available item from a queue.
        Returns work_item_id or None.
        """
        cur = self.conn.cursor()
        now = utc_now()
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=self.config.lease_duration_sec)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        cur.execute("""
            SELECT work_item_id FROM work_item_queue wiq
            JOIN work_items wi ON wi.id = wiq.work_item_id
            WHERE wiq.queue_name = ?
              AND wiq.lease_owner IS NULL
              AND wiq.available_at <= ?
              AND wiq.attempts < wiq.max_attempts
            ORDER BY wi.priority ASC, wiq.created_at ASC
            LIMIT 1
        """, (queue_name, now))

        row = cur.fetchone()
        if not row:
            return None

        wi_id = row[0]
        cur.execute("""
            UPDATE work_item_queue
            SET lease_owner = ?, lease_until = ?
            WHERE work_item_id = ?
        """, (agent_id, lease_until, wi_id))

        emit_event(
            cur, "work_leased", "work_item", wi_id,
            f"{agent_id} leased {wi_id} from {queue_name}",
            actor_id=agent_id, work_item_id=wi_id,
        )
        self.conn.commit()
        return wi_id

    def release_lease(self, work_item_id: str):
        """Release a lease without completing (e.g. agent can't proceed)."""
        cur = self.conn.cursor()
        cur.execute("""
            UPDATE work_item_queue
            SET lease_owner = NULL, lease_until = NULL
            WHERE work_item_id = ?
        """, (work_item_id,))
        self.conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY HELPER — for durable LLM calls
# ═══════════════════════════════════════════════════════════════════════════

def make_idempotency_key(run_id: str, step_no: int) -> str:
    """Deterministic key for (run, step) pair."""
    return hashlib.sha256(f"{run_id}:{step_no}".encode()).hexdigest()[:32]

def get_or_execute_step(
    conn: sqlite3.Connection,
    run_id: str,
    step_no: int,
    step_kind: str,
    execute_fn,       # Callable[[], dict]  — the actual LLM/tool call
) -> dict:
    """
    Durable execution wrapper:
    - If step already completed → return cached result (no API call)
    - If step not started → execute, save result, return
    - If step started but not completed → re-execute (idempotent retry)
    """
    cur = conn.cursor()
    idem_key = make_idempotency_key(run_id, step_no)

    # Check for existing completed step
    cur.execute("""
        SELECT payload, status FROM run_steps
        WHERE run_id = ? AND step_no = ? AND status = 'completed'
    """, (run_id, step_no))
    existing = cur.fetchone()
    if existing:
        return json.loads(existing[0])  # Replay from cache

    # Execute
    step_id = gen_id("step")
    cur.execute("""
        INSERT OR REPLACE INTO run_steps
            (id, run_id, step_no, step_kind, status, idempotency_key, payload)
        VALUES (?, ?, ?, ?, 'started', ?, '{}')
    """, (step_id, run_id, step_no, step_kind, idem_key))
    conn.commit()

    try:
        result = execute_fn()
        cur.execute("""
            UPDATE run_steps
            SET status = 'completed', payload = ?, cached_result = 0
            WHERE id = ?
        """, (json.dumps(result), step_id))
        conn.commit()
        return result
    except Exception as e:
        cur.execute("""
            UPDATE run_steps SET status = 'failed', payload = ? WHERE id = ?
        """, (json.dumps({"error": str(e)}), step_id))
        conn.commit()
        raise


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    config = OrchestratorConfig()
    orch = Orchestrator(config)
    print("Factory OS Orchestrator starting...")
    orch.run()
