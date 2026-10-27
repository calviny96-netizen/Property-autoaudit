"""Live progress and time estimates of background jobs (reprocess runs and chat uploads).

A ``Progress`` is bound to one row of ``xm.maintenance_jobs`` or ``xm.imports`` and writes its
``progress`` JSON column at most every two seconds through its own short-lived connection, so the
long job transaction never has to commit for the UI to see where it is::

    {stage, stage_label, done, total, percent, started_at, elapsed_s, eta_s, rate}

Percent is overall (0-100) using per-stage weights; the ETA is the remaining work of the current
stage at its observed rate plus the later stages at the rates learned from the last completed job
of the company (``result.durations`` / ``result.items``), falling back to built-in rates.
"""
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from db import connect

# The job the worker is running right now; pipelines pick it up when no progress is passed explicitly.
_current = ContextVar('xm_progress', default=None)


def current():
    """The active Progress of this thread (a NoProgress when none is running)."""
    return _current.get() or NoProgress()


@contextmanager
def active(progress):
    token = _current.set(progress)
    try:
        yield progress
    finally:
        _current.reset(token)

# Items per second when nothing was learned yet (parse = parse + embed per message).
DEFAULT_RATES = {'read': 5000.0, 'parse': 100.0, 'index': 400.0, 'match': 15.0, 'finish': 1.0}
# Seconds of fixed overhead of a stage with no items (the finish stage mostly).
DEFAULT_FIXED = {'finish': 5.0, 'index': 1.0}
STAGE_LABELS = {'read': 'Reading the chat file', 'parse': 'Reading and analysing messages',
                'index': 'Updating the search index', 'match': 'Finding matches', 'finish': 'Finishing up'}
STAGES = {'reprocess': ('parse', 'index', 'match', 'finish'), 'import': ('read', 'parse', 'index', 'match', 'finish')}
TABLES = {'reprocess': 'xm.maintenance_jobs', 'import': 'xm.imports'}
WRITE_EVERY_S = 2.0


def _utc(moment=None):
    return (moment or datetime.now(timezone.utc)).isoformat()


def learned_rates(conn, company_id, kind):
    """{stage: items per second} from the last completed job of this kind; stages without data use the defaults."""
    table = TABLES[kind]
    row = conn.execute(f"""SELECT progress FROM {table}
      WHERE company_id=%s AND status='completed' AND progress ? 'durations' ORDER BY finished_at DESC NULLS LAST LIMIT 1""",
                       (company_id,)).fetchone()
    rates = dict(DEFAULT_RATES)
    if not row:
        return rates
    durations = (row['progress'] or {}).get('durations') or {}
    items = (row['progress'] or {}).get('items') or {}
    for stage, seconds in durations.items():
        count = float(items.get(stage) or 0)
        if count > 0 and float(seconds) > 0:
            # Short runs are noisy: never learn a rate faster than ten times the default.
            rates[stage] = min(count / float(seconds), DEFAULT_RATES.get(stage, 100.0) * 10)
    return rates


def stage_seconds(stage, count, rates):
    return float(count or 0) / max(rates.get(stage, 100.0), 0.01) + DEFAULT_FIXED.get(stage, 0.0)


def estimate_seconds(conn, company_id, kind, items):
    """Up-front estimate of a whole job: ``items`` = {stage: item count}. Always a positive number."""
    rates = learned_rates(conn, company_id, kind)
    total = sum(stage_seconds(stage, items.get(stage, 0), rates) for stage in STAGES[kind])
    return max(1.0, round(total, 1))


def queued_progress(conn, company_id, kind, items):
    """The progress JSON of a job that is still waiting: 0 %, with the estimate."""
    eta = estimate_seconds(conn, company_id, kind, items)
    first = STAGES[kind][0]
    return {'stage': 'queued', 'stage_label': 'Waiting to start', 'done': 0, 'total': int(items.get(first, 0) or 0),
            'percent': 0, 'started_at': None, 'elapsed_s': 0, 'eta_s': eta, 'rate': None, 'estimate_s': eta,
            'items': {k: int(v) for k, v in items.items()}}


def write_progress(conn, kind, job_id, payload):
    conn.execute(f"UPDATE {TABLES[kind]} SET progress=%s::jsonb WHERE id=%s", (json.dumps(payload), job_id))


