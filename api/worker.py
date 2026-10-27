import os
import time

from db import connect, ensure_schema
from tenant import workspace_scope
from ingest import process_import
from integration import process_next_export


def claim_job():
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM xm.imports
            WHERE status='queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED LIMIT 1
            """
        ).fetchone()
        if row:
            conn.execute("UPDATE xm.imports SET status='processing', started_at=now() WHERE id=%s", (row["id"],))
            conn.commit()
            return str(row["id"])
    return None


def finish_maintenance(row, status, result=None, error=None):
    """Complete the active job and atomically honor ownership changes during it.

    A completed job also clears "results out of date", but only for changes
    saved before it was queued (clear_reprocess_pending compares timestamps).
    """
    import json
    from matching_scope import clear_reprocess_pending, queue_scope_recompute

    with workspace_scope(row['company_id']), connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
        job = conn.execute('SELECT result FROM xm.maintenance_jobs WHERE id=%s AND company_id=%s FOR UPDATE',
                           (row['id'], row['company_id'])).fetchone()
        if not job:
            return
        changed = bool((job['result'] or {}).get('scope_changed'))
        conn.execute('''UPDATE xm.maintenance_jobs SET status=%s,result=%s::jsonb,error=%s,finished_at=now()
          WHERE id=%s AND company_id=%s''',
          (status, json.dumps(result or {}), error, row['id'], row['company_id']))
        if status == 'completed':
            clear_reprocess_pending(conn, row['id'])
        if changed:
            queue_scope_recompute(conn)
        conn.commit()


def process_maintenance():
    from reindex import reindex_documents
    from matcher import recompute_matches
    from progress import Progress, active
    with connect() as conn:
        row=conn.execute("SELECT id,company_id,progress FROM xm.maintenance_jobs WHERE status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if not row: return False
        conn.execute("UPDATE xm.maintenance_jobs SET status='processing', started_at=now() WHERE id=%s",(row['id'],))
        conn.commit()
    progress = None
    try:
        with workspace_scope(row['company_id']):
            progress = Progress('reprocess', row['id'], row['company_id'], (row.get('progress') or {}).get('items'))
            with active(progress):
                count=reindex_documents()
                matches=recompute_matches()
            progress.finish()  # learned durations/items live in the progress column
        finish_maintenance(row, 'completed', {'documents': count, 'matches': matches})
    except Exception as exc:
        if progress is not None:
            with workspace_scope(row['company_id']):
                progress.fail(exc)
        finish_maintenance(row, 'failed', error=str(exc))
    return True


if __name__ == "__main__":
    ensure_schema()
    from entities import migrate_v4
    migrate_v4()
    with connect() as conn:
        # A restart mid-job: maintenance jobs start over; imports resume (ingest skips the messages it already stored).
        conn.execute("UPDATE xm.maintenance_jobs SET status='queued' WHERE status='processing'")
        conn.execute("UPDATE xm.imports SET status='queued', error=NULL WHERE status='processing'")
        conn.commit()
    # Jev review runs call an external API for minutes: they get their own thread so imports,
    # exports and maintenance jobs in the loop below are never kept waiting.
    import threading
    from ai_review import review_loop
    threading.Thread(target=review_loop, name='jev-review', daemon=True).start()
    while True:
        if process_maintenance():
            continue
        job_id = claim_job()
        if job_id:
            try:
                process_import(job_id)
            except Exception as exc:
                print(f"Import {job_id} failed: {exc}", flush=True)
        elif process_next_export():
            continue
        else:
            time.sleep(float(os.getenv("WORKER_POLL_SECONDS", "2")))
