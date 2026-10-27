"""Stock monitor for sales phone numbers a company wants to follow.

A listing "belongs" to a sales when the number appears in the signature of its
bubble. The tracked numbers are a company-level setting; counts are recorded
automatically after every import and whenever a listing changes status.
"""
import io
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from auth import current_user
from db import connect
from entities import GROUP_KEY
from parser import normalize_phone
from tenant import workspace_id
from runtime_cache import invalidate_stats
from sheet_import import MAX_BYTES, SheetImportError, read_sheets

router = APIRouter(prefix='/stock')
WIB = ZoneInfo('Asia/Jakarta')
MAX_TRACKED = 150
STATUS_KEYS = ('ready', 'on_hold', 'sold', 'deleted')


def parse_phones(value):
    """``"6282233744657, 081202310022"`` -> normalised, de-duplicated, validated numbers."""
    parts = re.split(r'[,;\n]+', value) if isinstance(value, str) else list(value or [])
    phones, invalid = [], []
    for part in parts:
        part = str(part).strip()
        if not part:
            continue
        phone = normalize_phone(part)
        if re.fullmatch(r'628\d{8,12}', phone):
            phones.append(phone)
        else:
            invalid.append(part)
    if invalid:
        raise HTTPException(400, 'Invalid phone number: ' + ', '.join(invalid[:5]) + '. Use an Indonesian number (08… or 628…); separate several with commas.')
    phones = list(dict.fromkeys(phones))
    if len(phones) > MAX_TRACKED:
        raise HTTPException(400, f'At most {MAX_TRACKED} sales numbers per company.')
    return phones


def tracked_phones(conn, company_id):
    return [row['phone'] for row in conn.execute(
        'SELECT phone FROM xm.tracked_sales WHERE company_id = %s ORDER BY created_at, phone', (company_id,)).fetchall()]


def phone_counts(conn, company_id, phones):
    """Unique listings per phone and status (texts that are identical count once)."""
    counts = {phone: dict.fromkeys(STATUS_KEYS, 0) for phone in phones}
    if not phones:
        return counts
    for row in conn.execute(
        '''SELECT p.phone, e.status, count(DISTINCT e.entity_id) AS n
           FROM unnest(%s::text[]) AS p(phone)
           JOIN xm.documents d ON d.company_id = %s AND d.document_type = 'property_listing' AND d.active
                AND (d.contact_phones @> ARRAY[p.phone] OR d.contact_phone = p.phone)
           JOIN xm.entities e ON e.entity_id = d.entity_id
           GROUP BY p.phone, e.status''', (phones, company_id)).fetchall():
        counts[row['phone']][row['status']] = row['n']
    return counts


