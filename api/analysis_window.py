"""The analysis window: how far back messages are parsed, embedded and matched.

Raw messages are always stored. Documents of messages sent before the window start keep their rows,
but they are not (re)parsed by a reprocess and get no matches. ``analysis_from`` (app_preferences)
pins the start; NULL means a rolling window of ``analysis_window_months`` calendar months, counted
from the first day of the month in WIB (today 21 Oct, 4 months -> 1 June).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo('Asia/Jakarta')
DEFAULT_MONTHS = 4
MIN_MONTHS, MAX_MONTHS = 1, 24


def today_wib() -> date:
    return datetime.now(WIB).date()


def rolling_start(months: int, today: date | None = None) -> date:
    today = today or today_wib()
    months = max(MIN_MONTHS, min(MAX_MONTHS, int(months or DEFAULT_MONTHS)))
    index = today.year * 12 + (today.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)


def window_settings(conn, company_id=None):
    """{'analysis_from': date, 'analysis_window_months': int, 'pinned': bool} of the workspace."""
    where = 'company_id=%s' if company_id else "company_id=current_setting('xm.workspace_id')"
    params = (company_id,) if company_id else ()
    preferences = conn.execute(f'SELECT analysis_from FROM xm.app_preferences WHERE {where}', params).fetchone() or {}
    settings = conn.execute(f'SELECT analysis_window_months FROM xm.match_settings WHERE {where}', params).fetchone() or {}
    months = int(settings.get('analysis_window_months') or DEFAULT_MONTHS)
    pinned = preferences.get('analysis_from')
    start = rolling_start(months)
    if pinned is not None and pinned < start:
        start = pinned
    return {'analysis_from': start, 'analysis_window_months': months, 'pinned': pinned is not None and pinned < rolling_start(months)}


def window_start(conn, company_id=None) -> date:
    return window_settings(conn, company_id)['analysis_from']


def in_window_sql(alias='r'):
    """SQL fragment (one %s = window start) keeping messages inside the window; undated messages stay in."""
    return f' AND ({alias}.sent_at IS NULL OR {alias}.sent_at >= %s)'


def message_in_window(sent_at, start: date) -> bool:
    if sent_at is None:
        return True
    moment = sent_at.date() if isinstance(sent_at, datetime) else sent_at
    return moment >= start
