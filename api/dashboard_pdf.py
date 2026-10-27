"""Dashboard -> Export PDF: the Home page of one period as an Indonesian A4 report.

The figures come from ``dashboard.compute_overview``; the two listing sections (active listings per
sales, posting-date analysis) are read here. Charts are drawn straight on the canvas so every bar
and point carries its value. Page numbers ("Hal. N dari M") come from a second pass, the first one
only counts pages.
"""
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from dashboard import compute_overview, resolve_period
from db import connect
from matching_scope import document_scope_filter
from report import latin, _logo_path

router = APIRouter(prefix='/dashboard')
WIB = ZoneInfo('Asia/Jakarta')

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 32
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
CONTENT_TOP = PAGE_HEIGHT - 104
CONTENT_BOTTOM = 52
FOOTER_Y = 30

# Brand blues carry the report; the other colours only appear where meaning needs them.
PRIMARY, NAVY, SKY, TINT = '#2a52d6', '#0b2a6f', '#7dd3fc', '#dbe4ff'
HOT, WARM, CUSTOM, GREEN, GREY = '#ee6666', '#fac858', '#8b5cf6', '#3ba272', '#94a3b8'
INK, MUTED, RULE, ZEBRA = '#25324b', '#53617b', '#dbe3ef', '#f4f7fc'
BUCKETS = ((0, 7, '<= 7 hari'), (8, 30, '8-30 hari'), (31, None, '> 30 hari'))

MONTHS_ID = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des']
PERIOD_LABELS_ID = {'week': 'Minggu ini', 'last_week': 'Minggu lalu', 'month': 'Bulan ini', 'last_month': 'Bulan lalu',
                    'last30': '30 hari terakhir', 'custom': 'Periode khusus'}
CATEGORY_ID = {'house': 'Rumah', 'warehouse': 'Gudang', 'apartment': 'Apartemen', 'shophouse': 'Ruko', 'land': 'Tanah',
               'factory': 'Pabrik', 'office': 'Kantor', 'villa': 'Villa', 'commercial_building': 'Gedung komersial',
               'hotel': 'Hotel', 'unknown': 'Tidak terdeteksi'}
TRANSACTION_ID = {'sale': 'Jual', 'rent': 'Sewa', 'both': 'Jual/Sewa'}
BASIS_SUFFIX = {'per_m2': '/m²', 'per_year': '/thn', 'per_month': '/bln'}
MAX_LISTING_ROWS = 400


# ---------------------------------------------------------------- formatting
def pretty_day(day: date) -> str:
    return f'{day.day} {MONTHS_ID[day.month - 1]} {day.year}'


def pretty_range(start: date, end: date) -> str:
    """13-19 Okt 2026 / 28 Sep - 4 Okt 2026 / 20 Des 2025 - 5 Jan 2026."""
    if start == end:
        return pretty_day(start)
    if (start.year, start.month) == (end.year, end.month):
        return f'{start.day}–{end.day} {MONTHS_ID[end.month - 1]} {end.year}'
    if start.year == end.year:
        return f'{start.day} {MONTHS_ID[start.month - 1]} – {pretty_day(end)}'
    return f'{pretty_day(start)} – {pretty_day(end)}'


def number(value) -> str:
    return f'{int(value):,}'.replace(',', '.')


def percent(part, whole) -> str:
    return f'{round(part / whole * 100) if whole else 0} %'


def money(value) -> str:
    from matching_rules import _money
    return _money(value)


def area_text(low, high):
    if not low and not high:
        return None
    low = float(low if low is not None else high)
    high = float(high or 0)

    def fmt(v):
        return f'{v:g}'.replace('.', ',')
    span = '–' + fmt(high) if high and high != low else ''
    return fmt(low) + span + ' m²'


def price_text(low, high, basis):
    if not low and not high:
        return None
    text = money(low) if not high or high == low else (money(high) if not low else f'{money(low)}–{money(high)}')
    return text + BASIS_SUFFIX.get(basis or '', '')


def listing_summary(row) -> str:
    """"Rumah · Surabaya Barat · LT 100 m² · LB 120 m² · Jual Rp 1,8 M", or the start of the message."""
    categories = [CATEGORY_ID.get(c, c) for c in (row.get('categories') or []) if c and c != 'unknown']
    if not categories and row.get('primary_category') and row['primary_category'] != 'unknown':
        categories = [CATEGORY_ID.get(row['primary_category'], row['primary_category'])]
    parts = [', '.join(categories),
             ' / '.join(str(loc).title() for loc in (row.get('locations') or [])[:3]),
             area_text(row.get('land_area_min'), row.get('land_area_max')) and 'LT ' + area_text(row.get('land_area_min'), row.get('land_area_max')),
             area_text(row.get('building_area_min'), row.get('building_area_max')) and 'LB ' + area_text(row.get('building_area_min'), row.get('building_area_max'))]
    sale = price_text(row.get('price_min'), row.get('price_max'), row.get('price_basis'))
    rent = price_text(row.get('rent_price_min'), row.get('rent_price_max'), row.get('rent_price_basis') or 'per_year')
    kind = row.get('transaction_type')
    if kind == 'both' and rent:
        parts.append(' · '.join(p for p in (sale and 'Jual ' + sale, 'Sewa ' + rent) if p))
    elif sale:
        parts.append((TRANSACTION_ID.get(kind, '') + ' ' + sale).strip())
    text = ' · '.join(p for p in parts if p)
    if text:
        return text
    raw = ' '.join(str(row.get('raw_text') or row.get('normalized_text') or '').split())
    return raw[:80] + ('…' if len(raw) > 80 else '')


