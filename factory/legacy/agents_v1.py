# ⚠️ DEPRECATED — not used in production. For reference only.
# See AUDIT_REPORT.md for details.

"""
Factory OS — Agent Adapters v1.0

Thin wrappers for each role. Every adapter follows the same contract:
    1. lease_work() from its queue
    2. Execute domain logic via get_or_execute_step() (durable)
    3. submit_event() back to orchestrator

Agents do NOT mutate work_item status directly — only the orchestrator does.
Agents propose events; orchestrator validates guards and commits transitions.
"""

import json
import os
import time
import sqlite3
import hashlib
import subprocess
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Any, Callable
from pathlib import Path

from factory.workspace_scanner import scan_workspace

# ── Imports from orchestrator (same package) ────────────────────────────
# In production these come from factory_orchestrator_v1
# from factory_orchestrator_v1 import (
#     Orchestrator, OrchestratorConfig, gen_id, utc_now,
#     emit_event, get_or_execute_step, make_idempotency_key
# )


# ═══════════════════════════════════════════════════════════════════════════
# LLM CLIENT INTERFACE
# ═══════════════════════════════════════════════════════════════════════════

class LLMClient(ABC):
    """
    Abstract LLM interface. Implement for your provider (OpenAI, Anthropic, local).
    The adapter doesn't care which model — it calls .complete() and gets text back.
    """

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict:
        """Returns {"content": str, "usage": {"prompt_tokens": int, "completion_tokens": int}}"""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# BASE AGENT ADAPTER
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AgentContext:
    """Immutable snapshot passed to each agent cycle."""
    work_item: dict
    work_item_files: list[dict]
    comments: list[dict]
    decisions: list[dict]
    recent_events: list[dict]
    parent_chain: list[dict]       # ancestors up to vision
    sibling_items: list[dict]      # same-parent items for context
    context_snapshot: Optional[str] # pre-built summary if available


