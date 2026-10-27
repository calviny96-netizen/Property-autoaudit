"""Dashboard statistics.

SQL only gathers compact aggregates (distinct unique texts, so identical posts
count once). Everything a person reads - periods, buckets, budget ranges,
supply/demand gaps and the plain-language insights - is shaped here in Python.
"""
import calendar
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from db import connect
from runtime_cache import cached_value, daily_snapshot
from tenant import workspace_id
from matching_scope import document_scope_filter, history_scope_filter

router = APIRouter(prefix='/dashboard')
WIB = ZoneInfo('Asia/Jakarta')

CATEGORY_LABELS = {
    'house': 'House', 'warehouse': 'Warehouse', 'apartment': 'Apartment', 'shophouse': 'Shophouse', 'land': 'Land',
    'factory': 'Factory', 'office': 'Office', 'villa': 'Villa', 'commercial_building': 'Commercial building',
    'hotel': 'Hotel', 'unknown': 'Not detected',
}
TRANSACTION_LABELS = {'sale': 'Buy', 'rent': 'Rent', 'both': 'Buy or rent', 'unknown': 'Not stated'}
STATUS_LABELS = {'ready': 'Ready', 'on_hold': 'On hold', 'sold': 'Sold', 'deleted': 'Deleted'}
# Budget bands in rupiah, labelled the way agents write amounts ("jt" = juta, "M" = miliar); the last band is open ended.
BUDGET_BANDS = [
    (0, 500_000_000, '<500 jt'), (500_000_000, 1_000_000_000, '500 jt–1 M'), (1_000_000_000, 2_000_000_000, '1–2 M'),
    (2_000_000_000, 5_000_000_000, '2–5 M'), (5_000_000_000, 10_000_000_000, '5–10 M'), (10_000_000_000, None, '>10 M'),
]
# Rent budgets are yearly amounts (a monthly rent counts ×12): tens of millions up to "more than 200 jt".
RENT_BANDS = [
    (0, 20_000_000, '<20 jt/th'), (20_000_000, 50_000_000, '20–50 jt/th'), (50_000_000, 100_000_000, '50–100 jt/th'),
    (100_000_000, 200_000_000, '100–200 jt/th'), (200_000_000, None, '>200 jt/th'),
]
# Words the parser can pick up from a "lokasi:" line that are directions or filler, not places a buyer can be matched to.
GENERIC_LOCATIONS = {
    'sekitar', 'sekitarnya', 'timur', 'barat', 'utara', 'selatan', 'pusat', 'tengah', 'area', 'daerah', 'kota', 'lokasi',
    'bebas', 'mana', 'saja', 'dekat', 'raya', 'jalan', 'jl', 'jln', 'strategis', 'premium', 'prime', 'dll', 'dsb',
}


def is_place(name: str) -> bool:
    name = name.strip().lower()
    return len(name) >= 3 and name not in GENERIC_LOCATIONS
PERIODS = ('week', 'last_week', 'month', 'last_month', 'last30', 'custom')
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
PERIOD_LABELS = {'week': 'This week', 'last_week': 'Last week', 'month': 'This month', 'last_month': 'Last month',
                 'last30': 'Last 30 days'}
# How a period reads inside a sentence: "12 buyer requests last week".
PERIOD_PHRASES = {'week': 'this week', 'last_week': 'last week', 'month': 'this month', 'last_month': 'last month',
                  'last30': 'in the last 30 days'}
DEFAULT_PERIOD = 'last_week'


def pretty_day(day: date) -> str:
    return f'{day.day} {MONTHS[day.month - 1]} {day.year}'