def phone_text(value):
    digits = ''.join(c for c in str(value or '') if c.isdigit())
    national = digits[2:] if digits.startswith('62') else digits.lstrip('0')
    return '+62 ' + '-'.join(p for p in (national[:3], national[3:7], national[7:]) if p) if national else ''


# ---------------------------------------------------------------- pure helpers (unit tested)
def newest_posting(dates):
    """The posting date of a listing: the newest of all its postings (1st, 3rd and 5th -> the 5th)."""
    days = [d.date() if isinstance(d, datetime) else d for d in dates if d]
    return max(days) if days else None


def stock_age(dates, today):
    """How fresh the ready stock is: listings last posted <= 7, 8-30 and > 30 days ago, with their share."""
    counts = Counter()
    for day in dates:
        day = newest_posting([day])
        if day is None:
            continue
        age = (today - day).days
        for low, high, label in BUCKETS:
            if age >= low and (high is None or age <= high):
                counts[label] += 1
                break
    total = sum(counts.values())
    return [{'label': label, 'count': counts.get(label, 0), 'pct': round(counts.get(label, 0) / total * 100) if total else 0}
            for _, _, label in BUCKETS]


def posting_histogram(dates, start, end):
    """Ready listings by newest posting date inside [start, end]: per day up to 14 days, else per week (Monday).
    Returns (bucket, labels, values, outside) where ``outside`` counts listings posted before/after the range."""
    days = [newest_posting([d]) for d in dates]
    inside = [d for d in days if d and start <= d <= end]
    outside = sum(1 for d in days if d) - len(inside)
    span = (end - start).days + 1
    if span <= 14:
        labels = [start + timedelta(days=i) for i in range(span)]
        counts = Counter(inside)
        return 'day', labels, [counts.get(d, 0) for d in labels], outside
    labels, cursor = [], start - timedelta(days=start.weekday())
    while cursor <= end:
        labels.append(cursor)
        cursor += timedelta(days=7)
    counts = Counter(d - timedelta(days=d.weekday()) for d in inside)
    return 'week', labels, [counts.get(d, 0) for d in labels], outside


def age_insights(buckets, total):
    if not total:
        return ['Belum ada listing siap yang bisa dianalisis.']
    fresh, old = buckets[0], buckets[2]
    lines = [f"{fresh['pct']} % stok siap ({number(fresh['count'])} listing) diposting ulang dalam 7 hari terakhir."]
    if old['count']:
        lines.append(f"{number(old['count'])} listing ({old['pct']} %) sudah lebih dari 30 hari tidak diposting; "
                     'periksa apakah masih tersedia atau minta sales memposting ulang.')
    else:
        lines.append('Tidak ada listing siap yang lebih dari 30 hari tidak diposting.')
    return lines


def nice_step(maximum, ticks=4):
    """A round gridline step (1, 2, 5, 10, ...) so that about ``ticks`` lines cover ``maximum``."""
    # Every chart here counts things, so a step is never smaller than one (a maximum of 1 reads 0, 1 and not 0, 0, 1).
    if maximum <= ticks:
        return 1
    rough = maximum / ticks
    magnitude = 10 ** math.floor(math.log10(rough))
    for factor in (1, 2, 5, 10):
        if rough <= factor * magnitude:
            return max(1, factor * magnitude)
    return 10 * magnitude


def make_insights_id(data):
    """The Home page highlights, written in Indonesian from the same figures."""
    kpi, out = data['kpi'], []
    buyers, previous = kpi['buyers'], kpi['buyers_prev']
    if buyers == 0:
        out.append('Tidak ada permintaan buyer pada periode ini.')
    else:
        trend = ''
        if previous:
            delta = kpi['buyers_change'] or 0
            if delta >= 500:
                trend = f', {buyers / previous:.1f}x periode sebelumnya'.replace('.', ',')
            elif delta == 0:
                trend = ', sama dengan periode sebelumnya'
            else:
                trend = f', {"naik" if delta > 0 else "turun"} {abs(delta):g} % dari periode sebelumnya'.replace('.', ',')
        out.append(f'{number(buyers)} permintaan buyer unik pada periode ini{trend}.')
    if data['categories'] and buyers:
        top = data['categories'][0]
        out.append(f"Paling dicari: {CATEGORY_ID.get(top['key'], top['label'])} ({percent(top['value'], buyers)} dari permintaan).")
    gaps = [g for g in data['gap_locations'] if g['buyers'] >= 2 and g['pressure'] >= 1.5]
    if gaps:
        g = gaps[0]
        stock = 'belum ada listing siap' if not g['listings'] else f"hanya {number(g['listings'])} listing siap"
        out.append(f"Peluang: {g['label']} dicari {number(g['buyers'])} buyer, {stock}.")
    matches = data['matches']
    if matches['total']:
        parts = [f"{matches['hot']} Hot", f"{matches['warm']} Warm"] + ([f"{matches['custom']} Custom"] if matches.get('custom') else [])
        out.append(f"{number(matches['total'])} match baru ditemukan dari upload periode ini ({', '.join(parts)}).")
    if data['status']['listing'].get('sold'):
        out.append(f"{number(data['status']['listing']['sold'])} listing ditandai Terjual.")
    return out[:5]


