"""Сбор сигналов из БД → improvement_candidates → Vision (self-improvement loop)."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from .db import gen_id
from .logging import FactoryLogger
from .models import EventType, Role, Severity
from .work_items import WorkItemOps


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fix_target_for_event_type(event_type: str) -> str:
    et = (event_type or "").lower()
    if "forge" in et:
        return "code"
    if "judge" in et or "review" in et:
        return "prompt"
    return "infra"


def _fix_target_for_reason(reason: str | None) -> str:
    r = (reason or "other").lower()
    if r in ("quality", "security", "architecture", "tests"):
        return "code"
    if r == "scope":
        return "prompt"
    return "code"


def _risk_for_candidate(
    fix_target: str, affected_role: str | None
) -> str:
    ar = (affected_role or "").lower()
    if fix_target == "infra":
        return "high"
    if fix_target == "prompt" and ar == "judge":
        return "high"
    if fix_target == "prompt":
        return "medium"
    return "low"


class FactoryIntrospector:
    def collect_signals(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        out.extend(self._collect_failure_clusters(conn))
        out.extend(self._collect_review_patterns(conn))
        out.extend(self._collect_retry_hotspots(conn))
        out.extend(self._collect_metric_anomalies(conn))
        out.extend(self._collect_hr_proposals(conn))
        return out

    def _collect_failure_clusters(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS cnt,
                   GROUP_CONCAT(work_item_id) AS wis,
                   GROUP_CONCAT(actor_role) AS roles
            FROM event_log
            WHERE severity IN ('error', 'fatal')
              AND datetime(event_time) > datetime('now', '-7 days')
            GROUP BY event_type
            HAVING cnt >= 2
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()
        out = []
        for r in rows:
            et = r["event_type"]
            cnt = int(r["cnt"] or 0)
            wis = (r["wis"] or "").split(",") if r["wis"] else []
            wis = [x for x in wis if x]
            ft = _fix_target_for_event_type(et)
            sev = min(cnt / 10.0, 1.0)
            out.append(
                {
                    "source_type": "failure_cluster",
                    "source_ref": et,
                    "title": f"Fix recurring {et} ({cnt} occurrences)",
                    "description": f"Repeated error/fatal events of type {et} in the last 7 days.",
                    "evidence": {
                        "event_type": et,
                        "count": cnt,
                        "work_item_ids": wis[:20],
                        "roles": r["roles"] or "",
                    },
                    "fix_target": ft,
                    "affected_role": None,
                    "affected_files": None,
                    "frequency": cnt,
                    "severity_score": sev,
                    "impact_score": 0.7,
                    "confidence": 0.6,
                }
            )
        return out

    def _collect_review_patterns(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        # decisions: verdict (not legacy decision column)
        rows = conn.execute(
            """
            SELECT COALESCE(d.reason_code, 'unknown') AS rc, COUNT(*) AS cnt,
                   GROUP_CONCAT(d.work_item_id) AS wis
            FROM decisions d
            WHERE d.verdict = 'rejected'
              AND datetime(d.created_at) > datetime('now', '-7 days')
            GROUP BY COALESCE(d.reason_code, 'unknown')
            HAVING cnt >= 2
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()
        out = []
        for r in rows:
            rc = r["rc"]
            cnt = int(r["cnt"] or 0)
            ft = _fix_target_for_reason(rc)
            out.append(
                {
                    "source_type": "review_pattern",
                    "source_ref": rc,
                    "title": f"Address repeated rejection reason: {rc} ({cnt}×)",
                    "description": f"Multiple decisions rejected with reason_code={rc} in the last 7 days.",
                    "evidence": {
                        "reason_code": rc,
                        "count": cnt,
                        "work_item_ids": (r["wis"] or "").split(",")[:20],
                    },
                    "fix_target": ft,
                    "affected_role": "reviewer",
                    "affected_files": None,
                    "frequency": cnt,
                    "severity_score": min(cnt / 8.0, 1.0),
                    "impact_score": 0.65,
                    "confidence": 0.55,
                }
            )
        return out

    def _collect_retry_hotspots(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT wi.id, wi.title,
              (SELECT COUNT(*) FROM runs r WHERE r.work_item_id = wi.id AND r.role = 'forge') AS forge_n,
              (SELECT COUNT(*) FROM review_results rr
               WHERE rr.work_item_id = wi.id AND rr.verdict != 'approved') AS rr_n
            FROM work_items wi
            WHERE wi.kind = 'atom'
              AND (
                (SELECT COUNT(*) FROM runs r WHERE r.work_item_id = wi.id AND r.role = 'forge') > 2
                OR (SELECT COUNT(*) FROM review_results rr
                    WHERE rr.work_item_id = wi.id AND rr.verdict != 'approved') > 1
              )
              AND wi.status NOT IN ('done', 'cancelled', 'archived')
            """
        ).fetchall()
        out = []
        for r in rows:
            wid = r["id"]
            fn = int(r["forge_n"] or 0)
            rr_n = int(r["rr_n"] or 0)
            out.append(
                {
                    "source_type": "retry_hotspot",
                    "source_ref": wid,
                    "title": f"Stabilize atom retry hotspot: {r['title'][:80]}",
                    "description": f"Atom {wid} has forge runs={fn}, non-approved reviews={rr_n}.",
                    "evidence": {
                        "work_item_id": wid,
                        "forge_runs": fn,
                        "review_rejections": rr_n,
                    },
                    "fix_target": "code",
                    "affected_role": "forge",
                    "affected_files": None,
                    "frequency": max(fn, rr_n),
                    "severity_score": 0.75,
                    "impact_score": 0.6,
                    "confidence": 0.5,
                }
            )
        return out

    def _collect_metric_anomalies(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        row = conn.execute(
            """
            WITH recent AS (
                SELECT AVG(
                    (julianday(finished_at) - julianday(started_at)) * 86400.0
                ) AS avg_sec
                FROM runs
                WHERE role = 'forge' AND status = 'completed'
                  AND finished_at IS NOT NULL AND started_at IS NOT NULL
                  AND datetime(started_at) > datetime('now', '-7 days')
            ),
            previous AS (
                SELECT AVG(
                    (julianday(finished_at) - julianday(started_at)) * 86400.0
                ) AS avg_sec
                FROM runs
                WHERE role = 'forge' AND status = 'completed'
                  AND finished_at IS NOT NULL AND started_at IS NOT NULL
                  AND datetime(started_at) > datetime('now', '-14 days')
                  AND datetime(started_at) <= datetime('now', '-7 days')
            )
            SELECT r.avg_sec AS recent_avg, p.avg_sec AS prev_avg
            FROM recent r, previous p
            """
        ).fetchone()
        if not row or row["recent_avg"] is None or row["prev_avg"] is None:
            return []
        recent = float(row["recent_avg"])
        prev = float(row["prev_avg"])
        if prev <= 0 or recent <= prev * 1.5:
            return []
        pct = int(round((recent / prev - 1.0) * 100))
        return [
            {
                "source_type": "metric_anomaly",
                "source_ref": "forge_duration_7d_vs_prev",
                "title": f"Forge performance degraded by ~{pct}% (7d vs prior 7d)",
                "description": "Average completed forge run duration increased significantly vs the previous week.",
                "evidence": {
                    "recent_avg_sec": recent,
                    "previous_avg_sec": prev,
                    "pct_delta": pct,
                },
                "fix_target": "infra",
                "affected_role": "forge",
                "affected_files": None,
                "frequency": 1,
                "severity_score": 0.55,
                "impact_score": 0.7,
                "confidence": 0.45,
            }
        ]

    def _collect_hr_proposals(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM work_item_queue WHERE queue_name = 'hr_inbox'"
        ).fetchone()["c"]
        if int(n or 0) < 1:
            return []
        return [
            {
                "source_type": "hr_proposal",
                "source_ref": "hr_inbox_pending",
                "title": "HR inbox has pending prompt/policy work",
                "description": "There are work items queued for HR — consider prompt or policy updates.",
                "evidence": {"queue": "hr_inbox", "depth": int(n)},
                "fix_target": "prompt",
                "affected_role": "hr",
                "affected_files": None,
                "frequency": int(n),
                "severity_score": 0.4,
                "impact_score": 0.55,
                "confidence": 0.4,
            }
        ]

    def deduplicate(
        self, conn: sqlite3.Connection, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        fresh: list[dict[str, Any]] = []
        for c in candidates:
            st = c.get("source_type")
            sr = c.get("source_ref")
            if st is None:
                continue
            key = (st, sr or "")
            row = conn.execute(
                """
                SELECT id, frequency FROM improvement_candidates
                WHERE source_type = ? AND IFNULL(source_ref, '') = IFNULL(?, '')
                  AND status IN ('proposed', 'approved')
                """,
                (st, sr),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE improvement_candidates
                    SET frequency = frequency + 1
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                continue
            fresh.append(c)
        return fresh

    def score_and_insert(
        self,
        conn: sqlite3.Connection,
        candidates: list[dict[str, Any]],
        logger: FactoryLogger | None = None,
    ) -> int:
        n = 0
        for c in candidates:
            cid = gen_id("ic")
            rl = _risk_for_candidate(
                str(c.get("fix_target", "code")), c.get("affected_role")
            )
            ev = c.get("evidence") or {}
            if not isinstance(ev, dict):
                ev = {"raw": ev}
            conn.execute(
                """
                INSERT INTO improvement_candidates (
                    id, source_type, source_ref, title, description, evidence,
                    fix_target, affected_role, affected_files,
                    frequency, severity_score, impact_score, confidence,
                    status, risk_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    cid,
                    c["source_type"],
                    c.get("source_ref"),
                    c["title"],
                    c["description"],
                    json.dumps(ev, ensure_ascii=False),
                    c["fix_target"],
                    c.get("affected_role"),
                    json.dumps(c.get("affected_files"), ensure_ascii=False)
                    if c.get("affected_files")
                    else None,
                    int(c.get("frequency", 1)),
                    float(c.get("severity_score", 0.5)),
                    float(c.get("impact_score", 0.5)),
                    float(c.get("confidence", 0.5)),
                    rl,
                ),
            )
            n += 1
            if logger:
                logger.log(
                    EventType.INTROSPECT_CANDIDATE_CREATED,
                    "improvement_candidate",
                    cid,
                    f"introspect candidate {c.get('source_type')}: {c.get('title', '')[:120]}",
                    severity=Severity.INFO,
                    actor_role=Role.ORCHESTRATOR.value,
                    payload={"candidate_id": cid, "source_type": c.get("source_type")},
                )
        return n

    def auto_approve_low_risk(self, conn: sqlite3.Connection, logger: FactoryLogger | None = None) -> int:
        cur = conn.execute(
            """
            SELECT id FROM improvement_candidates
            WHERE status = 'proposed'
              AND risk_level = 'low'
              AND priority_score > 0.6
              AND datetime(created_at) < datetime('now', '-1 hour')
            """
        )
        ids = [r["id"] for r in cur.fetchall()]
        for cid in ids:
            conn.execute(
                """
                UPDATE improvement_candidates
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?
                WHERE id = ?
                """,
                (_utc_now_iso(), "system", cid),
            )
            if logger:
                logger.log(
                    EventType.INTROSPECT_AUTO_APPROVED,
                    "improvement_candidate",
                    cid,
                    "auto-approved low-risk improvement candidate",
                    severity=Severity.INFO,
                    actor_role=Role.ORCHESTRATOR.value,
                    payload={"candidate_id": cid},
                )
        return len(ids)

    def _has_active_vision_under_half_done(self, conn: sqlite3.Connection) -> bool:
        for vision in conn.execute(
            """
            SELECT id FROM work_items
            WHERE kind = 'vision' AND status NOT IN ('done', 'cancelled', 'archived')
            """
        ).fetchall():
            vid = vision["id"]
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM work_items WHERE kind = 'atom' AND root_id = ?",
                (vid,),
            ).fetchone()["c"]
            if int(total or 0) == 0:
                continue
            done = conn.execute(
                """
                SELECT COUNT(*) AS c FROM work_items
                WHERE kind = 'atom' AND root_id = ?
                  AND status IN ('done', 'cancelled', 'archived')
                """,
                (vid,),
            ).fetchone()["c"]
            if int(done) / float(total) < 0.5:
                return True
        return False

    def convert_approved_to_vision(
        self, conn: sqlite3.Connection, logger: FactoryLogger | None = None
    ) -> str | None:
        if self._has_active_vision_under_half_done(conn):
            return None
        row = conn.execute(
            """
            SELECT * FROM improvement_candidates
            WHERE status = 'approved' AND vision_id IS NULL
            ORDER BY priority_score DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return self._convert_row_to_vision(conn, dict(row), logger)

    def convert_one(
        self, conn: sqlite3.Connection, candidate_id: str, logger: FactoryLogger | None = None
    ) -> str:
        row = conn.execute(
            "SELECT * FROM improvement_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if not row:
            raise ValueError("candidate not found")
        r = dict(row)
        if r["status"] != "approved":
            raise ValueError("candidate must be approved")
        if r.get("vision_id"):
            return r["vision_id"]
        return self._convert_row_to_vision(conn, r, logger)

    def _convert_row_to_vision(
        self, conn: sqlite3.Connection, row: dict[str, Any], logger: FactoryLogger | None
    ) -> str:
        cid = row["id"]
        if not logger:
            logger = FactoryLogger(conn)
        ops = WorkItemOps(conn, logger)
        vid = ops.create_vision(
            str(row["title"]),
            str(row["description"] or ""),
            auto_commit=False,
        )
        conn.execute(
            """
            UPDATE improvement_candidates
            SET status = 'converted', vision_id = ?
            WHERE id = ?
            """,
            (vid, cid),
        )
        logger.log(
            EventType.INTROSPECT_VISION_CREATED,
            "work_item",
            vid,
            f"Vision from improvement candidate {cid}",
            work_item_id=vid,
            severity=Severity.INFO,
            actor_role=Role.ORCHESTRATOR.value,
            payload={"candidate_id": cid, "vision_id": vid},
        )
        return vid


def run_introspect_tick(
    conn: sqlite3.Connection,
    logger: FactoryLogger,
    *,
    tick_counter: int,
) -> None:
    """
    Полный цикл introspect (вызывать из оркестратора).
    Каждые N тиков (env FACTORY_INTROSPECT_TICKS, default 20).
    """
    raw = os.environ.get("FACTORY_INTROSPECT_TICKS", "").strip()
    try:
        every = int(raw) if raw else 20
    except ValueError:
        every = 20
    if every < 1:
        every = 1
    if tick_counter % every != 0:
        return
    intro = FactoryIntrospector()
    candidates = intro.collect_signals(conn)
    candidates = intro.deduplicate(conn, candidates)
    if candidates:
        intro.score_and_insert(conn, candidates, logger)
    intro.auto_approve_low_risk(conn, logger)
    intro.convert_approved_to_vision(conn, logger)