def month_bounds(day: date):
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def resolve_period(period: str, date_from: str | None = None, date_to: str | None = None, today: date | None = None):
    """Inclusive Jakarta dates for a period, plus the equally long period it is compared with."""
    today = today or datetime.now(WIB).date()
    monday = today - timedelta(days=today.weekday())
    if period == 'week':
        start, end = monday, monday + timedelta(days=6)
    elif period == 'last_week':
        start, end = monday - timedelta(days=7), monday - timedelta(days=1)
    elif period == 'month':
        start, end = month_bounds(today)
    elif period == 'last_month':
        start, end = month_bounds(today.replace(day=1) - timedelta(days=1))
    elif period == 'last30':
        start, end = today - timedelta(days=29), today
    elif period == 'custom':
        try:
            start, end = date.fromisoformat(date_from or ''), date.fromisoformat(date_to or date_from or '')
        except ValueError:
            raise HTTPException(400, 'Invalid date.')
        if start > end:
            raise HTTPException(400, 'The start date cannot be after the end date.')
        if (end - start).days > 731:
            raise HTTPException(400, 'The date range can be at most 2 years.')
    else:
        raise HTTPException(400, 'Unknown period.')
    # A running period is compared on the same elapsed days, never against a full previous one.
    effective_end = min(end, today) if start <= today else end
    length = (effective_end - start).days + 1
    if period == 'month':
        prev_start = month_bounds(start - timedelta(days=1))[0]
        prev_end = min(prev_start + timedelta(days=length - 1), month_bounds(prev_start)[1])
    elif period == 'last_month':
        prev_start, prev_end = month_bounds(start - timedelta(days=1))
    elif period == 'week':
        prev_start = start - timedelta(days=7)
        prev_end = prev_start + timedelta(days=length - 1)
    else:
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=(end - start).days)
    return {'period': period, 'start': start, 'end': end, 'effective_end': effective_end,
            'prev_start': prev_start, 'prev_end': prev_end, 'days': (end - start).days + 1}


def bounds(start: date, end: date):
    """Inclusive dates -> naive [start, end) (the chat timestamps are stored in Jakarta time)."""
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def change(current: int, previous: int):
    """Percent change, or None when there is no earlier base to compare with."""
    if not previous:
        return None if not current else 100.0
    return round((current - previous) / previous * 100, 1)


def bucket_series(daily: dict, start: date, end: date):
    """Daily counts, or weekly (Monday) counts when the range is long enough to be unreadable per day."""
    span = (end - start).days + 1
    if span <= 62:
        days = [start + timedelta(days=i) for i in range(span)]
        return 'day', [d.isoformat() for d in days], [daily.get(d, 0) for d in days]
    weeks, labels = Counter(), []
    cursor = start - timedelta(days=start.weekday())
    while cursor <= end:
        labels.append(cursor)
        cursor += timedelta(days=7)
    for day, value in daily.items():
        weeks[day - timedelta(days=day.weekday())] += value
    return 'week', [d.isoformat() for d in labels], [weeks.get(d, 0) for d in labels]


def _yearly_rent(row):
    """A rent budget per year: the parsed rent price (per month ×12), else a rent buyer's plain amount."""
    amount, basis = row.get('rent_price_max') or row.get('rent_price_min'), row.get('rent_price_basis')
    if not amount:
        if row.get('transaction_type') != 'rent':
            return None
        amount, basis = row.get('price_max') or row.get('price_min'), row.get('price_basis')
    if not amount or basis == 'per_m2':
        return None
    return amount * 12 if basis == 'per_month' else amount


def _band(bands, counter, amount):
    for index, (low, high, _) in enumerate(bands):
        if amount >= low and (high is None or amount < high):
            counter[index] += 1
            return True
    return False


def budget_histogram(rows):
    """Count buyers per budget band: sale budgets by whole-property price, rent budgets by yearly rent
    (per-m² amounts are skipped). A buyer open to both counts on both sides when both amounts are known."""
    sale, rent, skipped = Counter(), Counter(), 0
    for row in rows:
        counted = False
        kind = row.get('transaction_type') or 'unknown'
        if kind != 'rent':
            amount = row.get('price_max') or row.get('price_min')
            if amount and row.get('price_basis') in (None, 'total'):
                counted = _band(BUDGET_BANDS, sale, amount)
        yearly = _yearly_rent(row) if kind in ('rent', 'both', 'unknown') else None
        if yearly:
            counted = _band(RENT_BANDS, rent, yearly) or counted
        if not counted:
            skipped += 1
    labels = [label for _, _, label in BUDGET_BANDS]
    rent_labels = [label for _, _, label in RENT_BANDS]
    return {'labels': labels, 'rent_labels': rent_labels, 'sale': [sale.get(i, 0) for i in range(len(labels))],
            'rent': [rent.get(i, 0) for i in range(len(rent_labels))], 'skipped': skipped}