# ---------------------------------------------------------------- data
def ready_listings_by_sales(conn, group_by):
    """Every ready listing with its sales key (same grouping as the Home chart) and newest posting date."""
    from workspace_cache import GROUP_KEYS
    key_sql = GROUP_KEYS['sender' if group_by == 'sender' else 'phone']
    scope_sql, scope_params = document_scope_filter(conn)
    rows = conn.execute(
        f'''SELECT {key_sql} AS k, g.group_id, coalesce(g.public_id, e.public_id) AS public_id, d.contact_name, d.contact_phone,
                   d.primary_category, d.categories, d.locations, d.land_area_min, d.land_area_max, d.building_area_min,
                   d.building_area_max, d.price_min, d.price_max, d.price_basis, d.rent_price_min, d.rent_price_max,
                   d.rent_price_basis, d.transaction_type, d.normalized_text, r.raw_text, r.sent_at,
                   (SELECT max(r2.sent_at) FROM xm.document_group_members gm
                      JOIN xm.documents d2 ON d2.id = gm.document_id JOIN xm.raw_messages r2 ON r2.id = d2.raw_message_id
                     WHERE gm.group_id = g.group_id AND d2.active) AS newest
            FROM xm.document_groups g JOIN xm.documents d ON d.id = g.group_id JOIN xm.raw_messages r ON r.id = d.raw_message_id
            LEFT JOIN xm.entities e ON e.entity_id = d.entity_id
            WHERE g.company_id = current_setting('xm.workspace_id') AND g.document_type = 'property_listing' AND g.status = 'ready'
              AND d.active''' + scope_sql, scope_params).fetchall()
    groups = defaultdict(list)
    for row in rows:
        row = dict(row)
        row['newest'] = newest_posting([row['newest'], row['sent_at']])
        groups[row['k']].append(row)
    from dashboard import sales_label
    sales = []
    for key, items in groups.items():
        items.sort(key=lambda r: (r['newest'] or date.min, r['public_id'] or ''), reverse=True)
        phones = Counter(r['contact_phone'] for r in items if r['contact_phone'])
        phone = key if group_by == 'phone' else (phones.most_common(1)[0][0] if phones else '')
        names = Counter(r['contact_name'] for r in items if r['contact_name'])
        label = sales_label(group_by, key)
        if group_by == 'phone' and names:
            label = f"{names.most_common(1)[0][0]} ({phone_text(phone)})" if phone else names.most_common(1)[0][0]
        elif phone:
            label = f'{label} ({phone_text(phone)})'
        sales.append({'key': key, 'label': label, 'listings': items})
    sales.sort(key=lambda s: (-len(s['listings']), s['label']))
    return sales


# ---------------------------------------------------------------- drawing primitives
def text_width(text, font='Helvetica', size=9):
    return stringWidth(text, font, size)


def clip(text, font, size, width):
    text = latin(text)
    if text_width(text, font, size) <= width:
        return text
    while text and text_width(text + '…', font, size) > width:
        text = text[:-1]
    return text + '…'


def wrap(text, font, size, width, max_lines=2):
    words, lines, line = latin(text).split(), [], ''
    for word in words:
        trial = (line + ' ' + word).strip()
        if text_width(trial, font, size) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and line:
        lines.append(line)
    if len(lines) == max_lines and (' '.join(lines) != ' '.join(words)):
        lines[-1] = clip(lines[-1] + ' ' + ' '.join(words[len(' '.join(lines).split()):]), font, size, width)
    return lines or ['']


class Doc:
    """A flowing cursor over A4 pages with the report header on each one."""

    def __init__(self, output, header, total=None):
        self.pdf = canvas.Canvas(output, pagesize=A4)
        self.pdf.setTitle('Laporan Dashboard')
        self.header, self.total, self.page = header, total, 0
        self.y = CONTENT_TOP
        self.new_page()

    def new_page(self):
        pdf = self.pdf
        if self.page:
            pdf.showPage()
        self.page += 1
        right = PAGE_WIDTH - MARGIN
        pdf.drawImage(self.header['logo'], MARGIN, PAGE_HEIGHT - 72, width=150, height=61, mask='auto', preserveAspectRatio=True)
        pdf.setFillColor(HexColor(NAVY))
        pdf.setFont('Helvetica-Bold', 12)
        pdf.drawRightString(right, PAGE_HEIGHT - 34, clip(self.header['company'], 'Helvetica-Bold', 12, 300))
        pdf.setFont('Helvetica-Bold', 10)
        pdf.setFillColor(HexColor(PRIMARY))
        pdf.drawRightString(right, PAGE_HEIGHT - 48, 'Laporan Dashboard')
        pdf.setFont('Helvetica', 9)
        pdf.setFillColor(HexColor(MUTED))
        pdf.drawRightString(right, PAGE_HEIGHT - 61, self.header['period'])
        pdf.drawRightString(right, PAGE_HEIGHT - 73, 'Dibuat ' + self.header['generated'])
        pdf.setStrokeColor(HexColor(RULE))
        pdf.setLineWidth(0.8)
        pdf.line(MARGIN, PAGE_HEIGHT - 85, right, PAGE_HEIGHT - 85)
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(MARGIN, FOOTER_Y, 'Auto Audit · Laporan Dashboard')
        pdf.drawRightString(right, FOOTER_Y, f'Hal. {self.page} dari {self.total}' if self.total else f'Hal. {self.page}')
        self.y = CONTENT_TOP

    def need(self, height):
        if self.y - height < CONTENT_BOTTOM:
            self.new_page()

    def section(self, title, caption=None):
        self.need(44 + (14 if caption else 0))
        self.y -= 22
        self.pdf.setFillColor(HexColor(NAVY))
        self.pdf.setFont('Helvetica-Bold', 16)
        self.pdf.drawString(MARGIN, self.y, latin(title))
        self.pdf.setStrokeColor(HexColor(PRIMARY))
        self.pdf.setLineWidth(2)
        self.pdf.line(MARGIN, self.y - 7, MARGIN + 36, self.y - 7)
        self.y -= 16
        if caption:
            self.caption(caption)

    def caption(self, text, size=9.5):
        self.pdf.setFillColor(HexColor(MUTED))
        self.pdf.setFont('Helvetica', size)
        for line in wrap(text, 'Helvetica', size, CONTENT_WIDTH, 3):
            self.pdf.drawString(MARGIN, self.y, line)
            self.y -= size * 1.35

    def chart_title(self, title, caption, height):
        """Title + caption of a chart, keeping them with the chart body of ``height``."""
        self.need(height + 36)
        self.y -= 8
        self.pdf.setFillColor(HexColor(INK))
        self.pdf.setFont('Helvetica-Bold', 12)
        self.pdf.drawString(MARGIN, self.y, latin(title))
        self.y -= 13
        self.caption(caption, 9)
        self.y -= 4

    def empty(self, text):
        self.need(30)
        self.pdf.setFillColor(HexColor(ZEBRA))
        self.pdf.roundRect(MARGIN, self.y - 24, CONTENT_WIDTH, 24, 6, fill=1, stroke=0)
        self.pdf.setFillColor(HexColor(MUTED))
        self.pdf.setFont('Helvetica-Oblique', 9.5)
        self.pdf.drawString(MARGIN + 10, self.y - 15.5, latin(text))
        self.y -= 32

    def finish(self):
        self.pdf.save()
        return self.page


