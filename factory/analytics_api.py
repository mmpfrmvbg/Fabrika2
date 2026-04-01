"""
Read-only агрегаты для GET /api/analytics (только SELECT).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import EventType, WorkItemKind

_ET_FORGE_START = EventType.FORGE_STARTED.value
_ET_JUDGE_OK = EventType.JUDGE_APPROVED.value

_ATOM_KINDS = (WorkItemKind.ATOM.value, WorkItemKind.ATM_CHANGE.value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def resolve_period(period: str) -> tuple[str | None, str, str]:
    """
    Возвращает (since_iso_utc или None для all, period_label, bucket_mode hour|day).
    """
    p = (period or "24h").strip().lower()
    now = _utc_now()
    if p == "all":
        return None, "all", "day"
    if p == "7d":
        return _iso(now - timedelta(days=7)), "7d", "day"
    if p == "30d":
        return _iso(now - timedelta(days=30)), "30d", "day"
    return _iso(now - timedelta(hours=24)), "24h", "hour"


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    t = str(s).strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


def _wi_counts_by_kind(
    conn: sqlite3.Connection,
    *,
    kinds: tuple[str, ...],
    since: str | None,
) -> dict[str, int]:
    """
    total / completed / in_progress / failed для набора kind.
    since=None → без нижней границы по created_at.
    """
    ph = ",".join("?" * len(kinds))
    q = f"""
        SELECT status, COUNT(*) AS c
        FROM work_items
        WHERE kind IN ({ph})
          AND status != 'archived'
    """
    params: list[Any] = list(kinds)
    if since is not None:
        q += " AND created_at >= ?"
        params.append(since)
    q += " GROUP BY status"
    rows = conn.execute(q, params).fetchall()
    by_status = {r["status"]: int(r["c"]) for r in rows}
    done = by_status.get("done", 0)
    failed = by_status.get("cancelled", 0) + by_status.get("judge_rejected", 0)
    terminal = {"done", "cancelled", "judge_rejected", "archived"}
    in_prog = sum(c for st, c in by_status.items() if st not in terminal)
    total = sum(by_status.values())
    return {
        "total": total,
        "completed": done,
        "in_progress": in_prog,
        "failed": failed,
    }


def _cycle_times_for_done_atoms(
    conn: sqlite3.Connection,
    *,
    completion_since: str | None,
) -> tuple[list[float], int]:
    """
    Длительности (сек): первый forge.started → последний judge.approved.
    first_pass_count среди done в окне (retry_count=0).
    """
    q = """
        SELECT id, retry_count
        FROM work_items
        WHERE kind IN (?, ?)
          AND status = 'done'
    """
    params: list[Any] = list(_ATOM_KINDS)
    if completion_since is not None:
        q += " AND updated_at >= ?"
        params.append(completion_since)
    rows = conn.execute(q, params).fetchall()
    durations: list[float] = []
    n_fp = 0
    for r in rows:
        wi_id = r["id"]
        if int(r["retry_count"] or 0) == 0:
            n_fp += 1
        t0r = conn.execute(
            """
            SELECT MIN(event_time) AS t FROM event_log
            WHERE work_item_id = ? AND event_type = ?
            """,
            (wi_id, _ET_FORGE_START),
        ).fetchone()
        t1r = conn.execute(
            """
            SELECT MAX(event_time) AS t FROM event_log
            WHERE work_item_id = ? AND event_type = ?
            """,
            (wi_id, _ET_JUDGE_OK),
        ).fetchone()
        t0 = _parse_ts(t0r["t"] if t0r else None)
        t1 = _parse_ts(t1r["t"] if t1r else None)
        if t0 and t1 and t1 >= t0:
            sec = (t1 - t0).total_seconds()
            if math.isfinite(sec) and sec >= 0:
                durations.append(float(sec))
    return durations, n_fp


def _stage_stats(
    conn: sqlite3.Connection,
    *,
    since: str | None,
) -> dict[str, dict[str, Any]]:
    q = """
        SELECT role, status, started_at, finished_at
        FROM runs
        WHERE role IN ('forge', 'reviewer', 'judge')
    """
    params: list[Any] = []
    if since is not None:
        q += " AND started_at >= ?"
        params.append(since)
    rows = conn.execute(q, params).fetchall()

    buckets: dict[str, list[float]] = {"forge": [], "review": [], "judge": []}
    counts: dict[str, int] = {"forge": 0, "review": 0, "judge": 0}
    fails: dict[str, int] = {"forge": 0, "review": 0, "judge": 0}
    role_map = {"forge": "forge", "reviewer": "review", "judge": "judge"}

    for r in rows:
        key = role_map.get(r["role"])
        if not key:
            continue
        counts[key] += 1
        if r["status"] == "failed":
            fails[key] += 1
        sa, fa = r["started_at"], r["finished_at"]
        if not sa or not fa:
            continue
        t0, t1 = _parse_ts(sa), _parse_ts(fa)
        if t0 and t1 and t1 >= t0:
            buckets[key].append((t1 - t0).total_seconds())

    out: dict[str, dict[str, Any]] = {}
    for key in ("forge", "review", "judge"):
        ds = buckets[key]
        avg = sum(ds) / len(ds) if ds else 0.0
        c = counts[key]
        fr = (fails[key] / c) if c else 0.0
        out[key] = {
            "avg_duration_sec": int(round(avg)),
            "count": c,
            "fail_rate": round(fr, 4),
        }
    return out


def _llm_totals(
    conn: sqlite3.Connection,
    *,
    since: str | None,
) -> dict[str, int]:
    q = """
        SELECT
            COALESCE(SUM(request_count), 0) AS calls,
            COALESCE(SUM(tokens_in), 0) AS tin,
            COALESCE(SUM(tokens_out), 0) AS tout
        FROM api_usage
        WHERE 1=1
    """
    params: list[Any] = []
    if since is not None:
        q += " AND created_at >= ?"
        params.append(since)
    r = conn.execute(q, params).fetchone()
    return {
        "total_calls": int(r["calls"]),
        "total_tokens_in": int(r["tin"]),
        "total_tokens_out": int(r["tout"]),
    }


def _throughput(
    conn: sqlite3.Connection,
    *,
    since: str | None,
    bucket_el: str,
    bucket_au: str,
) -> list[dict[str, Any]]:
    el_q = f"""
        SELECT {bucket_el} AS bucket, COUNT(*) AS c
        FROM event_log el
        INNER JOIN work_items wi ON wi.id = el.work_item_id
        WHERE el.event_type = ?
          AND wi.kind IN (?, ?)
    """
    params: list[Any] = [_ET_JUDGE_OK, _ATOM_KINDS[0], _ATOM_KINDS[1]]
    if since is not None:
        el_q += " AND el.event_time >= ?"
        params.append(since)
    el_q += " GROUP BY bucket ORDER BY bucket"
    el_rows = {r["bucket"]: int(r["c"]) for r in conn.execute(el_q, params).fetchall()}

    au_q = f"""
        SELECT {bucket_au} AS bucket,
               COALESCE(SUM(request_count), 0) AS c
        FROM api_usage
        WHERE 1=1
    """
    au_p: list[Any] = []
    if since is not None:
        au_q += " AND created_at >= ?"
        au_p.append(since)
    au_q += " GROUP BY bucket ORDER BY bucket"
    au_rows = {r["bucket"]: int(r["c"]) for r in conn.execute(au_q, au_p).fetchall()}

    keys = sorted(set(el_rows) | set(au_rows))
    return [
        {
            "hour": k,
            "atoms_completed": el_rows.get(k, 0),
            "llm_calls": au_rows.get(k, 0),
        }
        for k in keys
    ]


def compute_analytics(conn: sqlite3.Connection, period: str) -> dict[str, Any]:
    since, label, bucket_mode = resolve_period(period)
    completion_since = since

    visions = _wi_counts_by_kind(conn, kinds=(WorkItemKind.VISION.value,), since=since)
    atoms = _wi_counts_by_kind(conn, kinds=_ATOM_KINDS, since=since)

    durations, n_fp = _cycle_times_for_done_atoms(conn, completion_since=completion_since)
    avg_c = sum(durations) / len(durations) if durations else 0.0
    med_c = _median(durations)

    done_row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM work_items
        WHERE kind IN (?, ?) AND status = 'done'
        """
        + (" AND updated_at >= ?" if completion_since else ""),
        (*_ATOM_KINDS, completion_since) if completion_since else _ATOM_KINDS,
    ).fetchone()
    done_n = int(done_row["c"])
    first_pass = (n_fp / done_n) if done_n else 0.0
    retry_rate = (1.0 - first_pass) if done_n else 0.0

    stages = _stage_stats(conn, since=since)
    llm = _llm_totals(conn, since=since)

    if bucket_mode == "hour":
        b_el = "strftime('%Y-%m-%dT%H', el.event_time)"
        b_au = "strftime('%Y-%m-%dT%H', created_at)"
    else:
        b_el = "strftime('%Y-%m-%d', el.event_time)"
        b_au = "strftime('%Y-%m-%d', created_at)"

    throughput = _throughput(conn, since=since, bucket_el=b_el, bucket_au=b_au)

    avg_calls_atom = (
        (llm["total_calls"] / done_n) if done_n else 0.0
    )

    return {
        "period": label,
        "visions": visions,
        "atoms": {
            "total": atoms["total"],
            "completed": atoms["completed"],
            "in_progress": atoms["in_progress"],
            "failed": atoms["failed"],
            "avg_cycle_time_sec": int(round(avg_c)),
            "median_cycle_time_sec": int(round(med_c)) if med_c is not None else 0,
            "first_pass_rate": round(first_pass, 4),
            "retry_rate": round(retry_rate, 4),
        },
        "stages": stages,
        "llm": {
            "total_calls": llm["total_calls"],
            "total_tokens_in": llm["total_tokens_in"],
            "total_tokens_out": llm["total_tokens_out"],
            "avg_calls_per_atom": round(avg_calls_atom, 2),
        },
        "throughput": throughput,
    }