# An opportunity needs real pressure: fewer than three ready listings per buyer (or none at all).
OPPORTUNITY_MIN_PRESSURE = 1 / 3


def supply_demand(demand: dict, supply: dict, labeler, minimum_demand: int = 1, limit: int = 8):
    """Where buyers ask more than the ready stock can answer, most pressing first. Types or places with plenty
    of stock (three or more ready listings per buyer) are not opportunities and are left out."""
    rows = []
    for key, buyers in demand.items():
        if buyers < minimum_demand:
            continue
        stock = supply.get(key, 0)
        pressure = round(buyers / max(stock, 1), 2)
        if stock and pressure < OPPORTUNITY_MIN_PRESSURE:
            continue
        rows.append({'key': key, 'label': labeler(key), 'buyers': buyers, 'listings': stock, 'pressure': pressure})
    rows.sort(key=lambda row: (-row['pressure'], -row['buyers'], row['label']))
    return rows[:limit]


def plural(count, word, many=None):
    return f"{count} {word if count == 1 else (many or word + 's')}"


def make_insights(ctx):
    """A few plain sentences that point out what changed; never more than five."""
    out = []
    phrase = ctx.get('period_phrase') or ctx['period_label'].lower()
    buyers, previous = ctx['kpi']['buyers'], ctx['kpi']['buyers_prev']
    delta = change(buyers, previous)
    if buyers == 0:
        out.append(f'No buyer requests {phrase}.')
    else:
        trend = ''
        if delta is not None and previous:
            if delta >= 500:
                trend = f', {buyers / previous:.1f}× the previous period'
            elif delta == 0:
                trend = ', the same as the previous period'
            else:
                trend = f', {"up" if delta > 0 else "down"} {abs(delta):g}% on the previous period'
        out.append(f"{plural(buyers, 'unique buyer request')} {phrase}{trend}.")
    cats = ctx['categories']
    if cats and buyers:
        top = cats[0]
        out.append(f"Most wanted: {top['label']} ({round(top['value'] / buyers * 100)}% of requests).")
    gaps = [g for g in ctx['gap_locations'] if g['buyers'] >= 2 and g['pressure'] >= 1.5]
    if gaps:
        g = gaps[0]
        stock = 'no ready listings' if not g['listings'] else f"only {plural(g['listings'], 'ready listing')}"
        out.append(f"Opportunity: {g['label']} is wanted by {g['buyers']} buyers, with {stock}.")
    new = ctx['matches']
    if new['total']:
        parts = [f"{new['hot']} Hot", f"{new['warm']} Warm"] + ([f"{new['custom']} Custom"] if new.get('custom') else [])
        out.append(f"{plural(new['total'], 'new match', 'new matches')} found in uploads this period ({', '.join(parts)}).")
    if ctx['status']['listing'].get('sold'):
        sold = ctx['status']['listing']['sold']
        out.append(f"{plural(sold, 'listing')} marked as Sold.")
    return out[:5]


def _daily(conn, document_type, start, end):
    low, high = bounds(start, end)
    scope_sql, scope_params = document_scope_filter(conn)
    rows = conn.execute(
        '''SELECT r.sent_at::date AS day, count(DISTINCT d.entity_id) AS n
           FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
           WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = %s AND e.status <> 'deleted'
             AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql + ' GROUP BY 1', [document_type, low, high, *scope_params]).fetchall()
    return {row['day']: row['n'] for row in rows}


def _distinct(conn, document_type, start, end):
    low, high = bounds(start, end)
    scope_sql, scope_params = document_scope_filter(conn)
    row = conn.execute(
        '''SELECT count(DISTINCT d.entity_id) AS n
           FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
           WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = %s AND e.status <> 'deleted'
             AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql, [document_type, low, high, *scope_params]).fetchone()
    return row['n']