def legend(pdf, x, y, items, size=8.5):
    for label, color in items:
        pdf.setFillColor(HexColor(color))
        pdf.roundRect(x, y - 1, 8, 8, 2, fill=1, stroke=0)
        pdf.setFillColor(HexColor(INK))
        pdf.setFont('Helvetica', size)
        pdf.drawString(x + 11, y, label)
        x += 11 + text_width(label, 'Helvetica', size) + 14


def gridlines(pdf, left, bottom, width, height, maximum, unit=''):
    """Thin horizontal gridlines with their values at the left; returns the value -> y scale."""
    step = nice_step(maximum)
    top_value = max(step, math.ceil(maximum / step) * step)
    scale = height / top_value
    pdf.setStrokeColor(HexColor(RULE))
    pdf.setLineWidth(0.5)
    pdf.setFillColor(HexColor(MUTED))
    pdf.setFont('Helvetica', 7.5)
    value = 0
    while value <= top_value:
        y = bottom + value * scale
        pdf.line(left, y, left + width, y)
        pdf.drawRightString(left - 4, y - 2.5, number(value))
        value += step
    if unit:
        pdf.saveState()
        pdf.translate(left - 24, bottom + height / 2)
        pdf.rotate(90)
        pdf.drawCentredString(0, 0, unit)
        pdf.restoreState()
    return scale


def x_labels(pdf, left, bottom, slot, labels):
    pdf.setFillColor(HexColor(MUTED))
    pdf.setFont('Helvetica', 7.5)
    step = max(1, math.ceil(len(labels) / 15))
    last = len(labels) - 1
    for index, label in enumerate(labels):
        # Every step-th label, plus the last one unless it would sit on top of the previous printed label.
        if index % step == 0 or (index == last and (last % step) * slot >= text_width(label, 'Helvetica', 7.5) + 6):
            pdf.drawCentredString(left + slot * (index + 0.5), bottom - 10, label)


def short_label(iso, bucket):
    day = date.fromisoformat(iso)
    text = f'{day.day} {MONTHS_ID[day.month - 1]}'
    return 'Mg ' + text if bucket == 'week' else text


def stacked_bars(doc, labels, series, unit):
    """Vertical bars stacked from ``series`` [(name, values, colour)], per-segment values and totals on top."""
    pdf, height = doc.pdf, 170
    left, width = MARGIN + 34, CONTENT_WIDTH - 40
    bottom = doc.y - height
    plot_h = height - 30
    totals = [sum(values[i] for _, values, _ in series) for i in range(len(labels))]
    scale = gridlines(pdf, left, bottom + 16, width, plot_h, max(totals, default=0), unit)
    slot = width / max(len(labels), 1)
    bar_w = min(slot * 0.64, 36)
    for index, total in enumerate(totals):
        x = left + slot * (index + 0.5) - bar_w / 2
        y = bottom + 16
        for _, values, color in series:
            value = values[index]
            if not value:
                continue
            h = value * scale
            pdf.setFillColor(HexColor(color))
            pdf.rect(x, y, bar_w, h, fill=1, stroke=0)
            if len(series) > 1 and h >= 9 and bar_w >= 14:  # one series: the total on top already says it
                pdf.setFillColor(HexColor('#ffffff' if color != WARM else INK))
                pdf.setFont('Helvetica-Bold', 7)
                pdf.drawCentredString(x + bar_w / 2, y + h / 2 - 2.5, number(value))
            y += h
        if total:
            pdf.setFillColor(HexColor(NAVY))
            pdf.setFont('Helvetica-Bold', 7.5)
            pdf.drawCentredString(x + bar_w / 2, y + 3, number(total))
    x_labels(pdf, left, bottom + 16, slot, labels)
    legend(pdf, left, bottom - 2, [(name, color) for name, _, color in series])
    doc.y = bottom - 14


def line_chart(doc, labels, values, color, unit):
    pdf, height = doc.pdf, 150
    left, width = MARGIN + 34, CONTENT_WIDTH - 40
    bottom = doc.y - height
    plot_h = height - 26
    scale = gridlines(pdf, left, bottom + 16, width, plot_h, max(values, default=0), unit)
    slot = width / max(len(values), 1)
    points = [(left + slot * (i + 0.5), bottom + 16 + v * scale) for i, v in enumerate(values)]
    if len(points) > 1:
        pdf.setStrokeColor(HexColor(color))
        pdf.setLineWidth(1.6)
        path = pdf.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        pdf.drawPath(path, fill=0, stroke=1)
    pdf.setFont('Helvetica-Bold', 7)
    for index, (x, y) in enumerate(points):
        pdf.setFillColor(HexColor(color))
        pdf.circle(x, y, 2.4, fill=1, stroke=0)
        pdf.setFillColor(HexColor(NAVY))
        pdf.drawCentredString(x, y + 4.5, number(values[index]))
    x_labels(pdf, left, bottom + 16, slot, labels)
    doc.y = bottom - 14