class BaseAgentAdapter(ABC):
    """
    Common lifecycle for all agent roles.

    Subclasses implement:
        - queue_name: which queue to poll
        - role: agent role string
        - execute(ctx) → event_name: domain logic
    """

    def __init__(self, orchestrator, llm: LLMClient, agent_id: str):
        self.orch = orchestrator
        self.llm = llm
        self.agent_id = agent_id
        self.conn = orchestrator.conn
        self._running = False

    @property
    @abstractmethod
    def queue_name(self) -> str: ...

    @property
    @abstractmethod
    def role(self) -> str: ...

    @abstractmethod
    def execute(self, ctx: AgentContext) -> str:
        """
        Run domain logic. Return the event_name to submit.
        Raise AgentSkipError to release lease without event.
        Raise AgentRetryError for transient failures.
        """
        ...

    # ── Lifecycle ───────────────────────────────────────────────────────

    def run_loop(self, poll_interval: float = 5.0):
        """Poll queue, execute, submit. Repeat."""
        self._running = True
        while self._running:
            try:
                processed = self.run_once()
                if not processed:
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                self._running = False
            except Exception as e:
                self._log_error(f"Loop error: {e}")
                time.sleep(poll_interval * 2)

    def run_once(self) -> bool:
        """Single cycle: lease → build context → execute → submit. Returns True if work done."""
        wi_id = self.orch.lease_work(self.queue_name, self.agent_id)
        if not wi_id:
            return False

        try:
            ctx = self._build_context(wi_id)
            event_name = self.execute(ctx)
            result = self.orch.submit_event(wi_id, event_name, self.agent_id)

            if not result.success:
                self._log_error(
                    f"Transition failed for {wi_id}: {result.guard_failed or result.error}"
                )
                self.orch.release_lease(wi_id)
            return True

        except AgentSkipError as e:
            self._log_info(f"Skipping {wi_id}: {e}")
            self.orch.release_lease(wi_id)
            return True

        except Exception as e:
            self._log_error(f"Execution failed for {wi_id}: {e}")
            self.orch.release_lease(wi_id)
            return True

    # ── Context builder ─────────────────────────────────────────────────

    def _build_context(self, wi_id: str) -> AgentContext:
        cur = self.conn.cursor()

        # Work item
        cur.execute("SELECT * FROM work_items WHERE id = ?", (wi_id,))
        wi = dict(cur.fetchone())

        # Files
        cur.execute(
            "SELECT * FROM work_item_files WHERE work_item_id = ? ORDER BY path",
            (wi_id,)
        )
        files = [dict(r) for r in cur.fetchall()]

        # Comments (latest 50)
        cur.execute("""
            SELECT * FROM comments WHERE work_item_id = ?
            ORDER BY created_at DESC LIMIT 50
        """, (wi_id,))
        comments = [dict(r) for r in cur.fetchall()]

        # Decisions
        cur.execute("""
            SELECT * FROM decisions WHERE work_item_id = ?
            ORDER BY created_at DESC LIMIT 20
        """, (wi_id,))
        decisions = [dict(r) for r in cur.fetchall()]

        # Recent events
        cur.execute("""
            SELECT * FROM event_log WHERE work_item_id = ?
            ORDER BY id DESC LIMIT 100
        """, (wi_id,))
        events = [dict(r) for r in cur.fetchall()]

        # Parent chain
        parent_chain = []
        pid = wi.get("parent_id")
        while pid:
            cur.execute("SELECT * FROM work_items WHERE id = ?", (pid,))
            parent = cur.fetchone()
            if not parent:
                break
            parent_chain.append(dict(parent))
            pid = parent["parent_id"]

        # Siblings
        cur.execute("""
            SELECT * FROM work_items
            WHERE parent_id = ? AND id != ?
            ORDER BY priority, created_at
        """, (wi.get("parent_id"), wi_id))
        siblings = [dict(r) for r in cur.fetchall()]

        # Latest context snapshot
        cur.execute("""
            SELECT summary FROM context_snapshots
            WHERE work_item_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, (wi_id,))
        snap = cur.fetchone()

        return AgentContext(
            work_item=wi,
            work_item_files=files,
            comments=comments,
            decisions=decisions,
            recent_events=events,
            parent_chain=parent_chain,
            sibling_items=siblings,
            context_snapshot=snap["summary"] if snap else None,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _add_comment(self, wi_id: str, body: str, comment_type: str = "note",
                     structured: dict = None):
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO comments (id, work_item_id, author_role, author_agent_id,
                                  comment_type, body, structured_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            gen_id("cmt"), wi_id, self.role, self.agent_id,
            comment_type, body,
            json.dumps(structured) if structured else None,
        ))
        self.conn.commit()

    def _add_decision(self, wi_id: str, decision: str, reason_code: str = None,
                      comment_body: str = None, run_id: str = None) -> str:
        cur = self.conn.cursor()
        comment_id = None
        if comment_body:
            comment_id = gen_id("cmt")
            cur.execute("""
                INSERT INTO comments (id, work_item_id, author_role, author_agent_id,
                                      comment_type, body)
                VALUES (?, ?, ?, ?, 'decision', ?)
            """, (comment_id, wi_id, self.role, self.agent_id, comment_body))

        dec_id = gen_id("dec")
        cur.execute("""
            INSERT INTO decisions (id, work_item_id, decision_role, decision,
                                   reason_code, comment_id, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (dec_id, wi_id, self.role, decision, reason_code, comment_id, run_id))
        self.conn.commit()
        return dec_id

    def _create_run(self, wi_id: str, run_type: str, **kwargs) -> str:
        cur = self.conn.cursor()
        run_id = gen_id("run")
        cur.execute("""
            INSERT INTO runs (id, work_item_id, agent_id, role, run_type, status, started_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
        """, (run_id, wi_id, self.agent_id, self.role, run_type, utc_now()))
        self.conn.commit()
        return run_id

    def _complete_run(self, run_id: str, status: str = "completed", error: str = None):
        cur = self.conn.cursor()
        cur.execute("""
            UPDATE runs SET status = ?, finished_at = ?, error_summary = ?
            WHERE id = ?
        """, (status, utc_now(), error, run_id))
        self.conn.commit()

    def _llm_step(self, run_id: str, step_no: int, system_prompt: str,
                  messages: list[dict], **kwargs) -> str:
        """Durable LLM call via get_or_execute_step."""
        def _call():
            result = self.llm.complete(system_prompt, messages, **kwargs)
            return {"content": result["content"], "usage": result.get("usage", {})}

        result = get_or_execute_step(
            self.conn, run_id, step_no, "llm_reply", _call
        )
        return result["content"]

    def _log_error(self, msg: str):
        cur = self.conn.cursor()
        emit_event(cur, f"{self.role}_error", "system", self.agent_id,
                   msg, severity="error", actor_role=self.role, actor_id=self.agent_id)
        self.conn.commit()

    def _log_info(self, msg: str):
        cur = self.conn.cursor()
        emit_event(cur, f"{self.role}_info", "system", self.agent_id,
                   msg, actor_role=self.role, actor_id=self.agent_id)
        self.conn.commit()

    def _log_warn(self, msg: str):
        cur = self.conn.cursor()
        emit_event(
            cur,
            f"{self.role}_warn",
            "system",
            self.agent_id,
            msg,
            severity="warn",
            actor_role=self.role,
            actor_id=self.agent_id,
        )
        self.conn.commit()


class AgentSkipError(Exception):
    """Agent decides to skip this item (not an error, just can't proceed now)."""
    pass

class AgentRetryError(Exception):
    """Transient failure, should retry later."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# FORGE AGENT — implements atomic tasks
# ═══════════════════════════════════════════════════════════════════════════

class ForgeAgent(BaseAgentAdapter):
    queue_name = "forge_inbox"
    role = "forge"

    SYSTEM_PROMPT = """You are the Forge — a precise code implementation agent.
You receive an atomic task with:
- Description of what to do
- List of files to modify/create
- Acceptance criteria
- Any feedback from previous attempts (judge/reviewer comments)

Rules:
- Implement EXACTLY what the task specifies. No more, no less.
- Output valid, working code.
- Respect existing code style and architecture.
- If a file should be modified, output the complete new version.
- If something is unclear, implement the most conservative interpretation.

Output format:
For each file, output:
```file:<path>
<complete file content>
```
"""

    def __init__(self, orchestrator, llm: LLMClient, agent_id: str,
                 workspace_path: str = "."):
        super().__init__(orchestrator, llm, agent_id)
        self.workspace = Path(workspace_path)

    def execute(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        run_id = self._create_run(wi["id"], "implement")

        try:
            # ── Step 1: Build implementation prompt ─────────────────────
            prompt = self._build_prompt(ctx)

            # ── Step 2: Read current file contents ──────────────────────
            file_contents = {}
            for f in ctx.work_item_files:
                if f.get("intent") == "modify":
                    path = self.workspace / f["path"]
                    if not path.exists():
                        self._log_warn(
                            f"Declared intent=modify but file missing: {f['path']} → treating as create for this run"
                        )
                        # Only mutate this in-memory context; do NOT write back to DB.
                        f["intent"] = "create"
            for f in ctx.work_item_files:
                if f["intent"] in ("modify", "read"):
                    path = self.workspace / f["path"]
                    if path.exists():
                        content = get_or_execute_step(
                            self.conn, run_id, 1 + len(file_contents),
                            "file_read",
                            lambda p=path: {"path": str(p), "content": p.read_text()}
                        )
                        file_contents[f["path"]] = content["content"]

            step_offset = 1 + len(file_contents)

            # ── Step 3: LLM implementation call ─────────────────────────
            messages = [{"role": "user", "content": prompt}]
            if file_contents:
                context_msg = "\n\n".join(
                    f"Current content of `{p}`:\n```\n{c}\n```"
                    for p, c in file_contents.items()
                )
                messages.insert(0, {"role": "user", "content": context_msg})

            llm_response = self._llm_step(
                run_id, step_offset + 1, self.SYSTEM_PROMPT, messages
            )

            # ── Step 4: Parse and write files ───────────────────────────
            file_outputs = self._parse_file_outputs(llm_response)
            for path, content in file_outputs.items():
                full_path = self.workspace / path
                get_or_execute_step(
                    self.conn, run_id, step_offset + 2 + list(file_outputs).index(path),
                    "file_write",
                    lambda p=full_path, c=content: self._write_file(p, c)
                )

                # Record file change
                cur = self.conn.cursor()
                new_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                old_hash = (hashlib.sha256(file_contents.get(path, "").encode()).hexdigest()[:16]
                           if path in file_contents else None)
                cur.execute("""
                    INSERT INTO file_changes (id, work_item_id, run_id, path, change_type,
                                              old_hash, new_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    gen_id("fc"), wi["id"], run_id, path,
                    "modified" if path in file_contents else "created",
                    old_hash, new_hash,
                ))
                self.conn.commit()

            self._complete_run(run_id)
            return "forge_completed"

        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            return "forge_failed"

    def _build_prompt(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        parts = [
            f"# Task: {wi['title']}",
            f"\n{wi['description'] or 'No description provided.'}",
            f"\n## Files to work with:",
        ]
        for f in ctx.work_item_files:
            parts.append(f"- `{f['path']}` — {f['intent']}")

        # Include feedback from previous attempts
        rejections = [c for c in ctx.comments
                      if c["comment_type"] in ("rejection", "decision")]
        if rejections:
            parts.append("\n## Previous feedback (MUST address):")
            for r in rejections[:3]:  # Last 3 rejections
                parts.append(f"- [{r['author_role']}]: {r['body']}")

        # Parent context
        if ctx.parent_chain:
            parts.append(f"\n## Parent task: {ctx.parent_chain[0]['title']}")

        return "\n".join(parts)

    def _parse_file_outputs(self, response: str) -> dict[str, str]:
        """Parse ```file:<path> blocks from LLM output."""
        files = {}
        current_path = None
        current_lines = []

        for line in response.split("\n"):
            if line.startswith("```file:"):
                current_path = line[len("```file:"):].strip()
                current_lines = []
            elif line.strip() == "```" and current_path:
                files[current_path] = "\n".join(current_lines)
                current_path = None
            elif current_path is not None:
                current_lines.append(line)

        return files

    def _write_file(self, path: Path, content: str) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return {"path": str(path), "bytes": len(content)}


# ═══════════════════════════════════════════════════════════════════════════
# REVIEWER AGENT — automated checks + LLM code review
# ═══════════════════════════════════════════════════════════════════════════

class ReviewerAgent(BaseAgentAdapter):
    queue_name = "review_inbox"
    role = "reviewer"

    SYSTEM_PROMPT = """You are the Reviewer — a strict code quality gate.
You receive code changes for an atomic task and must evaluate:
1. Does the code match the task specification?
2. Is the code correct and free of obvious bugs?
3. Does it follow the project's style and architecture?
4. Are there security concerns?

Output EXACTLY one of:
APPROVED — if code is acceptable
REJECTED:<reason_code> — if code must be reworked

reason_code must be one of: quality, scope, security, architecture, tests

Then provide a detailed explanation.
"""

    def __init__(self, orchestrator, llm: LLMClient, agent_id: str,
                 workspace_path: str = ".", run_tests: bool = True,
                 run_lint: bool = True):
        super().__init__(orchestrator, llm, agent_id)
        self.workspace = Path(workspace_path)
        self.run_tests = run_tests
        self.run_lint = run_lint

    def execute(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        run_id = self._create_run(wi["id"], "review")

        try:
            all_passed = True

            # ── Automated checks ────────────────────────────────────────
            if self.run_lint:
                lint_result = get_or_execute_step(
                    self.conn, run_id, 1, "test",
                    lambda: self._run_lint(ctx)
                )
                self._save_check(run_id, "lint", lint_result)
                if lint_result["status"] == "failed" and lint_result.get("is_blocking", True):
                    all_passed = False

            if self.run_tests:
                test_result = get_or_execute_step(
                    self.conn, run_id, 2, "test",
                    lambda: self._run_tests(ctx)
                )
                self._save_check(run_id, "tests", test_result)
                if test_result["status"] == "failed" and test_result.get("is_blocking", True):
                    all_passed = False

            # ── LLM review ──────────────────────────────────────────────
            if all_passed:
                review_prompt = self._build_review_prompt(ctx)
                llm_verdict = self._llm_step(
                    run_id, 10, self.SYSTEM_PROMPT,
                    [{"role": "user", "content": review_prompt}]
                )
                llm_result = self._parse_verdict(llm_verdict)
                self._save_check(run_id, "architecture", {
                    "status": "passed" if llm_result["approved"] else "failed",
                    "summary": llm_result["explanation"],
                    "is_blocking": True,
                })
                if not llm_result["approved"]:
                    all_passed = False

            # ── Final decision ──────────────────────────────────────────
            if all_passed:
                self._add_decision(
                    wi["id"], "approved",
                    comment_body="All checks passed. Code approved for merge.",
                    run_id=run_id,
                )
                self._complete_run(run_id)
                return "review_passed"
            else:
                reason = llm_result.get("reason_code", "quality") if not all_passed else "tests"
                self._add_decision(
                    wi["id"], "rejected", reason_code=reason,
                    comment_body=f"Review failed. See run {run_id} for details.",
                    run_id=run_id,
                )
                self._complete_run(run_id)
                return "review_failed"

        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            return "review_failed"

    def _run_lint(self, ctx: AgentContext) -> dict:
        """Run linter on changed files. Override for project-specific linting."""
        py_files = [f["path"] for f in ctx.work_item_files
                    if f["path"].endswith(".py") and f["intent"] in ("modify", "create")]
        if not py_files:
            return {"status": "skipped", "summary": "No Python files to lint"}

        try:
            result = subprocess.run(
                ["python", "-m", "py_compile"] + [str(self.workspace / f) for f in py_files],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return {"status": "passed", "summary": "Syntax check passed"}
            return {
                "status": "failed", "is_blocking": True,
                "summary": f"Syntax errors: {result.stderr[:500]}",
            }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {"status": "warning", "summary": "Lint check unavailable"}

    def _run_tests(self, ctx: AgentContext) -> dict:
        """Run project tests. Override for specific test framework."""
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=short", "-q"],
                capture_output=True, text=True, timeout=120,
                cwd=str(self.workspace),
            )
            if result.returncode == 0:
                return {"status": "passed", "summary": result.stdout[:500]}
            return {
                "status": "failed", "is_blocking": True,
                "summary": result.stdout[:500] + result.stderr[:500],
            }
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {"status": "skipped", "summary": "Test runner unavailable"}

    def _build_review_prompt(self, ctx: AgentContext) -> str:
        parts = [f"# Review task: {ctx.work_item['title']}", ""]

        # Show file changes
        cur = self.conn.cursor()
        cur.execute("""
            SELECT path, change_type FROM file_changes
            WHERE work_item_id = ?
            ORDER BY created_at DESC
        """, (ctx.work_item["id"],))
        changes = cur.fetchall()

        parts.append("## Changed files:")
        for ch in changes:
            path = self.workspace / ch[0]
            parts.append(f"\n### `{ch[0]}` ({ch[1]})")
            if path.exists():
                parts.append(f"```\n{path.read_text()[:8000]}\n```")

        parts.append(f"\n## Task spec:\n{ctx.work_item['description'] or 'N/A'}")
        return "\n".join(parts)

    def _parse_verdict(self, response: str) -> dict:
        first_line = response.strip().split("\n")[0].upper()
        if first_line.startswith("APPROVED"):
            return {"approved": True, "explanation": response}
        reason = "quality"
        if "REJECTED:" in first_line:
            reason = first_line.split("REJECTED:")[-1].strip().lower()
        return {"approved": False, "reason_code": reason, "explanation": response}

    def _save_check(self, run_id: str, check_type: str, result: dict):
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO review_checks (id, run_id, check_type, status, is_blocking, summary, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            gen_id("chk"), run_id, check_type,
            result.get("status", "skipped"),
            result.get("is_blocking", 1),
            result.get("summary", ""),
            json.dumps(result),
        ))
        self.conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# JUDGE AGENT — arbiter with full log access
# ═══════════════════════════════════════════════════════════════════════════

class JudgeAgent(BaseAgentAdapter):
    queue_name = "judge_inbox"
    role = "judge"

    SYSTEM_PROMPT = """You are the Judge — the impartial arbiter of the Factory.
You see the full context: task hierarchy, all comments, decisions, and event history.

Your job:
1. Validate that the proposed work item / change is consistent with the vision
2. Check for conflicts with ongoing work
3. Ensure quality standards are met
4. Provide structured, actionable feedback

Output format (STRICT):
DECISION: APPROVED | REJECTED | NEEDS_CHANGES | DEFERRED
REASON_CODE: conflict | quality | scope | security | architecture | process | other
EXPLANATION: <detailed reasoning>
SUGGESTED_FIX: <what the initiator should change, if rejected>
"""

    def execute(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        run_id = self._create_run(wi["id"], "judge")

        try:
            prompt = self._build_judge_prompt(ctx)
            response = self._llm_step(
                run_id, 1, self.SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}]
            )

            verdict = self._parse_judge_response(response)
            decision = verdict["decision"].lower()

            # Map to our decision enum
            decision_map = {
                "approved": "approved",
                "rejected": "rejected",
                "needs_changes": "needs_changes",
                "deferred": "deferred",
            }
            normalized = decision_map.get(decision, "rejected")

            self._add_decision(
                wi["id"], normalized,
                reason_code=verdict.get("reason_code"),
                comment_body=verdict.get("explanation", response),
                run_id=run_id,
            )

            if verdict.get("suggested_fix"):
                self._add_comment(
                    wi["id"], verdict["suggested_fix"],
                    comment_type="instruction",
                    structured={"from": "judge", "type": "suggested_fix"},
                )

            self._complete_run(run_id)
            return "judge_approved" if normalized == "approved" else "judge_rejected"

        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            raise AgentRetryError(f"Judge failed: {e}")

    def _build_judge_prompt(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        parts = [
            f"# Judging: {wi['title']} (kind: {wi['kind']}, status: {wi['status']})",
            f"\n## Description:\n{wi['description'] or 'None'}",
        ]

        # Vision chain (bottom-up)
        if ctx.parent_chain:
            parts.append("\n## Ancestry (→ vision):")
            for p in ctx.parent_chain:
                parts.append(f"  - [{p['kind']}] {p['title']}")

        # Files
        if ctx.work_item_files:
            parts.append("\n## Declared files:")
            for f in ctx.work_item_files:
                parts.append(f"  - `{f['path']}` ({f['intent']})")

        # Comments & decisions history
        if ctx.comments:
            parts.append("\n## Recent comments:")
            for c in ctx.comments[:10]:
                parts.append(f"  [{c['author_role']}|{c['comment_type']}]: {c['body'][:300]}")

        if ctx.decisions:
            parts.append("\n## Previous decisions on this item:")
            for d in ctx.decisions[:5]:
                parts.append(f"  [{d['decision_role']}]: {d['decision']} ({d.get('reason_code','—')})")

        # Context snapshot
        if ctx.context_snapshot:
            parts.append(f"\n## Context summary:\n{ctx.context_snapshot}")

        # Siblings for conflict detection
        if ctx.sibling_items:
            parts.append("\n## Sibling tasks (same parent):")
            for s in ctx.sibling_items[:10]:
                parts.append(f"  - [{s['status']}] {s['title']}")

        # Retry counters
        parts.append(f"\n## Metrics: forge_attempts={wi['forge_attempts']}, "
                     f"review_rejections={wi['review_rejections']}, "
                     f"judge_rejections={wi['judge_rejections']}")

        return "\n".join(parts)

    def _parse_judge_response(self, response: str) -> dict:
        result = {"decision": "rejected", "reason_code": "other", "explanation": response}
        for line in response.split("\n"):
            line = line.strip()
            if line.upper().startswith("DECISION:"):
                result["decision"] = line.split(":", 1)[1].strip().upper()
            elif line.upper().startswith("REASON_CODE:"):
                result["reason_code"] = line.split(":", 1)[1].strip().lower()
            elif line.upper().startswith("EXPLANATION:"):
                result["explanation"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("SUGGESTED_FIX:"):
                result["suggested_fix"] = line.split(":", 1)[1].strip()
        return result


# ═══════════════════════════════════════════════════════════════════════════
# PLANNER AGENT — decomposes high-level items into sub-items
# ═══════════════════════════════════════════════════════════════════════════

class PlannerAgent(BaseAgentAdapter):
    queue_name = "planner_inbox"
    role = "planner"

    SYSTEM_PROMPT = """You are the Planner — you decompose high-level goals into actionable sub-tasks.

IMPORTANT: Check the workspace structure in the user message (section "Current workspace structure").
If the task mentions modifying existing functionality, find the relevant files and use intent='modify'.
If files don't exist yet, use intent='create'.
Match file paths EXACTLY as shown in the workspace structure.

Input: a work item (vision, initiative, epic, story, or task) + architect comments.
Output: a structured decomposition.

Rules:
- Each sub-item must be smaller in scope than its parent.
- Atoms MUST specify exact files (path + intent: read/modify/create/delete).
- Never create an atom without file declarations.
- If architect commented, incorporate their guidance.
- If judge rejected a previous decomposition, address the feedback.
- Max depth: if this is already depth 3+, create only atoms.

Output as JSON array:
[
  {
    "kind": "story|task|atom",
    "title": "...",
    "description": "...",
    "files": [{"path": "...", "intent": "modify|create|read|delete"}],
    "depends_on_index": null | <index of sibling in this array>
  }
]
"""

    def __init__(self, orchestrator, llm: LLMClient, agent_id: str, workspace_path: Optional[str] = None):
        super().__init__(orchestrator, llm, agent_id)
        wp = (workspace_path or os.environ.get("FACTORY_WORKSPACE_ROOT") or ".").strip()
        self.workspace = Path(wp).resolve()

    def execute(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        run_id = self._create_run(wi["id"], "decompose")

        try:
            prompt = self._build_planner_prompt(ctx)
            response = self._llm_step(
                run_id, 1, self.SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}]
            )

            sub_items = self._parse_decomposition(response)
            if not sub_items:
                self._add_comment(wi["id"], "Planner could not decompose further.",
                                  comment_type="analysis")
                self._complete_run(run_id)
                raise AgentSkipError("Empty decomposition")

            # Create sub work items
            created_ids = []
            cur = self.conn.cursor()
            for i, item in enumerate(sub_items):
                new_id = gen_id("wi")
                kind = item.get("kind", "task")
                # Force atoms at max depth
                if wi["planning_depth"] >= wi.get("max_planning_depth", 3) - 1:
                    kind = "atom"

                cur.execute("""
                    INSERT INTO work_items
                        (id, parent_id, root_id, kind, title, description,
                         status, creator_role, planning_depth)
                    VALUES (?, ?, ?, ?, ?, ?, 'draft', 'planner', ?)
                """, (
                    new_id, wi["id"], wi["root_id"], kind,
                    item["title"], item.get("description", ""),
                    wi["planning_depth"] + 1,
                ))

                # Declare files for atoms
                if kind == "atom" and item.get("files"):
                    for f in item["files"]:
                        cur.execute("""
                            INSERT INTO work_item_files (id, work_item_id, path, intent)
                            VALUES (?, ?, ?, ?)
                        """, (gen_id("wf"), new_id, f["path"], f["intent"]))

                created_ids.append(new_id)

                emit_event(
                    cur, "work_item_created", "work_item", new_id,
                    f"Planner created {kind}: {item['title']}",
                    work_item_id=new_id, actor_role="planner",
                )

            # Handle internal dependencies (depends_on_index)
            for i, item in enumerate(sub_items):
                dep_idx = item.get("depends_on_index")
                if dep_idx is not None and 0 <= dep_idx < len(created_ids):
                    cur.execute("""
                        INSERT INTO work_item_links (id, src_id, dst_id, link_type)
                        VALUES (?, ?, ?, 'depends_on')
                    """, (gen_id("lnk"), created_ids[i], created_ids[dep_idx]))

            self.conn.commit()

            self._add_comment(
                wi["id"],
                f"Decomposed into {len(sub_items)} sub-items: "
                + ", ".join(item["title"] for item in sub_items),
                comment_type="summary",
            )

            self._complete_run(run_id)
            return "planner_decomposed"

        except AgentSkipError:
            raise
        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            raise AgentRetryError(f"Planner failed: {e}")

    def _build_planner_prompt(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        parts = [
            f"# Decompose: {wi['title']} (kind: {wi['kind']}, depth: {wi['planning_depth']})",
            f"\n{wi['description'] or ''}",
        ]
        # Architect comments
        arch_comments = [c for c in ctx.comments if c["author_role"] == "architect"]
        if arch_comments:
            parts.append("\n## Architect guidance:")
            for c in arch_comments[:5]:
                parts.append(f"  - {c['body'][:500]}")

        # Judge feedback
        judge_rejections = [d for d in ctx.decisions
                           if d["decision_role"] == "judge" and d["decision"] == "rejected"]
        if judge_rejections:
            parts.append("\n## Judge feedback (MUST address):")
            for d in judge_rejections[:3]:
                related = [c for c in ctx.comments if c["id"] == d.get("comment_id")]
                if related:
                    parts.append(f"  - {related[0]['body'][:500]}")

        # Existing children
        if ctx.sibling_items:  # siblings here = children of same parent = our siblings
            pass
        cur = self.conn.cursor()
        cur.execute("""
            SELECT kind, title, status FROM work_items WHERE parent_id = ?
        """, (wi["id"],))
        existing = cur.fetchall()
        if existing:
            parts.append("\n## Already existing sub-items:")
            for e in existing:
                parts.append(f"  - [{e[0]}|{e[2]}] {e[1]}")

        parts.append("\n## Current workspace structure\n")
        parts.append(scan_workspace(self.workspace, max_files=100))

        return "\n".join(parts)

    def _parse_decomposition(self, response: str) -> list[dict]:
        """Extract JSON array from LLM response."""
        # Find JSON block
        start = response.find("[")
        end = response.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        try:
            items = json.loads(response[start:end])
            return [item for item in items if isinstance(item, dict) and "title" in item]
        except json.JSONDecodeError:
            return []


# ═══════════════════════════════════════════════════════════════════════════
# ARCHITECT AGENT — analyzes project, proposes structural improvements
# ═══════════════════════════════════════════════════════════════════════════

class ArchitectAgent(BaseAgentAdapter):
    queue_name = "architect_inbox"
    role = "architect"

    SYSTEM_PROMPT = """You are the Architect — you think about the project structure, not tasks.
You analyze the codebase and propose improvements: refactoring, module restructuring,
dependency cleanup, pattern enforcement, tech debt reduction.

You DON'T plan task execution order — the Planner does that.
You DO comment on task specifications to ensure architectural consistency.

When reviewing a work item:
- Comment on architectural implications
- Flag potential conflicts with other modules
- Suggest file organization if needed
- Note if the approach contradicts established patterns

Output format when proposing new work:
PROPOSAL: <title>
RATIONALE: <why this matters>
SCOPE: <which modules/files are affected>
PRIORITY: <1-5, 1=critical>

When commenting on existing tasks:
COMMENT: <your architectural note>
"""

    def __init__(self, orchestrator, llm: LLMClient, agent_id: str,
                 workspace_path: str = "."):
        super().__init__(orchestrator, llm, agent_id)
        self.workspace = Path(workspace_path)

    def execute(self, ctx: AgentContext) -> str:
        wi = ctx.work_item

        # Architect has two modes: commenting on existing items, or proposing new ones
        if wi["kind"] in ("vision", "initiative", "epic"):
            return self._comment_on_item(ctx)
        else:
            return self._comment_on_item(ctx)

    def _comment_on_item(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        run_id = self._create_run(wi["id"], "analyze")

        try:
            prompt = self._build_analysis_prompt(ctx)
            response = self._llm_step(
                run_id, 1, self.SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}]
            )

            # Parse and save comment
            self._add_comment(
                wi["id"], response,
                comment_type="analysis",
                structured={"from": "architect", "type": "architectural_review"},
            )

            # Check if architect proposes new work items
            proposals = self._parse_proposals(response)
            if proposals:
                cur = self.conn.cursor()
                for p in proposals:
                    new_id = gen_id("wi")
                    cur.execute("""
                        INSERT INTO work_items
                            (id, parent_id, root_id, kind, title, description,
                             status, priority, creator_role)
                        VALUES (?, ?, ?, 'task', ?, ?, 'draft', ?, 'architect')
                    """, (
                        new_id, wi["id"], wi["root_id"],
                        p["title"], p.get("rationale", ""),
                        p.get("priority", 100),
                    ))
                    emit_event(
                        cur, "architect_proposed", "work_item", new_id,
                        f"Architect proposed: {p['title']}",
                        work_item_id=new_id, actor_role="architect",
                    )
                self.conn.commit()

            self._complete_run(run_id)
            return "architect_commented"

        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            raise AgentRetryError(f"Architect failed: {e}")

    def _build_analysis_prompt(self, ctx: AgentContext) -> str:
        wi = ctx.work_item
        parts = [
            f"# Analyze: {wi['title']} (kind: {wi['kind']})",
            f"\n{wi['description'] or ''}",
        ]

        if ctx.work_item_files:
            parts.append("\n## Declared files:")
            for f in ctx.work_item_files:
                parts.append(f"  - `{f['path']}` ({f['intent']})")
                # Try to show current content summary
                path = self.workspace / f["path"]
                if path.exists():
                    content = path.read_text()
                    lines = len(content.split("\n"))
                    parts.append(f"    ({lines} lines)")

        if ctx.parent_chain:
            parts.append("\n## Context chain:")
            for p in ctx.parent_chain:
                parts.append(f"  - [{p['kind']}] {p['title']}")

        return "\n".join(parts)

    def _parse_proposals(self, response: str) -> list[dict]:
        proposals = []
        current = {}
        for line in response.split("\n"):
            line = line.strip()
            if line.upper().startswith("PROPOSAL:"):
                if current.get("title"):
                    proposals.append(current)
                current = {"title": line.split(":", 1)[1].strip()}
            elif line.upper().startswith("RATIONALE:"):
                current["rationale"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("PRIORITY:"):
                try:
                    current["priority"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    current["priority"] = 100
        if current.get("title"):
            proposals.append(current)
        return proposals


# ═══════════════════════════════════════════════════════════════════════════
# HR AGENT — prompt optimization (stub, activated by cron)
# ═══════════════════════════════════════════════════════════════════════════

class HRAgent(BaseAgentAdapter):
    """
    HR Agent — analyzes agent performance via logs, proposes prompt changes.
    Activated periodically (e.g. every hour), not on demand.
    Changes go through Judge before being applied by Forge.
    """
    queue_name = "hr_inbox"
    role = "hr"

    SYSTEM_PROMPT = """You are the HR Agent — you optimize agent behavior by analyzing logs.
You can see all system prompts (except your own) and all agent performance data.

Rules:
- Only propose changes when there's clear evidence of systematic issues.
- Small, targeted changes. Never rewrite an entire prompt at once.
- Each proposal must reference specific log evidence.
- Proposals go through Judge review before implementation.

Output format:
TARGET_ROLE: <which agent's prompt to change>
CHANGE_TYPE: append | replace_section | remove_section
EVIDENCE: <specific event_log entries or patterns>
PROPOSED_CHANGE: <the actual text change>
RATIONALE: <why this will improve performance>
"""

    def execute(self, ctx: AgentContext) -> str:
        # HR is periodic, not task-driven.
        # When triggered, it scans recent logs for patterns.
        run_id = self._create_run(ctx.work_item["id"], "hr_audit")

        try:
            # Build analysis from recent event log
            cur = self.conn.cursor()
            cur.execute("""
                SELECT event_type, actor_role, severity, message
                FROM event_log
                WHERE severity IN ('warn', 'error', 'fatal')
                ORDER BY id DESC LIMIT 100
            """)
            issues = [dict(r) for r in cur.fetchall()]

            # Get current prompt versions
            cur.execute("""
                SELECT role, version, content_ref FROM prompt_versions WHERE active = 1
            """)
            active_prompts = [dict(r) for r in cur.fetchall()]

            prompt = self._build_hr_prompt(issues, active_prompts)
            response = self._llm_step(
                run_id, 1, self.SYSTEM_PROMPT,
                [{"role": "user", "content": prompt}]
            )

            # Parse proposals and create atm_change work items
            changes = self._parse_hr_proposals(response)
            if changes:
                for change in changes:
                    new_id = gen_id("wi")
                    cur.execute("""
                        INSERT INTO work_items
                            (id, parent_id, root_id, kind, title, description,
                             status, creator_role)
                        VALUES (?, NULL, ?, 'atm_change', ?, ?, 'draft', 'hr')
                    """, (
                        new_id, new_id,
                        f"Prompt change: {change.get('target_role', 'unknown')}",
                        json.dumps(change),
                    ))
                    emit_event(
                        cur, "hr_change_proposed", "work_item", new_id,
                        f"HR proposed prompt change for {change.get('target_role')}",
                        work_item_id=new_id, actor_role="hr",
                    )
                self.conn.commit()

            self._complete_run(run_id)
            self._add_comment(
                ctx.work_item["id"],
                f"HR audit complete. {len(changes)} change(s) proposed.",
                comment_type="summary",
            )
            return "architect_commented"  # HR items go through judge separately

        except Exception as e:
            self._complete_run(run_id, "failed", str(e))
            raise AgentRetryError(f"HR failed: {e}")

    def _build_hr_prompt(self, issues: list, prompts: list) -> str:
        parts = ["# HR Audit — Recent Issues"]
        for issue in issues[:50]:
            parts.append(f"  [{issue['severity']}|{issue['actor_role']}] {issue['message'][:200]}")
        parts.append("\n# Active Prompts:")
        for p in prompts:
            parts.append(f"  - {p['role']} v{p['version']} → {p['content_ref']}")
        return "\n".join(parts)

    def _parse_hr_proposals(self, response: str) -> list[dict]:
        proposals = []
        current = {}
        for line in response.split("\n"):
            line = line.strip()
            if line.upper().startswith("TARGET_ROLE:"):
                if current.get("target_role"):
                    proposals.append(current)
                current = {"target_role": line.split(":", 1)[1].strip()}
            elif line.upper().startswith("CHANGE_TYPE:"):
                current["change_type"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("PROPOSED_CHANGE:"):
                current["proposed_change"] = line.split(":", 1)[1].strip()
            elif line.upper().startswith("RATIONALE:"):
                current["rationale"] = line.split(":", 1)[1].strip()
        if current.get("target_role"):
            proposals.append(current)
        return proposals


# ═══════════════════════════════════════════════════════════════════════════
# AGENT RUNNER — manages all agents in a single process
# ═══════════════════════════════════════════════════════════════════════════

class AgentRunner:
    """
    Runs multiple agent adapters in a round-robin fashion within one process.
    For production: use separate processes/threads per agent.
    """

    def __init__(self, orchestrator, llm: LLMClient):
        self.orch = orchestrator
        self.agents: list[BaseAgentAdapter] = [
            PlannerAgent(orchestrator, llm, "planner_01"),
            ArchitectAgent(orchestrator, llm, "architect_01"),
            JudgeAgent(orchestrator, llm, "judge_01"),
            ForgeAgent(orchestrator, llm, "forge_01"),
            ReviewerAgent(orchestrator, llm, "reviewer_01"),
        ]

    def run_cycle(self) -> int:
        """Run one cycle for each agent. Returns total items processed."""
        total = 0
        for agent in self.agents:
            try:
                if agent.run_once():
                    total += 1
            except (AgentSkipError, AgentRetryError):
                pass
            except Exception as e:
                print(f"[{agent.role}] Error: {e}")
        return total

    def run_loop(self, poll_interval: float = 3.0):
        """Continuous loop."""
        while True:
            try:
                processed = self.run_cycle()
                if processed == 0:
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                break