def snapshot_stock(conn, company_id, event_type, phones=None, import_id=None, agent_name=None, note=None):
    """Append one log row per tracked phone with its current counts and the change since last time."""
    tracked = tracked_phones(conn, company_id)
    phones = [phone for phone in (phones if phones is not None else tracked) if phone in tracked]
    if not phones:
        return 0
    counts = phone_counts(conn, company_id, phones)
    previous = {row['phone']: row for row in conn.execute(
        '''SELECT DISTINCT ON (phone) phone, total, ready FROM xm.stock_log
           WHERE company_id = %s AND phone = ANY(%s) ORDER BY phone, logged_at DESC, id DESC''', (company_id, phones)).fetchall()}
    rows = []
    for phone in phones:
        c = counts[phone]
        total = c['ready'] + c['on_hold'] + c['sold']
        before = previous.get(phone)
        rows.append((company_id, phone, event_type, import_id, agent_name, total, c['ready'], c['on_hold'], c['sold'], c['deleted'],
                     total - before['total'] if before else 0, c['ready'] - before['ready'] if before else 0, note))
    with conn.cursor() as cur:
        cur.executemany(
            '''INSERT INTO xm.stock_log(company_id, phone, event_type, import_id, agent_name, total, ready, on_hold, sold, deleted,
                                         delta_total, delta_ready, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', rows)
    return len(rows)


def snapshot_for_entities(conn, company_id, entity_ids):
    """After a status change: log only the tracked sales whose listings were touched."""
    tracked = tracked_phones(conn, company_id)
    if not tracked:
        return 0
    touched = conn.execute(
        '''SELECT DISTINCT p.phone FROM xm.documents d
           CROSS JOIN LATERAL unnest(d.contact_phones || coalesce(ARRAY[d.contact_phone], '{}')) AS p(phone)
           WHERE d.company_id = %s AND d.entity_id = ANY(%s::uuid[]) AND p.phone = ANY(%s)''',
        (company_id, [str(item) for item in entity_ids], tracked)).fetchall()
    return snapshot_stock(conn, company_id, 'status', [row['phone'] for row in touched])


class TrackedPayload(BaseModel):
    phones: str | list[str] = Field(default='')
    labels: dict[str, str] = Field(default_factory=dict)


def _overview(conn, company_id):
    tracked_rows = conn.execute(
        'SELECT phone, label, created_at FROM xm.tracked_sales WHERE company_id = %s ORDER BY created_at, phone', (company_id,)).fetchall()
    phones = [row['phone'] for row in tracked_rows]
    counts = phone_counts(conn, company_id, phones)
    week_ago = datetime.now(WIB).replace(tzinfo=None) - timedelta(days=7)
    extras = {row['phone']: row for row in conn.execute(
        '''SELECT p.phone, count(DISTINCT e.entity_id) FILTER (WHERE r.sent_at >= %s AND e.status <> 'deleted') AS new_7d,
                  max(r.sent_at) AS last_posted_at, mode() WITHIN GROUP (ORDER BY d.contact_name) AS contact_name
           FROM unnest(%s::text[]) AS p(phone)
           JOIN xm.documents d ON d.company_id = %s AND d.document_type = 'property_listing' AND d.active
                AND (d.contact_phones @> ARRAY[p.phone] OR d.contact_phone = p.phone)
           JOIN xm.entities e ON e.entity_id = d.entity_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
           GROUP BY p.phone''', (week_ago, phones, company_id)).fetchall()} if phones else {}
    spark = {}
    if phones:
        for row in conn.execute(
            '''SELECT phone, logged_at, ready, total FROM (
                 SELECT phone, logged_at, ready, total, row_number() OVER (PARTITION BY phone ORDER BY logged_at DESC, id DESC) rn
                 FROM xm.stock_log WHERE company_id = %s AND phone = ANY(%s)) s WHERE rn <= 30 ORDER BY phone, logged_at''',
                (company_id, phones)).fetchall():
            spark.setdefault(row['phone'], []).append({'at': row['logged_at'], 'ready': row['ready'], 'total': row['total']})
    items = []
    for row in tracked_rows:
        c = counts[row['phone']]
        extra = extras.get(row['phone']) or {}
        items.append({
            'phone': row['phone'], 'label': row['label'], 'contact_name': extra.get('contact_name'),
            'counts': {**c, 'total': c['ready'] + c['on_hold'] + c['sold']},
            'new_7d': extra.get('new_7d') or 0, 'last_posted_at': extra.get('last_posted_at'),
            'history': spark.get(row['phone'], []), 'tracked_since': row['created_at'],
        })
    totals = {key: sum(item['counts'][key] for item in items) for key in (*STATUS_KEYS, 'total')}
    last = conn.execute('SELECT max(logged_at) AS at FROM xm.stock_log WHERE company_id = %s', (company_id,)).fetchone()
    return {'tracked': items, 'totals': totals, 'last_logged_at': last['at'] if last else None, 'max_tracked': MAX_TRACKED}


@router.get('/overview')
def overview():
    with connect() as conn:
        return _overview(conn, workspace_id())


@router.put('/tracked')
def save_tracked(payload: TrackedPayload, request: Request):
    """Replace the tracked numbers. Company-level setting; only a company admin may change it."""
    phones = parse_phones(payload.phones)
    user = current_user(request)
    labels = {normalize_phone(key): value.strip()[:80] for key, value in payload.labels.items() if value.strip()}
    company_id = workspace_id()
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
        existing = set(tracked_phones(conn, company_id))
        conn.execute('DELETE FROM xm.tracked_sales WHERE company_id = %s AND NOT (phone = ANY(%s))', (company_id, phones))
        for phone in phones:
            conn.execute(
                '''INSERT INTO xm.tracked_sales(company_id, phone, label, created_by, created_at) VALUES (%s,%s,%s,%s,clock_timestamp())
                   ON CONFLICT (company_id, phone) DO UPDATE SET label = coalesce(excluded.label, xm.tracked_sales.label)''',
                (company_id, phone, labels.get(phone), user['id'] if user else None))
        added = [phone for phone in phones if phone not in existing]
        if added:
            snapshot_stock(conn, company_id, 'tracking', added, note='Started tracking')
        if existing != set(phones):
            _watchlist_changed(conn, company_id)
        conn.commit()
        result = _overview(conn, company_id)
    invalidate_stats(company_id)
    return result


@router.post('/tracked/parse')
def parse_tracked_file(file: UploadFile = File(...)):
    """Read a CSV or Excel file for "Import from CSV / Excel". Nothing is saved: the browser picks the
    sheet and the columns and saves the numbers with PUT /stock/tracked. Company admins only (access.py)."""
    data = file.file.read(MAX_BYTES + 1)
    try:
        return read_sheets(file.filename or '', data)
    except SheetImportError as exc:
        raise HTTPException(400, str(exc)) from exc


def _watchlist_changed(conn, company_id):
    """In Sales mode the watchlist decides which pairs exist: results are out of date until reprocessed."""
    current_mode = conn.execute('SELECT matching_mode FROM xm.app_preferences WHERE company_id=%s', (company_id,)).fetchone()
    if current_mode and current_mode['matching_mode'] == 'sales':
        from matching_scope import mark_reprocess_pending
        mark_reprocess_pending(conn, 'Sales watchlist changed')


def one_phone(value):
    phones = parse_phones(value)
    if len(phones) != 1:
        raise HTTPException(400, 'Enter exactly one phone number.')
    return phones[0]


def clean_label(value):
    return (value or '').strip()[:80] or None


class TrackedItem(BaseModel):
    phone: str = Field(min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=200)


class TrackedEdit(TrackedItem):
    original_phone: str = Field(min_length=1, max_length=40)


def _lock_watchlist(conn):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")


@router.post('/tracked/item')
def add_tracked(payload: TrackedItem, request: Request):
    """Add one sales number (and its name) to the watchlist."""
    phone = one_phone(payload.phone)
    user = current_user(request)
    company_id = workspace_id()
    with connect() as conn:
        _lock_watchlist(conn)
        existing = tracked_phones(conn, company_id)
        if phone in existing:
            raise HTTPException(409, 'This number is already on the watchlist.')
        if len(existing) >= MAX_TRACKED:
            raise HTTPException(400, f'At most {MAX_TRACKED} sales numbers per company.')
        conn.execute(
            '''INSERT INTO xm.tracked_sales(company_id, phone, label, created_by, created_at) VALUES (%s,%s,%s,%s,clock_timestamp())''',
            (company_id, phone, clean_label(payload.label), user['id'] if user else None))
        snapshot_stock(conn, company_id, 'tracking', [phone], note='Started tracking')
        _watchlist_changed(conn, company_id)
        conn.commit()
        result = _overview(conn, company_id)
    invalidate_stats(company_id)
    return result


@router.patch('/tracked/item')
def edit_tracked(payload: TrackedEdit):
    """Rename a sales number or correct the number itself; its history and "tracked since" stay."""
    original = normalize_phone(payload.original_phone)
    phone = one_phone(payload.phone)
    company_id = workspace_id()
    with connect() as conn:
        _lock_watchlist(conn)
        existing = tracked_phones(conn, company_id)
        if original not in existing:
            raise HTTPException(404, 'This number is not on the watchlist.')
        if phone != original and phone in existing:
            raise HTTPException(409, 'This number is already on the watchlist.')
        conn.execute('UPDATE xm.tracked_sales SET phone=%s, label=%s WHERE company_id=%s AND phone=%s',
                     (phone, clean_label(payload.label), company_id, original))
        if phone != original:
            conn.execute('UPDATE xm.stock_log SET phone=%s WHERE company_id=%s AND phone=%s', (phone, company_id, original))
            snapshot_stock(conn, company_id, 'tracking', [phone], note='Phone number changed')
            _watchlist_changed(conn, company_id)
        conn.commit()
        result = _overview(conn, company_id)
    invalidate_stats(company_id)
    return result


@router.delete('/tracked/item')
def remove_tracked(phone: str):
    """Stop following one sales number. Its earlier log rows stay in the history."""
    wanted = normalize_phone(phone)
    company_id = workspace_id()
    with connect() as conn:
        _lock_watchlist(conn)
        if not conn.execute('DELETE FROM xm.tracked_sales WHERE company_id=%s AND phone=%s', (company_id, wanted)).rowcount:
            raise HTTPException(404, 'This number is not on the watchlist.')
        _watchlist_changed(conn, company_id)
        conn.commit()
        result = _overview(conn, company_id)
    invalidate_stats(company_id)
    return result


@router.post('/snapshot')
def manual_snapshot():
    company_id = workspace_id()
    with connect() as conn:
        written = snapshot_stock(conn, company_id, 'manual', note='Logged manually')
        conn.commit()
        return {'logged': written, **_overview(conn, company_id)}


@router.get('/log')
def stock_log(phone: str = '', date_from: str = '', date_to: str = '', limit: int = 50, offset: int = 0):
    limit = min(max(limit, 1), 200)
    clauses, params = ["l.company_id = current_setting('xm.workspace_id')"], []
    if phone.strip():
        clauses.append('l.phone = %s')
        params.append(normalize_phone(phone))
    try:
        if date_from:
            clauses.append('l.logged_at >= %s')
            params.append(datetime.combine(date.fromisoformat(date_from), time.min, WIB))
        if date_to:
            clauses.append('l.logged_at < %s')
            params.append(datetime.combine(date.fromisoformat(date_to) + timedelta(days=1), time.min, WIB))
    except ValueError:
        raise HTTPException(400, 'Invalid date.')
    with connect() as conn:
        rows = conn.execute(
            '''SELECT l.id, l.phone, l.logged_at, l.event_type, l.import_id, l.agent_name, l.total, l.ready, l.on_hold, l.sold,
                      l.deleted, l.delta_total, l.delta_ready, l.note, t.label
               FROM xm.stock_log l LEFT JOIN xm.tracked_sales t ON t.company_id = l.company_id AND t.phone = l.phone
               WHERE ''' + ' AND '.join(clauses) + ' ORDER BY l.logged_at DESC, l.id DESC LIMIT %s OFFSET %s',
            params + [limit + 1, max(0, offset)]).fetchall()
    return {'rows': rows[:limit], 'has_more': len(rows) > limit}


def display_phone(phone):
    """``6287852258118`` -> ``+62 878-5225-8118``, the way the app shows numbers."""
    national = phone[2:] if phone.startswith('62') else phone.lstrip('0')
    return '+62 ' + '-'.join(part for part in (national[:3], national[3:7], national[7:]) if part)


def sender_title(author):
    """A WhatsApp author as a display name: ``~ Budi, +62 812-3456-7890`` -> ``Budi``.

    Unsaved contacts are exported as "~ name, phone"; the raw author stays the group key.
    A bare number (no name) is kept as it is.
    """
    text = ' '.join(str(author or '').split())
    text = re.sub(r'^~\s*', '', text)
    name = re.sub(r'\s*,\s*\+?\d[\d\s().-]{5,}$', '', text).strip(' ,')
    return name or text


def clean_contact_name(name):
    """Drop a leading "Contact:" / "Hubungi" style prefix captured with the signature name."""
    return re.sub(r'^\s*(?:info\s+lanjut|informasi|contact(?:\s+person)?|kontak|hubungi|marketing|call|wa)\s*[:：\-–]?\s*', '',
                  name or '', flags=re.I).lstrip('~ ').strip()


def export_rows(conn, company_id, phones):
    """Every listing of the given sales numbers, as the "Lihat listing sales ini" window shows them:
    ready, on-hold and sold, any match status, one row per content (v4.10 content key), newest first.
    A listing that is one part of a multi-listing bubble is exported as that part's text."""
    return conn.execute(
        f'''SELECT phone, raw_text, sent_at FROM (
             SELECT DISTINCT ON (p.ord, {GROUP_KEY}) p.ord, p.phone,
                    coalesce(d.segment_text, r.raw_text) AS raw_text, r.sent_at, d.id
             FROM unnest(%s::text[]) WITH ORDINALITY AS p(phone, ord)
             JOIN xm.documents d ON d.company_id = %s AND d.document_type = 'property_listing' AND d.active
                  AND (d.contact_phones @> ARRAY[p.phone] OR d.contact_phone = p.phone)
             JOIN xm.entities e ON e.entity_id = d.entity_id AND e.status <> 'deleted'
             JOIN xm.raw_messages r ON r.id = d.raw_message_id
             ORDER BY p.ord, {GROUP_KEY}, r.sent_at DESC NULLS LAST, d.id) listing
           ORDER BY ord, sent_at DESC NULLS LAST, id''', (phones, company_id)).fetchall()


def build_workbook(rows, names):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font

    book = Workbook()
    sheet = book.active
    sheet.title = 'Listings per sales'
    sheet.append(['Name', 'Phone', 'Date', 'Original message'])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        # Excel rejects control characters and caps a cell at 32,767 characters.
        text = ILLEGAL_CHARACTERS_RE.sub('', row['raw_text'] or '')[:32767]
        sheet.append([names[row['phone']], display_phone(row['phone']), row['sent_at'], text])
        line = sheet.max_row
        sheet.cell(line, 3).number_format = 'dd/mm/yyyy hh:mm'
        message = sheet.cell(line, 4)
        message.data_type = 's'  # a message starting with "=" is text, never a formula
        message.alignment = Alignment(wrap_text=True, vertical='top')
        for column in (1, 2, 3):
            sheet.cell(line, column).alignment = Alignment(vertical='top')
    for column, width in zip('ABCD', (24, 20, 18, 90)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@router.get('/export')
def export_listings(phone: str = ''):
    """Excel of the listings of every tracked sales, or of one of them when ``phone`` is given."""
    company_id = workspace_id()
    with connect() as conn:
        tracked = _overview(conn, company_id)['tracked']
        if phone.strip():
            wanted = normalize_phone(phone)
            tracked = [item for item in tracked if item['phone'] == wanted]
            if not tracked:
                raise HTTPException(404, 'This sales number is not on the watchlist.')
        names = {item['phone']: item['label'] or clean_contact_name(item['contact_name']) or 'Sales' for item in tracked}
        rows = export_rows(conn, company_id, list(names))
    return Response(build_workbook(rows, names), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="sales-stock-listings.xlsx"'})
