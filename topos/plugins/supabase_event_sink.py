"""Optional event sink that mirrors topos runner events to Supabase.

Enabled only when all three env vars are set:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    TOPOS_RUN_ID

Otherwise ``make_sink()`` returns ``None`` and the Runner treats it as if
no sink were configured. This file is safe to import on a host without the
``supabase`` package — the SDK import is deferred until ``make_sink()``
actually runs, and is also gracefully skipped if the package is missing.

The sink buffers events in an in-memory queue and flushes them from a
single background daemon thread, so calls into ``emit()`` from the runner
never block on network I/O. Any flush failure is logged and swallowed —
**the sink is best-effort by design**; we never let live-viz infrastructure
break a real topos run.

Three side-effects per batch:
1. INSERT new rows into ``topos_run_events`` (the append-only stream that
   Supabase Realtime subscribes to).
2. UPSERT into ``topos_run_tasks`` so the per-task state table always
   reflects the latest known status, even if events are missed.
3. UPDATE ``topos_runs.status`` on ``run_started`` / ``run_finished``.
"""

from __future__ import annotations

import datetime
import os
import queue
import threading
from typing import Callable

_BATCH_MAX = 32
_FLUSH_INTERVAL_S = 0.5


def make_sink() -> Callable[[dict], None] | None:
    """Return an emit callable, or None if env is missing / supabase unavailable.

    A None return value is the explicit signal to the Runner that no sink
    is configured; the Runner falls back to its zero-op default."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    run_id = os.environ.get("TOPOS_RUN_ID")
    if not (url and key and run_id):
        return None
    try:
        from supabase import create_client
    except ImportError:
        print(
            "[supabase_event_sink] SUPABASE_URL+TOPOS_RUN_ID set but "
            "`supabase` package not installed; sink disabled",
            flush=True,
        )
        return None

    client = create_client(url, key)
    q: queue.Queue = queue.Queue()
    SENTINEL = object()

    def _flush(batch: list[dict]) -> None:
        try:
            client.table("topos_run_events").insert(
                [{"run_id": run_id, "type": e["type"], "payload": e} for e in batch]
            ).execute()
        except Exception as exc:  # noqa: BLE001 — sink must never crash runner
            print(f"[supabase_event_sink] events insert failed: {exc!r}", flush=True)

        # Fold rows that share (task_id, iter) within this batch: Postgres
        # rejects ON CONFLICT batches with duplicate conflict keys. Merging
        # (later non-None values overwrite earlier) preserves started_at
        # from a task_started even when its task_completed is in the same
        # batch — the completed row simply omits started_at so the merge
        # leaves it intact. In production this is rare (events flush every
        # 0.5s while tasks run for seconds-to-minutes), but in tests and
        # very fast tasks it matters for visual fidelity.
        task_rows_by_pk: dict[tuple[str, int], dict] = {}
        for e in batch:
            row = _task_row_from_event(e, run_id)
            if row is None:
                continue
            pk = (row["task_id"], row["iter"])
            existing = task_rows_by_pk.get(pk)
            if existing is None:
                task_rows_by_pk[pk] = row
            else:
                for k, v in row.items():
                    if v is not None:
                        existing[k] = v
        if task_rows_by_pk:
            try:
                client.table("topos_run_tasks").upsert(
                    list(task_rows_by_pk.values()), on_conflict="run_id,task_id,iter"
                ).execute()
            except Exception as exc:  # noqa: BLE001
                print(f"[supabase_event_sink] tasks upsert failed: {exc!r}", flush=True)

        for e in batch:
            patch = _runs_patch_from_event(e)
            if not patch:
                continue
            try:
                client.table("topos_runs").update(patch).eq("id", run_id).execute()
            except Exception as exc:  # noqa: BLE001
                print(f"[supabase_event_sink] runs update failed: {exc!r}", flush=True)

    def _worker() -> None:
        while True:
            try:
                first = q.get(timeout=_FLUSH_INTERVAL_S)
            except queue.Empty:
                continue
            if first is SENTINEL:
                return
            batch: list[dict] = [first]
            while len(batch) < _BATCH_MAX:
                try:
                    nxt = q.get_nowait()
                except queue.Empty:
                    break
                if nxt is SENTINEL:
                    _flush(batch)
                    return
                batch.append(nxt)
            _flush(batch)

    threading.Thread(target=_worker, name="topos-supabase-sink", daemon=True).start()

    def emit(event: dict) -> None:
        q.put(event)

    return emit


def _task_row_from_event(e: dict, run_id: str) -> dict | None:
    """Translate an event into a topos_run_tasks UPSERT payload, or None
    if this event type doesn't map to a per-task row.

    Every NOT NULL column (``deps``, ``status``, ``cost_usd``,
    ``artifact_urls``) is set explicitly. Reason: PostgREST batches an
    upsert into a single INSERT whose column list is the *union* across
    all rows in the batch — columns missing on one row are filled with
    NULL, not the DB default. So if task_started omits ``cost_usd``
    while a sibling task_completed includes it, the started row's
    INSERT crashes on the NOT NULL constraint. Be explicit per row."""
    t = e.get("type")
    if t == "task_started":
        return {
            "run_id":        run_id,
            "task_id":       e["task_id"],
            "iter":          e.get("iter", 0),
            "kind":          e.get("kind", "agent"),
            "backend":       e.get("backend"),
            "deps":          e.get("deps", []),
            "status":        "running",
            "started_at":    _isoformat(e.get("ts")),
            "cost_usd":      0,
            "artifact_urls": {},
        }
    if t in ("task_completed", "task_failed", "task_skipped"):
        status = {
            "task_completed": "done",
            "task_failed":    "failed",
            "task_skipped":   "skipped",
        }[t]
        row: dict = {
            "run_id":        run_id,
            "task_id":       e["task_id"],
            "iter":          e.get("iter", 0),
            "kind":          e.get("kind", "agent"),
            "deps":          e.get("deps", []),
            "status":        status,
            "ended_at":      _isoformat(e.get("ts")),
            "duration_s":    e.get("duration_s"),
            "cost_usd":      e.get("cost_usd", 0),
            "artifact_urls": e.get("artifact_urls", {}),
        }
        if "result_summary" in e:
            row["result_summary"] = e["result_summary"]
        return row
    return None


def _runs_patch_from_event(e: dict) -> dict | None:
    """Translate a run-level event into a topos_runs UPDATE patch."""
    t = e.get("type")
    if t == "run_started":
        return {"status": "running", "started_at": _isoformat(e.get("ts"))}
    if t == "run_finished":
        patch = {
            "status":             "done" if e.get("success") else "failed",
            "ended_at":           _isoformat(e.get("ts")),
            "total_cost_usd":     e.get("total_cost_usd", 0),
        }
        if "final_judge_passed" in e:
            patch["final_judge_passed"] = e["final_judge_passed"]
        if "final_judge_score" in e:
            patch["final_judge_score"] = e["final_judge_score"]
        return patch
    return None


def _isoformat(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