def _match_counts(conn, start, end):
    low = datetime.combine(start, time.min, WIB)
    high = datetime.combine(end + timedelta(days=1), time.min, WIB)
    scope_sql, scope_params = history_scope_filter(conn)
    rows = conn.execute(
        '''SELECT (me.found_at AT TIME ZONE 'Asia/Jakarta')::date AS day, count(*) AS total,
                  count(*) FILTER (WHERE me.last_status = 'hot') AS hot,
                  count(*) FILTER (WHERE me.last_status = 'custom') AS custom
           FROM xm.match_events me
           WHERE me.company_id = current_setting('xm.workspace_id') AND me.source = 'import' AND me.active
             AND me.found_at >= %s AND me.found_at < %s''' + scope_sql + ' GROUP BY 1',
        [low, high, *scope_params]).fetchall()
    return {row['day']: (row['total'], row['hot'], row['custom']) for row in rows}


OVERVIEW_TTL = 30


def sales_label(group_by, key):
    """Chart label of a sales: the clean WhatsApp name, or the phone number as it is written in the bubble."""
    from workspace_cache import NO_SENDER
    from stock import sender_title
    if group_by == 'sender':
        return 'Unknown sender' if key == NO_SENDER else (sender_title(key) or key)
    return key or 'No phone number'


def _imports_version():
    """Imports and rebuilds invalidate read caches across API/worker processes."""
    with connect() as conn:
        row = conn.execute(
            '''SELECT count(*) AS n, max(finished_at) AS last,
                 (SELECT refreshed_at FROM xm.workspace_cache_state WHERE company_id=current_setting('xm.workspace_id')) AS refreshed
               FROM xm.imports
               WHERE company_id = current_setting('xm.workspace_id') AND status = 'completed' ''').fetchone()
    return row['n'], row['last'], row['refreshed']


@router.get('/overview')
def overview(period: Literal['week', 'last_week', 'month', 'last_month', 'last30', 'custom'] = DEFAULT_PERIOD,
             date_from: str = '', date_to: str = '', refresh: bool = False):
    return daily_snapshot(workspace_id(), ('overview', period, date_from, date_to),
                          lambda: compute_overview(period, date_from, date_to), refresh=refresh)


