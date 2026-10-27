"""Company-owned eligibility for automatic matching, independent of uploader."""
from dataclasses import dataclass

from fastapi import HTTPException

from parser import normalize_phone
from search_terms import normalize_terms, search_filter
from stock import tracked_phones
from tenant import workspace_id


@dataclass(frozen=True)
class MatchingScope:
    mode: str
    terms: tuple[str, ...] = ()
    phones: frozenset[str] = frozenset()

    def includes(self, document):
        if self.mode == 'company':
            # Signatures may contain the company name even though the parser
            # deliberately removes them from normalized matching/embedding text.
            text = '\n'.join(str(document.get(key) or '') for key in
                             ('raw_text', 'normalized_text')).casefold()
            return not self.terms or any(term in text for term in self.terms)
        contacts = list(document.get('contact_phones') or [])
        if document.get('contact_phone'):
            contacts.append(document['contact_phone'])
        return any(normalize_phone(phone) in self.phones for phone in contacts)

    def allows_pair(self, buyer, listing):
        # One side must belong to the monitored company/sales. The other side
        # remains the marketplace, so an external buyer can match company stock.
        return self.includes(buyer) or self.includes(listing)


def load_matching_scope(conn, company_id, mode=None, *, require_sales=True):
    row = conn.execute(
        'SELECT matching_mode, search_terms FROM xm.app_preferences WHERE company_id=%s',
        (company_id,)).fetchone() or {'matching_mode': 'company', 'search_terms': []}
    mode = mode or row['matching_mode']
    if mode not in ('company', 'sales'):
        raise HTTPException(400, 'Matching mode must be company or sales.')
    if mode == 'sales':
        phones = frozenset(normalize_phone(phone) for phone in tracked_phones(conn, company_id))
        if not phones and require_sales:
            raise HTTPException(400, 'Watchlist is empty. Add your sales numbers under Sales stock before using Sales mode.')
        return MatchingScope(mode, phones=phones)
    return MatchingScope(mode, terms=tuple(term.casefold() for term in normalize_terms(row['search_terms'])))


def document_scope_filter(conn):
    """Mandatory company ownership filter for document/raw-message aliases d/r."""
    scope = load_matching_scope(conn, workspace_id(), require_sales=False)
    if scope.mode == 'company':
        # contact_name can fall back to the WhatsApp sender when a signature
        # is absent; the sender is not evidence of company ownership.
        clause, params = search_filter(list(scope.terms), include_contact=False)
    else:
        if not scope.phones:
            return ' AND false', []
        clause = ' AND (d.contact_phones && %s::text[] OR d.contact_phone=ANY(%s))'
        params = [list(scope.phones), list(scope.phones)]
    return clause, params


def history_scope_filter(conn, direction=None):
    """Filter the chosen source by current rules; aggregate views accept either side.

    Events are undirected history. A company listing can match an external buyer,
    but that buyer must never become a source in the company buyer gallery.
    Uncorrelated subqueries let PostgreSQL hash the monitored entities once.
    """
    clause, params = document_scope_filter(conn)
    if not clause or clause == ' AND false':
        return clause, params
    monitored = '''SELECT d.entity_id FROM xm.documents d
      JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.active''' + clause
    if direction is not None:
        source = {'buyer': 'buyer_entity', 'property': 'listing_entity'}[direction]
        return f' AND me.{source} IN ({monitored})', params
    return f' AND (me.buyer_entity IN ({monitored}) OR me.listing_entity IN ({monitored}))', params * 2