def horizontal_bars(doc, rows, series, unit, label_width=140, row_height=None, legend_items=None):
    """``rows`` are labels; ``series`` [(name, values, colour)] draws one bar per series per row with the value
    at the end of each bar. Rows are drawn in the given order (callers sort them)."""
    pdf = doc.pdf
    per_row = row_height or (12 * len(series) + 8)
    height = per_row * len(rows) + 20 + (12 if legend_items or len(series) > 1 else 0)
    doc.need(height)
    left, width = MARGIN + label_width, CONTENT_WIDTH - label_width - 36
    top = doc.y
    maximum = max((v for _, values, _ in series for v in values), default=0)
    step = nice_step(maximum)
    top_value = max(step, math.ceil(maximum / step) * step)
    scale = width / top_value
    pdf.setStrokeColor(HexColor(RULE))
    pdf.setLineWidth(0.5)
    pdf.setFillColor(HexColor(MUTED))
    pdf.setFont('Helvetica', 7.5)
    bottom = top - per_row * len(rows)
    value = 0
    while value <= top_value:
        x = left + value * scale
        pdf.line(x, bottom, x, top)
        pdf.drawCentredString(x, bottom - 9, number(value))
        value += step
    pdf.drawRightString(left + width, bottom - 18, unit)
    bar_h = 10
    for index, label in enumerate(rows):
        row_top = top - per_row * index
        pdf.setFillColor(HexColor(INK))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawRightString(left - 8, row_top - per_row / 2 - 3, clip(label, 'Helvetica', 8.5, label_width - 12))
        y = row_top - 4
        for _, values, color in series:
            y -= bar_h + 2
            w = values[index] * scale
            pdf.setFillColor(HexColor(color))
            pdf.roundRect(left, y, max(w, 0.1), bar_h, 2, fill=1, stroke=0)
            pdf.setFillColor(HexColor(NAVY))
            pdf.setFont('Helvetica-Bold', 7.5)
            pdf.drawString(left + w + 3, y + 2.5, number(values[index]))
    if legend_items or len(series) > 1:
        legend(pdf, MARGIN, bottom - 34, legend_items or [(name, color) for name, _, color in series])
        bottom -= 16
    doc.y = bottom - 28


def donut(pdf, cx, cy, radius, items, caption):
    """A ring with the share of each item; values are printed on the big slices and in the legend beside it."""
    total = sum(value for _, value, _ in items)
    pdf.setLineWidth(0)
    if not total:
        pdf.setFillColor(HexColor(TINT))
        pdf.circle(cx, cy, radius, fill=1, stroke=0)
    angle = 90
    for label, value, color in items:
        if not value:
            continue
        extent = -360 * value / total
        pdf.setFillColor(HexColor(color))
        pdf.setStrokeColor(HexColor('#ffffff'))
        pdf.setLineWidth(1)
        pdf.wedge(cx - radius, cy - radius, cx + radius, cy + radius, angle, extent, fill=1, stroke=1)
        if value / total >= 0.08:
            mid = math.radians(angle + extent / 2)
            r = radius * 0.74
            pdf.setFillColor(HexColor('#ffffff' if color != WARM else INK))
            pdf.setFont('Helvetica-Bold', 8)
            pdf.drawCentredString(cx + r * math.cos(mid), cy + r * math.sin(mid) - 3, number(value))
        angle += extent
    pdf.setFillColor(HexColor('#ffffff'))
    pdf.circle(cx, cy, radius * 0.52, fill=1, stroke=0)
    pdf.setFillColor(HexColor(NAVY))
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawCentredString(cx, cy - 1, number(total))
    pdf.setFillColor(HexColor(MUTED))
    pdf.setFont('Helvetica', 7)
    pdf.drawCentredString(cx, cy - 11, caption)
    y = cy + radius - 10
    for label, value, color in items:
        pdf.setFillColor(HexColor(color))
        pdf.roundRect(cx + radius + 12, y - 1, 8, 8, 2, fill=1, stroke=0)
        pdf.setFillColor(HexColor(INK))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(cx + radius + 24, y, f'{label}: {number(value)} ({percent(value, total)})')
        y -= 14