class Progress:
    """Reports the stages of one job. ``items`` are the planned item counts per stage (may be refined later)."""

    def __init__(self, kind, job_id, company_id, items=None, started_at=None):
        self.kind, self.job_id, self.company_id = kind, str(job_id), company_id
        self.stages = STAGES[kind]
        self.items = {stage: int((items or {}).get(stage, 0) or 0) for stage in self.stages}
        self.started = time.monotonic()
        self.started_at = started_at or datetime.now(timezone.utc)
        self.durations, self.counts = {}, {}
        self.stage, self.stage_started, self.done, self.total = None, None, 0, 0
        self.last_write = 0.0
        with connect() as conn:
            self.rates = learned_rates(conn, company_id, kind)
            conn.execute(f"UPDATE {TABLES[kind]} SET started_at=coalesce(started_at, now()) WHERE id=%s", (self.job_id,))
            conn.commit()

    # -- planning -------------------------------------------------------------
    def plan(self, **items):
        """Refine the item counts once they are known (e.g. the number of buyer requests)."""
        for stage, count in items.items():
            if stage in self.items:
                self.items[stage] = int(count or 0)

    def _estimated(self, stage, count=None):
        return stage_seconds(stage, self.items[stage] if count is None else count, self.rates)

    def _weights(self):
        seconds = {stage: max(self._estimated(stage), 0.5) for stage in self.stages}
        total = sum(seconds.values()) or 1.0
        return {stage: value / total for stage, value in seconds.items()}

    # -- reporting ------------------------------------------------------------
    def report(self, stage, done, total, force=False):
        now = time.monotonic()
        if stage != self.stage:
            self._close_stage(now)
            self.stage, self.stage_started = stage, now
            if total:
                self.items[stage] = max(self.items.get(stage, 0), int(total))
            force = True
        self.done, self.total = int(done), int(total)
        if not force and now - self.last_write < WRITE_EVERY_S:
            return
        self.last_write = now
        self._write(self.snapshot(now))

    def _close_stage(self, now):
        if self.stage is None or self.stage_started is None:
            return
        self.durations[self.stage] = round(self.durations.get(self.stage, 0.0) + now - self.stage_started, 2)
        self.counts[self.stage] = max(self.counts.get(self.stage, 0), self.done)

    def snapshot(self, now=None):
        now = now or time.monotonic()
        weights = self._weights()
        index = self.stages.index(self.stage) if self.stage in self.stages else 0
        fraction = (self.done / self.total) if self.total else 0.0
        percent = sum(weights[s] for s in self.stages[:index]) + weights.get(self.stage, 0.0) * min(fraction, 1.0)
        stage_elapsed = now - (self.stage_started or now)
        rate = self.done / stage_elapsed if stage_elapsed > 0.5 and self.done else None
        if self.total:
            remaining_here = (self.total - self.done) / rate if rate else self._estimated(self.stage, self.total - self.done)
        else:
            remaining_here = max(self._estimated(self.stage) - stage_elapsed, 0.0) if self.stage else 0.0
        eta = remaining_here + sum(self._estimated(s) for s in self.stages[index + 1:])
        return {'stage': self.stage, 'stage_label': STAGE_LABELS.get(self.stage, self.stage or ''), 'done': self.done,
                'total': self.total, 'percent': int(round(min(percent, 0.99) * 100)), 'started_at': _utc(self.started_at),
                'elapsed_s': int(now - self.started), 'eta_s': int(round(max(eta, 0))),
                'rate': round(rate, 2) if rate else None, 'items': dict(self.items)}

    def finish(self):
        """Mark the job complete (100 %) and return the learned ``{'durations', 'items'}`` for the job result."""
        now = time.monotonic()
        self._close_stage(now)
        self.stage, self.stage_started = None, None
        learned = {'durations': dict(self.durations), 'items': {s: int(self.counts.get(s) or self.items.get(s) or 0) for s in self.stages}}
        payload = {'stage': 'done', 'stage_label': 'Done', 'done': self.total, 'total': self.total, 'percent': 100,
                   'started_at': _utc(self.started_at), 'elapsed_s': int(now - self.started), 'eta_s': 0, 'rate': None, **learned}
        self._write(payload)
        return learned

    def fail(self, error=''):
        now = time.monotonic()
        self._close_stage(now)
        payload = {**self.snapshot(now), 'stage': 'failed', 'stage_label': 'Failed', 'eta_s': None, 'error': str(error)[:300]}
        self._write(payload)

    def _write(self, payload):
        try:
            with connect() as conn:
                write_progress(conn, self.kind, self.job_id, payload)
                conn.commit()
        except Exception as exc:  # progress is advisory: never fail the job because of it
            print(f'Progress not written for {self.kind} {self.job_id}: {exc}', flush=True)


class NoProgress:
    """Stands in when a pipeline runs without a tracked job (tests, command-line scripts)."""
    def plan(self, **items): pass
    def report(self, *args, **kwargs): pass
    def finish(self): return {'durations': {}, 'items': {}}
    def fail(self, error=''): pass