def queue_scope_recompute(conn):
    """Refresh cached pairs after ownership changes in the same settings transaction.

    A processing job may already have read the previous rules. Flag it for one
    successor after completion, preserving the single active job per company.
    """
    import uuid

    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
    scope = load_matching_scope(conn, workspace_id(), require_sales=False)
    if scope.mode == 'sales' and not scope.phones:
        return None
    if not conn.execute("SELECT 1 FROM xm.documents WHERE company_id=current_setting('xm.workspace_id') AND active LIMIT 1").fetchone():
        return None
    active = conn.execute("SELECT * FROM xm.maintenance_jobs WHERE company_id=current_setting('xm.workspace_id') AND status IN ('queued','processing') ORDER BY created_at LIMIT 1 FOR UPDATE").fetchone()
    if active:
        if active['status'] == 'processing':
            return conn.execute("""UPDATE xm.maintenance_jobs
              SET result=coalesce(result,'{}'::jsonb) || '{"scope_changed":true}'::jsonb
              WHERE id=%s RETURNING *""", (active['id'],)).fetchone()
        return active
    import json
    from progress import queued_progress
    # The job shows "about N minutes" from the moment it is queued (rates learned from the last run).
    return conn.execute('INSERT INTO xm.maintenance_jobs(id, progress) VALUES(%s, %s::jsonb) RETURNING *',
                        (uuid.uuid4(), json.dumps(queued_progress(conn, workspace_id(), 'reprocess', reprocess_items(conn))))).fetchone()


def reprocess_items(conn):
    """Planned work of a reprocess: messages to parse and buyer requests to match inside the analysis window."""
    from analysis_window import in_window_sql, window_start
    start = window_start(conn)
    row = conn.execute("""SELECT count(*) FILTER (WHERE r.duplicate_of IS NULL) AS messages,
          (SELECT count(DISTINCT coalesce(d.content_key, d.id::text)) FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
            WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.document_type='buyer_request'""" + in_window_sql('r') + """) AS requests
        FROM xm.raw_messages r WHERE r.company_id=current_setting('xm.workspace_id')""" + in_window_sql('r'), (start, start)).fetchone()
    return {'parse': int(row['messages'] or 0), 'match': int(row['requests'] or 0), 'index': 0, 'finish': 1}


def _has_active_documents(conn):
    return bool(conn.execute(
        "SELECT 1 FROM xm.documents WHERE company_id=current_setting('xm.workspace_id') AND active LIMIT 1").fetchone())


def mark_reprocess_pending(conn, reason):
    """Saving settings no longer rebuilds results: remember that (and why) they are out of date.

    Runs inside the caller's settings transaction. ``reason`` is a short English
    phrase shown to people ("Glossary updated"); repeated reasons are kept once.
    Returns False when the workspace has no active data, so nothing is pending.
    """
    if not _has_active_documents(conn):
        return False
    reason = str(reason or '').strip()[:120] or 'Settings changed'
    # A job clears what was pending when it was queued (worker: since <= created_at).
    # Locking the active job keeps the worker from starting it before this commit.
    active = conn.execute("""SELECT status, created_at FROM xm.maintenance_jobs
      WHERE company_id=current_setting('xm.workspace_id') AND status IN ('queued','processing')
      ORDER BY created_at LIMIT 1 FOR UPDATE""").fetchone()
    if active and active['status'] == 'processing':
        # The running job may already have read the old settings: stay pending after it.
        since = 'clock_timestamp()'
    elif active:
        # A queued job reads settings when it starts, so it covers this change too.
        since = 'coalesce(reprocess_pending_since, %(queued)s)'
    else:
        since = 'coalesce(reprocess_pending_since, clock_timestamp())'
    conn.execute(
        f"""UPDATE xm.app_preferences SET reprocess_pending_since={since},
             reprocess_reasons=CASE WHEN reprocess_reasons @> jsonb_build_array(%(reason)s::text) THEN reprocess_reasons
                                    ELSE reprocess_reasons || jsonb_build_array(%(reason)s::text) END
           WHERE company_id=current_setting('xm.workspace_id')""",
        {'reason': reason, 'queued': active['created_at'] if active else None})
    return True


def clear_reprocess_pending(conn, job_id):
    """After a reprocess job completed: clear what it covered.

    Changes saved after the job was queued stay pending, because the job may
    already have read the earlier settings.
    """
    return conn.execute(
        """UPDATE xm.app_preferences ap SET reprocess_pending_since=NULL, reprocess_reasons='[]'::jsonb
           FROM xm.maintenance_jobs j
           WHERE j.id=%s AND j.company_id=ap.company_id AND j.status='completed'
             AND ap.company_id=current_setting('xm.workspace_id')
             AND ap.reprocess_pending_since IS NOT NULL AND ap.reprocess_pending_since<=j.created_at""",
        (job_id,)).rowcount > 0