def table(doc, columns, rows, font_size=8.5, zebra=True):
    """``columns`` are (title, width, align); cells may be a string or a list of lines. The header repeats on a
    new page."""
    pdf = doc.pdf
    line_h = font_size * 1.3

    def header():
        doc.need(line_h + 6)
        pdf.setFillColor(HexColor(TINT))
        pdf.rect(MARGIN, doc.y - line_h - 4, CONTENT_WIDTH, line_h + 4, fill=1, stroke=0)
        pdf.setFillColor(HexColor(NAVY))
        pdf.setFont('Helvetica-Bold', font_size)
        x = MARGIN
        for title, width, align in columns:
            (pdf.drawRightString if align == 'right' else pdf.drawString)(x + width - 6 if align == 'right' else x + 6, doc.y - line_h + 1, title)
            x += width
        doc.y -= line_h + 4

    header()
    for index, row in enumerate(rows):
        cells = [cell if isinstance(cell, list) else [cell] for cell in row]
        lines = max(len(cell) for cell in cells)
        row_h = line_h * lines + 4
        if doc.y - row_h < CONTENT_BOTTOM:
            doc.new_page()
            header()
        if zebra and index % 2:
            pdf.setFillColor(HexColor(ZEBRA))
            pdf.rect(MARGIN, doc.y - row_h, CONTENT_WIDTH, row_h, fill=1, stroke=0)
        x = MARGIN
        for (title, width, align), cell in zip(columns, cells):
            for line_no, line in enumerate(cell):
                bold = line.startswith('**')
                text = clip(line[2:] if bold else line, 'Helvetica-Bold' if bold else 'Helvetica', font_size, width - 12)
                pdf.setFont('Helvetica-Bold' if bold else 'Helvetica', font_size)
                pdf.setFillColor(HexColor(INK))
                y = doc.y - line_h * (line_no + 1) + 1
                if align == 'right':
                    pdf.drawRightString(x + width - 6, y, text)
                else:
                    pdf.drawString(x + 6, y, text)
            x += width
        doc.y -= row_h
    pdf.setStrokeColor(HexColor(RULE))
    pdf.setLineWidth(0.5)
    pdf.line(MARGIN, doc.y, MARGIN + CONTENT_WIDTH, doc.y)
    doc.y -= 10


def kpi_cards(doc, cards):
    """Four rounded cards: value, label, split line and the change against the previous period."""
    pdf = doc.pdf
    gap, height = 10, 74
    width = (CONTENT_WIDTH - gap * (len(cards) - 1)) / len(cards)
    doc.need(height + 8)
    top = doc.y
    for index, card in enumerate(cards):
        x = MARGIN + (width + gap) * index
        pdf.setFillColor(HexColor(ZEBRA))
        pdf.setStrokeColor(HexColor(RULE))
        pdf.setLineWidth(0.6)
        pdf.roundRect(x, top - height, width, height, 8, fill=1, stroke=1)
        pdf.setFillColor(HexColor(card.get('color', PRIMARY)))
        pdf.roundRect(x, top - height, 4, height, 2, fill=1, stroke=0)
        pdf.setFillColor(HexColor(MUTED))
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(x + 12, top - 15, card['label'])
        pdf.setFillColor(HexColor(NAVY))
        pdf.setFont('Helvetica-Bold', 20)
        pdf.drawString(x + 12, top - 38, number(card['value']))
        pdf.setFont('Helvetica', 7.5)
        pdf.setFillColor(HexColor(INK))
        y = top - 51
        if card.get('detail'):
            pdf.drawString(x + 12, y, clip(card['detail'], 'Helvetica', 7.5, width - 20))
            y -= 11
        change = card.get('change', 'none')
        if change == 'none':
            pdf.setFillColor(HexColor(MUTED))
            pdf.drawString(x + 12, y, 'vs periode sebelumnya: -')
        elif change is None:
            pdf.setFillColor(HexColor(MUTED))
            pdf.drawString(x + 12, y, 'Belum ada pembanding')
        else:
            color = GREEN if change > 0 else (HOT if change < 0 else MUTED)
            pdf.setFillColor(HexColor(color))
            if change:
                path = pdf.beginPath()
                if change > 0:
                    path.moveTo(x + 12, y); path.lineTo(x + 18, y); path.lineTo(x + 15, y + 5.5)
                else:
                    path.moveTo(x + 12, y + 5.5); path.lineTo(x + 18, y + 5.5); path.lineTo(x + 15, y)
                path.close()
                pdf.drawPath(path, fill=1, stroke=0)
            pdf.drawString(x + 21, y, f'{abs(change):g} % vs periode sebelumnya'.replace('.', ','))
    doc.y = top - height - 10