def compute_overview(period, date_from, date_to):
    info = resolve_period(period, date_from, date_to)
    start, end = info['start'], info['end']
    low, high = bounds(start, end)
    today = datetime.now(WIB).date()
    custom_label = pretty_day(start) if start == end else f'{pretty_day(start)} – {pretty_day(end)}'
    labels = {**PERIOD_LABELS, 'custom': custom_label}
    phrases = {**PERIOD_PHRASES, 'custom': f'on {custom_label}' if start == end else f'from {custom_label}'}
    with connect() as conn:
        ws = workspace_id()
        scope_sql, scope_params = document_scope_filter(conn)
        buyer_daily = _daily(conn, 'buyer_request', start, end)
        listing_daily = _daily(conn, 'property_listing', start, end)
        kpi = {
            'buyers': _distinct(conn, 'buyer_request', start, end),
            'listings': _distinct(conn, 'property_listing', start, end),
            'buyers_prev': _distinct(conn, 'buyer_request', info['prev_start'], info['prev_end']),
            'listings_prev': _distinct(conn, 'property_listing', info['prev_start'], info['prev_end']),
        }
        monday = today - timedelta(days=today.weekday())
        quick = {'this_week': _distinct(conn, 'buyer_request', monday, monday + timedelta(days=6)),
                 'this_month': _distinct(conn, 'buyer_request', *month_bounds(today))}
        categories = [{'key': row['k'], 'label': CATEGORY_LABELS.get(row['k'], row['k']), 'value': row['n']} for row in conn.execute(
            '''SELECT coalesce(d.primary_category, 'unknown') AS k, count(DISTINCT d.entity_id) AS n
               FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'buyer_request'
                 AND e.status <> 'deleted' AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql + ' GROUP BY 1 ORDER BY 2 DESC, 1', [low, high, *scope_params]).fetchall()]
        transactions = [{'key': row['k'], 'label': TRANSACTION_LABELS.get(row['k'], row['k']), 'value': row['n']} for row in conn.execute(
            '''SELECT d.transaction_type AS k, count(DISTINCT d.entity_id) AS n
               FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'buyer_request'
                 AND e.status <> 'deleted' AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql + ' GROUP BY 1 ORDER BY 2 DESC', [low, high, *scope_params]).fetchall()]
        locations = conn.execute(
            '''SELECT loc AS k, count(DISTINCT d.entity_id) AS n
               FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
               CROSS JOIN LATERAL unnest(d.locations) AS loc
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'buyer_request'
                 AND e.status <> 'deleted' AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql + ' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 80',
            [low, high, *scope_params]).fetchall()
        locations = [row for row in locations if is_place(row['k'])][:40]
        demand_locations = {row['k']: row['n'] for row in locations}
        stock_by_category = {row['k']: row['n'] for row in conn.execute(
            '''SELECT coalesce(d.primary_category, 'unknown') AS k, count(DISTINCT d.entity_id) AS n
               FROM xm.documents d JOIN xm.entities e ON e.entity_id = d.entity_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'property_listing'
                 AND e.status = 'ready' ''' + scope_sql + ' GROUP BY 1', scope_params).fetchall()}
        stock_by_location = {row['k']: row['n'] for row in conn.execute(
            '''SELECT loc AS k, count(DISTINCT d.entity_id) AS n
               FROM xm.documents d JOIN xm.entities e ON e.entity_id = d.entity_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
               CROSS JOIN LATERAL unnest(d.locations) AS loc
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'property_listing'
                 AND e.status = 'ready' AND loc = ANY(%s)''' + scope_sql + ' GROUP BY 1', [list(demand_locations), *scope_params]).fetchall()} if demand_locations else {}
        budget_rows = conn.execute(
            '''SELECT DISTINCT ON (d.entity_id) d.transaction_type, d.price_min, d.price_max, d.price_basis,
                      d.rent_price_min, d.rent_price_max, d.rent_price_basis
               FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id JOIN xm.entities e ON e.entity_id = d.entity_id
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active AND d.document_type = 'buyer_request'
                 AND e.status <> 'deleted' AND r.sent_at >= %s AND r.sent_at < %s''' + scope_sql + '''
               ORDER BY d.entity_id, r.sent_at DESC''', [low, high, *scope_params]).fetchall()
        status_rows = conn.execute(
            # Calculate ownership once; a correlated IN can rescan the chat
            # archive for every entity when the company has many records.
            '''WITH owned_entities AS MATERIALIZED (
                 SELECT DISTINCT d.entity_id FROM xm.documents d
                 JOIN xm.raw_messages r ON r.id=d.raw_message_id
                 WHERE d.company_id=current_setting('xm.workspace_id') AND d.active ''' + scope_sql + ''')
               SELECT e.document_type,e.status,count(*) AS n FROM xm.entities e
               WHERE e.company_id=%s AND e.entity_id IN (SELECT entity_id FROM owned_entities)
               GROUP BY 1,2''', [*scope_params,ws]).fetchall()
        match_daily = _match_counts(conn, start, end)
        match_prev = _match_counts(conn, info['prev_start'], info['prev_end'])
        pref = conn.execute('SELECT listing_group_by FROM xm.app_preferences WHERE company_id = %s', (ws,)).fetchone()
        group_by = pref['listing_group_by'] if pref else 'sender'
        from workspace_cache import GROUP_KEYS
        key_sql = GROUP_KEYS['sender' if group_by == 'sender' else 'phone']
        top_sales = conn.execute(
            f'''SELECT {key_sql} AS k, count(*) AS n, mode() WITHIN GROUP (ORDER BY d.contact_name) AS contact_name
                FROM xm.document_groups g JOIN xm.documents d ON d.id = g.group_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
                WHERE g.company_id = current_setting('xm.workspace_id') AND g.document_type = 'property_listing' AND g.status = 'ready'
                ''' + scope_sql + ' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10', scope_params).fetchall()
        latest = conn.execute(
            '''SELECT max(r.sent_at)::date AS day FROM xm.documents d JOIN xm.raw_messages r ON r.id = d.raw_message_id
               WHERE d.company_id = current_setting('xm.workspace_id') AND d.active''' + scope_sql, scope_params).fetchone()
    bucket, labels_x, buyers_series = bucket_series(buyer_daily, start, end)
    _, _, listings_series = bucket_series(listing_daily, start, end)
    match_days = {day: values[0] for day, values in match_daily.items()}
    match_hot = {day: values[1] for day, values in match_daily.items()}
    match_custom = {day: values[2] for day, values in match_daily.items()}
    _, _, match_series = bucket_series(match_days, start, end)
    _, _, hot_series = bucket_series(match_hot, start, end)
    _, _, custom_series = bucket_series(match_custom, start, end)
    match_total = sum(match_days.values())
    match_hot_total = sum(match_hot.values())
    match_custom_total = sum(match_custom.values())
    status = {'buyer': {}, 'listing': {}}
    for row in status_rows:
        status['buyer' if row['document_type'] == 'buyer_request' else 'listing'][row['status']] = row['n']
    gap_categories = supply_demand({item['key']: item['value'] for item in categories if item['key'] != 'unknown'},
                                   stock_by_category, lambda key: CATEGORY_LABELS.get(key, key))
    gap_locations = supply_demand(demand_locations, stock_by_location, lambda key: key.title(), minimum_demand=2)
    ctx = {
        'period_label': labels[period], 'period_phrase': phrases[period], 'kpi': kpi, 'categories': categories,
        'gap_locations': gap_locations,
        'matches': {'total': match_total, 'hot': match_hot_total, 'warm': match_total - match_hot_total - match_custom_total,
                    'custom': match_custom_total}, 'status': status,
    }
    prev_total = sum(values[0] for values in match_prev.values())
    return {
        'period': {'key': period, 'label': labels[period], 'date_from': start.isoformat(), 'date_to': end.isoformat(),
                   'compare_from': info['prev_start'].isoformat(), 'compare_to': info['prev_end'].isoformat(),
                   'days': info['days'], 'bucket': bucket, 'in_future': start > today},
        'latest_data_date': latest['day'].isoformat() if latest and latest['day'] else None,
        'quick': quick,
        'kpi': {**kpi, 'buyers_change': change(kpi['buyers'], kpi['buyers_prev']), 'listings_change': change(kpi['listings'], kpi['listings_prev']),
                'matches': match_total, 'matches_hot': match_hot_total, 'matches_custom': match_custom_total, 'matches_prev': prev_total,
                'matches_change': change(match_total, prev_total)},
        'trend': {'labels': labels_x, 'buyers': buyers_series, 'listings': listings_series,
                  'matches': match_series, 'matches_hot': hot_series, 'matches_custom': custom_series},
        'categories': categories, 'transactions': transactions,
        # Ready stock per property type (every listing counted once), for the stock chart.
        'stock_categories': [{'key': key, 'label': CATEGORY_LABELS.get(key, key), 'value': value}
                             for key, value in sorted(stock_by_category.items(), key=lambda item: (-item[1], item[0])) if key != 'unknown'],
        'locations': [{'label': row['k'].title(), 'value': row['n'], 'stock': stock_by_location.get(row['k'], 0)} for row in locations[:10]],
        'budget': budget_histogram(budget_rows),
        'gap_categories': gap_categories, 'gap_locations': gap_locations,
        'status': {side: {key: counts.get(key, 0) for key in ('ready', 'on_hold', 'sold', 'deleted')} for side, counts in status.items()},
        'top_sales': {'group_by': group_by, 'rows': [{'key': row['k'], 'label': sales_label(group_by, row['k']),
                                                         'contact_name': row['contact_name'], 'value': row['n']} for row in top_sales]},
        'matches': ctx['matches'],
        'insights': make_insights(ctx),
    }