def latest_maintenance_job(conn):
    return conn.execute("""SELECT * FROM xm.maintenance_jobs WHERE company_id=current_setting('xm.workspace_id')
      ORDER BY created_at DESC LIMIT 1""").fetchone()


def reprocess_status(conn):
    """``{pending, since, reasons, job}`` for the "matches are not updated yet" banner. The caller commits."""
    row = conn.execute("""SELECT reprocess_pending_since, reprocess_reasons FROM xm.app_preferences
      WHERE company_id=current_setting('xm.workspace_id')""").fetchone() or {}
    since = row.get('reprocess_pending_since')
    if since is not None:
        # Self-healing: a completed reprocess queued after the change covers it,
        # even when the worker that finished it did not clear the flag.
        covered = conn.execute("""SELECT id FROM xm.maintenance_jobs WHERE company_id=current_setting('xm.workspace_id')
          AND status='completed' AND created_at>=%s ORDER BY created_at LIMIT 1""", (since,)).fetchone()
        if covered and clear_reprocess_pending(conn, covered['id']):
            since, row = None, {'reprocess_reasons': []}
    reasons = row.get('reprocess_reasons') or []
    return {'pending': since is not None, 'since': since,
            'reasons': [str(item) for item in reasons] if since is not None else [], 'job': latest_maintenance_job(conn),
            **analysis_window_status(conn)}


def analysis_window_status(conn):
    """``analysis_from`` (effective ISO date), the months setting, and whether any stored message is older."""
    from analysis_window import window_settings
    window = window_settings(conn)
    older = conn.execute("""SELECT 1 FROM xm.raw_messages r WHERE r.company_id=current_setting('xm.workspace_id')
      AND r.sent_at < %s LIMIT 1""", (window['analysis_from'],)).fetchone()
    return {'analysis_from': window['analysis_from'].isoformat(), 'analysis_window_months': window['analysis_window_months'],
            'analysis_pinned': window['pinned'], 'has_older_data': older is not None}


def extend_analysis_window(conn, start):
    """Pin the window start to ``start`` (a date) when it is earlier than the effective start. Returns True when changed."""
    from analysis_window import today_wib, window_settings
    if start > today_wib():
        raise HTTPException(400, 'The date cannot be in the future.')
    # "All dates" asks from 1970: never pin earlier than the oldest stored message.
    oldest = conn.execute("SELECT min(sent_at)::date AS day FROM xm.raw_messages WHERE company_id=current_setting('xm.workspace_id')").fetchone()
    if oldest and oldest['day'] and start < oldest['day']:
        start = oldest['day']
    if start >= window_settings(conn)['analysis_from']:
        return False
    conn.execute("UPDATE xm.app_preferences SET analysis_from=%s, updated_at=now() WHERE company_id=current_setting('xm.workspace_id')", (start,))
    return True


def queue_reprocess(conn, analysis_from=None):
    """The explicit "Reprocess now": one active job per company, a successor when one is running.

    ``analysis_from`` (a date) extends the analysis window back to that day before queueing, so later
    reprocesses keep covering it."""
    scope = load_matching_scope(conn, workspace_id(), require_sales=False)
    if analysis_from is not None:
        extend_analysis_window(conn, analysis_from)
    if scope.mode == 'sales' and not scope.phones:
        load_matching_scope(conn, workspace_id(), require_sales=True)  # explains the empty watchlist
    job = queue_scope_recompute(conn)
    if job is None and not _has_active_documents(conn):
        # Nothing uploaded (or everything removed): there is nothing to bring up to date.
        conn.execute("""UPDATE xm.app_preferences SET reprocess_pending_since=NULL, reprocess_reasons='[]'::jsonb
          WHERE company_id=current_setting('xm.workspace_id')""")
    return job