# ---------------------------------------------------------------- the report
def build_dashboard_pdf(data, sales, company_name, generated=None, today=None, total=None):
    start, end = date.fromisoformat(data['period']['date_from']), date.fromisoformat(data['period']['date_to'])
    today = today or datetime.now(WIB).date()
    now = datetime.now(WIB)
    generated = generated or f'{pretty_day(now.date())} {now:%H:%M} WIB'
    header = {'logo': ImageReader(str(_logo_path())), 'company': latin(company_name or 'Auto Audit'),
              'period': f"{PERIOD_LABELS_ID.get(data['period']['key'], 'Periode')} · {pretty_range(start, end)}",
              'generated': generated}
    output = BytesIO()
    doc = Doc(output, header, total)
    pdf = doc.pdf
    bucket = data['period']['bucket']
    per = 'minggu' if bucket == 'week' else 'hari'
    labels = [short_label(iso, bucket) for iso in data['trend']['labels']]
    kpi, matches = data['kpi'], data['matches']

    # 1. Ringkasan
    doc.section('Ringkasan', f"Periode {pretty_range(start, end)}, dibandingkan dengan "
                f"{pretty_range(date.fromisoformat(data['period']['compare_from']), date.fromisoformat(data['period']['compare_to']))}. "
                'Pesan yang sama dihitung satu kali.')
    doc.y -= 6
    kpi_cards(doc, [
        {'label': 'Permintaan buyer', 'value': kpi['buyers'], 'change': kpi['buyers_change'], 'color': PRIMARY},
        {'label': 'Listing baru', 'value': kpi['listings'], 'change': kpi['listings_change'], 'color': SKY},
        {'label': 'Match baru', 'value': kpi['matches'], 'change': kpi['matches_change'], 'color': HOT,
         'detail': f"{matches['hot']} Hot · {matches['warm']} Warm · {matches.get('custom', 0)} Custom"},
        {'label': 'Listing siap', 'value': data['status']['listing']['ready'], 'change': 'none', 'color': GREEN,
         'detail': f"{number(data['status']['listing']['on_hold'])} ditahan · {number(data['status']['listing']['sold'])} terjual"},
    ])

    # 2. Sorotan
    doc.section('Sorotan', 'Dihitung dari data periode ini.')
    doc.y -= 4
    for line in make_insights_id(data):
        lines = wrap(line, 'Helvetica', 10, CONTENT_WIDTH - 18, 3)
        doc.need(14 * len(lines) + 4)
        pdf.setFillColor(HexColor(PRIMARY))
        pdf.circle(MARGIN + 4, doc.y - 3.5, 2, fill=1, stroke=0)
        pdf.setFillColor(HexColor(INK))
        pdf.setFont('Helvetica', 10)
        for text in lines:
            pdf.drawString(MARGIN + 14, doc.y - 7, text)
            doc.y -= 14
        doc.y -= 3

    # 3. Charts
    doc.section('Grafik', 'Angka pada setiap batang dan titik adalah nilai pastinya. Tanggal dalam WIB; minggu dimulai hari Senin.')
    doc.chart_title(f'Match baru per {per}', f'Pasangan buyer-listing yang ditemukan dari upload, per {per}. Angka di atas batang adalah totalnya.', 180)
    if kpi['matches']:
        hot, custom = data['trend']['matches_hot'], data['trend'].get('matches_custom') or [0] * len(labels)
        warm = [max(0, t - h - c) for t, h, c in zip(data['trend']['matches'], hot, custom)]
        stacked_bars(doc, labels, [('Hot', hot, HOT), ('Warm', warm, WARM), ('Custom', custom, CUSTOM)], 'match')
    else:
        doc.empty('Tidak ada match baru pada periode ini.')
    doc.chart_title(f'Permintaan buyer per {per}', f'Permintaan buyer unik per {per}.', 160)
    line_chart(doc, labels, data['trend']['buyers'], PRIMARY, 'buyer')
    doc.chart_title(f'Listing baru per {per}', f'Listing unik yang diposting per {per}.', 160)
    line_chart(doc, labels, data['trend']['listings'], NAVY, 'listing')

    categories = [c for c in data['categories'] if c['value']]
    doc.chart_title('Jenis properti paling dicari', 'Jenis properti pada permintaan buyer periode ini, beserta jumlah dan persentasenya.',
                    20 * max(len(categories), 1) + 40)
    if categories:
        total = sum(c['value'] for c in categories)
        horizontal_bars(doc, [f"{CATEGORY_ID.get(c['key'], c['label'])} ({percent(c['value'], total)})" for c in categories],
                        [('Buyer', [c['value'] for c in categories], PRIMARY)], 'jumlah buyer', label_width=170)
    else:
        doc.empty('Belum ada permintaan buyer pada periode ini.')

    locations = data['locations']
    doc.chart_title('Lokasi paling dicari vs listing siap', 'Jumlah buyer yang mencari lokasi tersebut dibandingkan listing siap di sana.',
                    32 * max(len(locations), 1) + 52)
    if locations:
        horizontal_bars(doc, [loc['label'] for loc in locations],
                        [('Buyer', [loc['value'] for loc in locations], PRIMARY), ('Listing siap', [loc['stock'] for loc in locations], GREEN)], 'jumlah')
    else:
        doc.empty('Belum ada lokasi yang terbaca pada periode ini.')

    budget = data['budget']
    skipped = f" {number(budget['skipped'])} buyer tidak menyebut budget yang bisa dibandingkan." if budget.get('skipped') else ''
    doc.chart_title('Budget buyer (beli)', 'Jumlah buyer per rentang harga beli. Harga per m² tidak dihitung.' + skipped,
                    20 * len(budget['labels']) + 40)
    horizontal_bars(doc, ['Rp ' + label for label in budget['labels']], [('Jual', budget['sale'], PRIMARY)], 'jumlah buyer', label_width=110)
    rent_labels = budget.get('rent_labels') or []
    if rent_labels:
        doc.chart_title('Budget buyer (sewa)', 'Jumlah buyer per rentang sewa per tahun.', 20 * len(rent_labels) + 40)
        horizontal_bars(doc, ['Rp ' + label for label in rent_labels], [('Sewa', budget['rent'][:len(rent_labels)], SKY)], 'jumlah buyer', label_width=120)

    doc.chart_title('Peluang', 'Dicari banyak buyer tetapi listing siapnya sedikit. Tekanan = buyer dibagi listing siap.', 60)
    gaps = [('Jenis', CATEGORY_ID.get(g['key'], g['label']), g) for g in data['gap_categories']] + \
           [('Lokasi', g['label'], g) for g in data['gap_locations']]
    if gaps:
        table(doc, [('Kelompok', 70, 'left'), ('Jenis / lokasi', 221, 'left'), ('Buyer', 70, 'right'), ('Listing siap', 90, 'right'), ('Tekanan', 80, 'right')],
              [[kind, name, number(g['buyers']), number(g['listings']), f"{g['pressure']:.1f}x".replace('.', ',')] for kind, name, g in gaps])
    else:
        doc.empty('Belum cukup permintaan untuk melihat peluang.')

    doc.chart_title('Status stok', 'Berapa listing dan buyer yang siap, ditahan, atau terjual/selesai.', 130)
    status = data['status']
    top = doc.y
    donut(pdf, MARGIN + 50, top - 60, 44, [('Siap', status['listing']['ready'], GREEN), ('Ditahan', status['listing']['on_hold'], WARM),
                                           ('Terjual', status['listing']['sold'], GREY)], 'listing')
    donut(pdf, MARGIN + CONTENT_WIDTH / 2 + 50, top - 60, 44, [('Siap', status['buyer']['ready'], PRIMARY), ('Ditahan', status['buyer']['on_hold'], WARM),
                                                               ('Selesai', status['buyer']['sold'], GREY)], 'buyer')
    pdf.setFillColor(HexColor(INK))
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(MARGIN, top - 2, 'Listing')
    pdf.drawString(MARGIN + CONTENT_WIDTH / 2, top - 2, 'Buyer')
    doc.y = top - 120

    top_sales = data['top_sales']['rows']
    doc.chart_title('Listing siap per sales', 'Listing siap, dikelompokkan menurut '
                    + ('nomor telepon pada pesan.' if data['top_sales']['group_by'] == 'phone' else 'pengirim pesan.'),
                    20 * max(len(top_sales), 1) + 40)
    if top_sales:
        names = {s['key']: s['label'] for s in sales}
        horizontal_bars(doc, [names.get(row['key'], row['label']) for row in top_sales],
                        [('Listing siap', [row['value'] for row in top_sales], NAVY)], 'jumlah listing', label_width=180)
    else:
        doc.empty('Belum ada listing siap.')

    # 4. Tabel listing aktif per sales
    total_listings = sum(len(s['listings']) for s in sales)
    doc.section('Listing aktif per sales', f'{number(total_listings)} listing siap dari {number(len(sales))} sales. Tanggal posting adalah tanggal '
                'posting terbaru dari setiap listing. Urut dari sales dengan listing terbanyak, listing terbaru di atas.')
    printed = 0
    for entry in sales:
        if printed >= MAX_LISTING_ROWS:
            break
        rows = entry['listings']
        doc.need(60)
        doc.y -= 12
        pdf.setFillColor(HexColor(NAVY))
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawString(MARGIN, doc.y, clip(f"Sales: {entry['label']} · {number(len(rows))} listing siap", 'Helvetica-Bold', 11, CONTENT_WIDTH))
        doc.y -= 8
        take = rows[:MAX_LISTING_ROWS - printed]
        table(doc, [('ID', 68, 'left'), ('Ringkasan', 353, 'left'), ('Tanggal posting terbaru', 110, 'right')],
              [[row['public_id'] or '-', wrap(listing_summary(row), 'Helvetica', 8.5, 341, 2), pretty_day(row['newest']) if row['newest'] else '-'] for row in take])
        printed += len(take)
    if total_listings > printed:
        doc.caption(f'… dan {number(total_listings - printed)} listing lainnya.', 10)
    if not sales:
        doc.empty('Belum ada listing siap.')

    # 5. Analisis tanggal posting
    dates = [row['newest'] for entry in sales for row in entry['listings']]
    doc.section('Analisis tanggal posting listing', 'Listing siap menurut tanggal posting terbarunya. Listing yang lama tidak diposting ulang '
                'perlu dicek ketersediaannya.')
    kind, days, values, outside = posting_histogram(dates, start, end)
    unit = 'minggu' if kind == 'week' else 'hari'
    doc.chart_title(f'Listing siap per tanggal posting terbaru ({unit})',
                    f'Listing siap yang posting terbarunya jatuh pada periode ini, per {unit}.'
                    + (f' {number(outside)} listing siap lainnya terakhir diposting di luar periode ini.' if outside else ''), 180)
    stacked_bars(doc, [('Mg ' if kind == 'week' else '') + f'{d.day} {MONTHS_ID[d.month - 1]}' for d in days],
                 [('Listing siap', values, PRIMARY)], 'listing')
    buckets = stock_age(dates, today)
    doc.chart_title('Umur stok', f'Jarak hari antara posting terbaru dan hari ini ({pretty_day(today)}).', 90)
    table(doc, [('Terakhir diposting', 231, 'left'), ('Listing siap', 150, 'right'), ('Persentase', 150, 'right')],
          [[b['label'], number(b['count']), f"{b['pct']} %"] for b in buckets], zebra=True)
    for line in age_insights(buckets, len([d for d in dates if d])):
        lines = wrap(line, 'Helvetica', 10, CONTENT_WIDTH - 18, 3)
        doc.need(14 * len(lines) + 4)
        pdf.setFillColor(HexColor(PRIMARY))
        pdf.circle(MARGIN + 4, doc.y - 3.5, 2, fill=1, stroke=0)
        pdf.setFillColor(HexColor(INK))
        pdf.setFont('Helvetica', 10)
        for text in lines:
            pdf.drawString(MARGIN + 14, doc.y - 7, text)
            doc.y -= 14
    pages = doc.finish()
    return output.getvalue(), pages


def render(data, sales, company_name, today=None):
    """Two passes: the first counts the pages, the second prints "Hal. N dari M"."""
    now = datetime.now(WIB)
    generated = f'{pretty_day(now.date())} {now:%H:%M} WIB'
    _, pages = build_dashboard_pdf(data, sales, company_name, generated, today)
    content, _ = build_dashboard_pdf(data, sales, company_name, generated, today, total=pages)
    return content


def safe_name(text):
    return ''.join(c if c.isalnum() or c in '-_' else '-' for c in latin(text or 'Company')).strip('-')[:40] or 'Company'


@router.get('/export.pdf')
def export_pdf(period: Literal['week', 'last_week', 'month', 'last_month', 'last30', 'custom'] = 'last_week',
               date_from: str = '', date_to: str = ''):
    info = resolve_period(period, date_from, date_to)
    data = compute_overview(period, date_from, date_to)
    with connect() as conn:
        from company import company_row
        company = company_row(conn)
        sales = ready_listings_by_sales(conn, company['listing_group_by'] or 'sender')
    content = render(data, sales, company['company_name'])
    filename = f"Laporan-Dashboard-{safe_name(company['company_name'])}-{info['start'].isoformat()}_{info['end'].isoformat()}.pdf"
    return Response(content, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{filename}"'})
