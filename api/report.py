import re
from io import BytesIO
from urllib.parse import quote
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from parser import clean_text, split_contact


# The PDF is shared with other agents and stays Indonesian (product UI is English).
# One full-width bar at the top of every pair page says how strong the pair is. Custom (v4.3): a requirement
# the buyer insists on (location, tower, legal status, unit type) is not stated in the listing.
STATUS_BARS = {
    'hot': ('#fee2e2', '#b91c1c', 'Hot \u2014 kecocokan kuat'),
    'warm': ('#ffedd5', '#b45309', 'Warm \u2014 perlu dicek'),
    'custom': ('#ede9fe', '#6d28d9', 'Custom \u2014 syarat wajib buyer perlu dicek manual'),
    'unmatched': ('#e2e8f0', '#334155', 'Belum cocok'),
}
# The export of both directions opens each person's sections with a full-colour cover: dark blue for
# Listing → Buyer (their listings with the buyers that fit), sky blue for Buyer → Listing.
SECTION_STYLES = {
    'property': {'words': ('Listing', 'Buyer'), 'background': '#0b2a6f', 'text': '#ffffff', 'muted': '#c9d8f7',
                 'about': 'Listing dengan buyer yang cocok', 'source': 'listing', 'target': 'buyer'},
    'buyer': {'words': ('Buyer', 'Listing'), 'background': '#7dd3fc', 'text': '#0b1f44', 'muted': '#1e3a66',
              'about': 'Buyer dengan listing yang cocok', 'source': 'buyer', 'target': 'listing'},
}
# The numbered badge ("No. 3") of a pair page: the section colour, or the brand blue in a one-direction export.
BADGE_COLORS = {'property': ('#0b2a6f', '#ffffff'), 'buyer': ('#7dd3fc', '#0b1f44'), None: ('#1645a0', '#ffffff')}
HIGHLIGHT = '#fff59d'  # the words in both messages that back a reason (see matching_rules.evidence_spans)
FOOTER_COLOR = '#53617b'
COUNT_CHIPS = [('hot', 'Hot'), ('warm', 'Warm'), ('custom', 'Custom'), ('unmatched', 'belum cocok')]


def pair_status(target):
    """Stored status of the pair on this page; pages without a counterpart are "Belum cocok"."""
    if not target:
        return 'unmatched'
    if target.get('status') in ('hot', 'warm', 'custom'):
        return target['status']
    return 'hot' if float(target.get('score') or 0) >= 80 else 'warm'  # rows from before stored statuses


def _status_icon(pdf, status):
    # Vector silhouettes stay sharp and portable without emoji fonts. Drawn in a 24 x 24 box.
    if status == 'hot':
        pdf.setFillColor(HexColor('#ef4444'))
        path = pdf.beginPath()
        path.moveTo(12, 23); path.curveTo(15, 14, 24, 13, 21, 5)
        path.curveTo(18, -3, 3, -1, 3, 8)
        path.curveTo(3, 13, 8, 17, 8, 19)
        path.curveTo(8, 13, 12, 12, 12, 23)
        pdf.drawPath(path, fill=1, stroke=0)
        pdf.setFillColor(HexColor('#fbbf24'))
        path = pdf.beginPath(); path.moveTo(13, 13)
        path.curveTo(13, 8, 18, 6, 15, 2); path.curveTo(8, -1, 6, 5, 13, 13)
        pdf.drawPath(path, fill=1, stroke=0)
    elif status == 'warm':
        pdf.setStrokeColor(HexColor('#64748b')); pdf.setLineWidth(1.5)
        pdf.setFillColor(HexColor('#ffffff')); pdf.roundRect(9, 6, 7, 17, 3.5, fill=1, stroke=1)
        pdf.circle(12.5, 5, 5, fill=1, stroke=1)
        pdf.setStrokeColor(HexColor('#f97316')); pdf.setLineWidth(3); pdf.line(12.5, 5, 12.5, 18)
        pdf.setFillColor(HexColor('#f97316')); pdf.circle(12.5, 5, 3, fill=1, stroke=0)
    elif status == 'custom':
        pdf.setFillColor(HexColor('#7c3aed'))
        path = pdf.beginPath()
        path.moveTo(12, 1); path.curveTo(6, 9, 4, 12, 4, 15)
        path.curveTo(4, 25, 20, 25, 20, 15); path.curveTo(20, 12, 18, 9, 12, 1)
        pdf.drawPath(path, fill=1, stroke=0)
        pdf.setFillColor(HexColor('#ffffff')); pdf.circle(12, 15, 3.2, fill=1, stroke=0)


def ownership_label(target, direction, company_name=None):
    """Indonesian label of the pair's ownership tier (``ownership_tier`` on the recommendation row); '' when unknown."""
    tier = (target or {}).get('ownership_tier')
    if tier == 'own':
        return 'Buyer sendiri' if direction == 'property' else 'Stok sendiri'
    if tier == 'team':
        return 'Tim'
    if tier == 'company':
        return latin(str(company_name or '').strip())[:40] or 'Company'
    if tier == 'other':
        return 'Agen lain'
    return ''


def status_bar(pdf, status, number=None, section=None, tier_label=''):
    """Full-width coloured bar under the logo row, above both columns; ``number`` adds the "No. N" badge on its right,
    ``tier_label`` the pair's ownership tier just left of it."""
    background, color, text = STATUS_BARS[status]
    x, y, width = MARGIN, STATUS_BAR_Y, PAGE_WIDTH - 2 * MARGIN
    pdf.saveState()
    pdf.setFillColor(HexColor(background))
    pdf.roundRect(x, y, width, STATUS_BAR_HEIGHT, 7, fill=1, stroke=0)
    text_x = x + 14
    if status != 'unmatched':
        pdf.saveState()
        pdf.translate(x + 10, y + 3)
        pdf.scale(0.9, 0.9)
        _status_icon(pdf, status)
        pdf.restoreState()
        text_x = x + 40
    pdf.setFillColor(HexColor(color))
    pdf.setFont('Helvetica-Bold', 12)
    pdf.drawString(text_x, y + 9.5, text)
    right = x + width - 4
    if number is not None:
        fill, ink = BADGE_COLORS.get(section, BADGE_COLORS[None])
        label = f'No. {number}'
        badge = stringWidth(label, 'Helvetica-Bold', 11) + 20
        pdf.setFillColor(HexColor(fill))
        pdf.roundRect(right - badge, y + 4, badge, STATUS_BAR_HEIGHT - 8, 6, fill=1, stroke=0)
        pdf.setFillColor(HexColor(ink))
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawCentredString(right - badge / 2, y + 10, label)
        right -= badge + 6
    if tier_label:
        chip = stringWidth(tier_label, 'Helvetica-Bold', 10) + 18
        pdf.setFillColor(HexColor('#ffffff'))
        pdf.roundRect(right - chip, y + 4, chip, STATUS_BAR_HEIGHT - 8, 6, fill=1, stroke=0)
        pdf.setFillColor(HexColor('#1645a0'))
        pdf.setFont('Helvetica-Bold', 10)
        pdf.drawCentredString(right - chip / 2, y + 10.5, tier_label)
    pdf.restoreState()


def part_label(row):
    """Indonesian label of one part of a multi-context bubble ("Bagian 1 dari 2"), else None."""
    count = int(row.get('segment_count') or 1)
    if count < 2 or not row.get('segment_text'):
        return None
    return f"Bagian {int(row.get('segment_index') or 0) + 1} dari {count}"


def message_text(row, signature=True):
    """What the page prints for one side: the part itself (plus the sender's signature) when the
    bubble holds several requests/listings, else the whole original bubble."""
    raw = row.get('raw_text') or row.get('normalized_text') or ''
    if not part_label(row):
        return raw
    contact = split_contact(clean_text(raw))[1] if signature and row.get('raw_text') else ''
    return row['segment_text'].strip() + ('\n\n' + contact if contact else '')


def contact_url(row, kind):
    phone = ''.join(c for c in row['contact_phone'] if c.isdigit())
    if phone.startswith('0'): phone = '62' + phone[1:]
    elif phone.startswith('8'): phone = '62' + phone
    message = (f"Halo {row.get('contact_name') or 'Bapak/Ibu'}, saya ingin menindaklanjuti "
               f"{'kebutuhan properti' if kind == 'buyer' else 'listing properti'} yang Anda bagikan:\n\n"
               f"{message_text(row, signature=False)[:500]}\n\n"
               "Apakah masih tersedia? Saya memiliki calon pasangan yang sesuai. "
               "Boleh saya meminta informasi lebih lanjut? Terima kasih.")
    return 'https://wa.me/' + phone + '?text=' + quote(message, safe='')


BODY_FONT_SIZE = 11
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 32
COLUMN_GAP = 18
COLUMN_WIDTH = (PAGE_WIDTH - 2 * MARGIN - COLUMN_GAP) / 2
TEXT_WIDTH = COLUMN_WIDTH - 24
# Page, top to bottom: logo row and rule (-85), status bar, reasons callout (when the pair has reasons), column
# labels, text, WhatsApp button, footer. Without a callout the labels and text keep these positions.
STATUS_BAR_HEIGHT = 28
STATUS_BAR_Y = PAGE_HEIGHT - 123
LABEL_Y = PAGE_HEIGHT - 165
BODY_TOP = PAGE_HEIGHT - 180
BUTTON_Y = 46
BODY_BOTTOM = 85
BODY_HEIGHT = BODY_TOP - BODY_BOTTOM
FOOTER_Y = 30
# Reasons callout under the status bar: two columns of bullets; its text shrinks (never below CALLOUT_MIN_SIZE)
# before the callout grows past CALLOUT_MAX_HEIGHT.
CALLOUT_GAP = 8
CALLOUT_PADDING = 9
CALLOUT_TITLE = 'Alasan direkomendasikan'
CALLOUT_FONT_SIZE = 9.5
CALLOUT_MIN_SIZE = 4  # only reached by an unusually long list of reasons
CALLOUT_MAX_HEIGHT = 150
CALLOUT_ICON = 15
CALLOUT_ITEM_GAP = 3


# ---------------------------------------------------------------- text helpers
# The built-in PDF fonts know the cp1252 characters only; a few symbols the reasons use get a plain spelling.
_PLAIN = str.maketrans({'\u2192': '->', '\u2248': '~', '\u2265': '>=', '\u2264': '<=', '\u2713': '', '\u2714': ''})


def _cp1252(text):
    return (text or '').translate(_PLAIN).encode('cp1252', 'ignore').decode('cp1252')


def latin(text):
    """The built-in PDF fonts only know Latin-1 style characters; drop the rest (emoji and the like)."""
    return _cp1252(text).strip()


def _markup(text):
    return escape(_cp1252(text)).replace('\n', '<br/>')


def highlighted_markup(text, spans=()):
    """Paragraph markup of a message with the ``spans`` (start, end) on a light-yellow background."""
    pieces, last = [], 0
    for start, end in sorted(spans or ()):
        start, end = max(start, last), min(end, len(text))
        if end <= start:
            continue
        pieces.append(_markup(text[last:start]))
        marked = [f'<span backColor="{HIGHLIGHT}">{escape(_cp1252(line))}</span>' if _cp1252(line).strip() else escape(_cp1252(line))
                  for line in text[start:end].split('\n')]
        pieces.append('<br/>'.join(marked))
        last = end
    pieces.append(_markup(text[last:]))
    return ''.join(pieces)


def fit_paragraph(text, width=TEXT_WIDTH, height=BODY_HEIGHT, label=None, highlights=()):
    """Keep the full text on one page, using 11 pt unless it needs to shrink. ``label`` is a bold first line;
    ``highlights`` are (start, end) spans of ``text`` marked as evidence."""
    markup = highlighted_markup(text, highlights)
    if label:
        markup = f'<b>{escape(label)}</b><br/><br/>' + markup

    def measure(size):
        style = ParagraphStyle('body', fontName='Helvetica', fontSize=size,
                               leading=size * 1.35, textColor=HexColor('#25324b'), wordWrap='CJK')
        paragraph = Paragraph(markup, style)
        _, used_height = paragraph.wrap(width, height)
        return paragraph, used_height

    paragraph, used_height = measure(BODY_FONT_SIZE)
    if used_height <= height:
        return paragraph, used_height
    # Fit each column independently so a long listing does not shrink its buyer.
    # No continuation pages, clipping, or text truncation.
    lower, upper = 0, BODY_FONT_SIZE
    for _ in range(24):
        size = (lower + upper) / 2
        candidate, candidate_height = measure(size)
        if candidate_height <= height:
            lower = size
            paragraph, used_height = candidate, candidate_height
        else:
            upper = size
    return paragraph, used_height


# ---------------------------------------------------------------- reasons (same rules as lib/reasons.ts)
# Explanations come from matching_rules.assess_pair in English: purpose and unit lines, positive reasons, then
# "Needs check" items, then internal summary lines. The callout shows the first groups, in Indonesian.
_INTERNAL = [re.compile(pattern, re.I) for pattern in (
    r'^(?:location|lokasi)\s*:?\s*\d+(?:[.,]\d+)?\s*%',
    r'^(?:asset\s+categor(?:y|ies)|kategori\s+aset)\b',
    r'^(?:semantic|semantik|text\s+similarity|kemiripan\s+teks|data\s+quality|kualitas\s+data|score|skor)\b',
    r'\bnot\s+requested\b|\btidak\s+diminta\b',
    r'\b(?:extraction|ekstraksi|confidence|vector|qdrant|embedding|worker)\b',
    r'^\s*[\w\s]+:\s*\d+(?:[.,]\d+)?\s*%\s*$')]
_NEEDS_CHECK = [re.compile(pattern, re.I) for pattern in (
    r'^needs?\s+check\b',
    r'\b(?:not\s+(?:yet\s+)?(?:confirmed|verified|stated|available|known|clear|read|met)|unconfirmed|unverified|unknown|'
    r'needs?\s+(?:to\s+be\s+)?(?:checked|check|confirm(?:ed|ation)?|verif(?:ied|ication|y))|please\s+check|held\s+back|'
    r'on\s+hold\s+until|tolerance|negotiat)',
    r'\b(?:belum|perlu|ditahan|toleransi|negosiasi)\b')]
_CHECK_MARK = re.compile('\\s*[\u2713\u2714\u2611\u2705]\ufe0f?\\s*$')
_NEEDS_PREFIX = re.compile(r'^needs?\s+check\s*[:：\-–—]\s*', re.I)
_NOISE = [(re.compile(pattern, re.I), replacement) for pattern, replacement in (
    (r'\s*(?:menurut|according\s+to\s+the|per\s+the)\s+(?:indeks(?:\s+lokasi)?|location\s+index|index)\b', ''),
    (r'\s*(?:menurut|according\s+to|per)\s+(?:data\s+impor|imported\s+data)\b', ''),
    (r'\s*\((?:batas|limit)\s+4\s*km\)|\s*\(4\s*km\s+limit\)', ''),
    (r'\(within\s+(?:the\s+)?tolerance\)', '(within the Warm range)'),
    (r'\s{2,}', ' '))]
_REWRITES = [(re.compile(pattern, re.I), replacement) for pattern, replacement in (
    (r'^purpose\s*:\s*(.+?)\s*\(not\s+confirmed\)$', r'Purpose not confirmed (\1)'),
    (r'^purpose\s*:\s*not\s+stated$', 'Purpose not stated'),
    (r'^floor\s+level$', 'Floor level fits'))]


def reason_kind(text):
    line = str(text or '').strip()
    if not line or any(pattern.search(line) for pattern in _INTERNAL):
        return 'internal'
    # A proven requirement ("Tower Anderson ✓", a place name that happens to contain "perlu") is positive.
    if _CHECK_MARK.search(line) and not _NEEDS_PREFIX.search(line):
        return 'positive'
    return 'check' if any(pattern.search(line) for pattern in _NEEDS_CHECK) else 'positive'


def _tidy(text, kind):
    line = _CHECK_MARK.sub('', str(text).strip())
    for pattern, replacement in _NOISE:
        line = pattern.sub(replacement, line)
    if kind == 'check':
        line = _NEEDS_PREFIX.sub('', line)
    line = line.strip()
    for pattern, replacement in _REWRITES:
        line = pattern.sub(replacement, line)
    return line[:1].upper() + line[1:]


def split_reasons(reasons):
    """Customer-facing reasons, positives first, then items to verify (English, as the app shows them)."""
    positive, checks, seen = [], [], set()
    for raw in reasons if isinstance(reasons, (list, tuple)) else []:
        kind = reason_kind(raw)
        if kind == 'internal':
            continue
        text = _tidy(raw, kind)
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        (checks if kind == 'check' else positive).append(text)
    return positive, checks


# ---------------------------------------------------------------- reasons in Indonesian
_CATEGORIES = {'house': 'rumah', 'land': 'tanah', 'shophouse': 'ruko', 'warehouse': 'gudang', 'factory': 'pabrik', 'villa': 'villa',
               'apartment': 'apartemen', 'office': 'kantor', 'hotel': 'hotel', 'commercial building': 'gedung komersial'}
_MEASURES = {'land area': 'luas tanah', 'building area': 'luas bangunan', 'width': 'lebar', 'road width (row)': 'lebar jalan (ROW)',
             'bedrooms': 'jumlah kamar tidur', 'floors': 'jumlah lantai', 'land': 'tanah', 'building': 'bangunan'}
_PURPOSES = {'buy': 'beli', 'rent': 'sewa', 'buy or rent': 'beli atau sewa'}
# Buyer requirements matching_rules quotes ("REQUIREMENT_LABELS", "_EXTRA_NEEDS", request flags).
_LABELS = {
    'move-in ready': 'siap huni', 'ready to use': 'siap pakai', 'container access': 'akses kontainer', 'main road location': 'lokasi jalan raya',
    'corner lot': 'posisi hook/pojok', 'shm certificate': 'sertifikat SHM', 'hgb certificate': 'sertifikat HGB', 'non-lsd land': 'tanah non-LSD',
    'near a toll road': 'dekat tol', 'ample parking': 'parkiran luas', 'fully furnished': 'full furnish',
    'modern minimalist style': 'gaya minimalis modern', 'water extraction permit (sipa)': 'izin pengambilan air (SIPA)',
    'loading dock': 'loading dock', 'balcony': 'balkon', 'boarding house (kost)': 'rumah kost', 'warehouse estate (pergudangan)': 'kawasan pergudangan', 'lift': 'lift', 'adjacent units side by side (jejer)': 'unit berjejer (jejer)',
    "all of the buyer's exclusions": 'semua pengecualian dari buyer',
    'below-market price needs a comparison; "negotiable" is not proof': 'harga di bawah pasar perlu dibandingkan; "nego" bukan bukti',
    'parking availability and terms': 'ketersediaan dan syarat parkir', 'neighbourhood/zoning conditions': 'kondisi lingkungan/zonasi',
    'requested building condition': 'kondisi bangunan yang diminta', 'suitability for the intended use': 'kesesuaian dengan peruntukan',
    'special access or use': 'akses atau penggunaan khusus', "buyer's extra facilities or restrictions": 'fasilitas tambahan atau larangan dari buyer',
}
_QUOTED = {'water source': 'sumber air', 'former use': 'bekas penggunaan', 'equipment': 'peralatan'}


def _alts(value):
    return re.sub(r'\s+or\s+', ' atau ', value.replace('Strata title (SHMRS)', 'Strata (SHMRS)'))


def _categories(value):
    return ', '.join(_CATEGORIES.get(item.strip().lower(), item.strip()) for item in value.split(','))


def _measure(value):
    return _MEASURES.get(value.strip().lower(), value.strip())


def _period(value):
    """"2 years" → "2 tahun", "6 months" → "6 bulan"."""
    return re.sub(r'\b(\d+) months?\b', r'\1 bulan', re.sub(r'\b(\d+) years?\b', r'\1 tahun', value))


def _floor(value):
    value = re.sub(r'\bor lower\b', 'atau lebih rendah', re.sub(r'\bor higher\b', 'atau lebih tinggi', value))
    return re.sub(r'\bfloor\b', 'lantai', value)


def _amount(value):
    """A price as matching_rules shows it ("price Rp 1,8 M", "Rp 50 rb/m² per year × 120 m² ≈ …")."""
    value = re.sub(r'^rent price\b', 'harga sewa', re.sub(r'^price\b', 'harga', value.strip(), flags=re.I), flags=re.I)
    value = re.sub(r'\bper year\b', 'per tahun', re.sub(r'\bper month\b', 'per bulan', value))
    return re.sub(r'× requested ([\d.,]+ m²)', r'× \1 yang diminta', value)


def _label(value):
    value = value.strip()
    quoted = re.fullmatch(r'(water source|former use|equipment) \((.+)\)', value, re.I)
    if quoted:
        return f'{_QUOTED[quoted.group(1).lower()]} ({quoted.group(2)})'
    place = re.fullmatch(r'location (".+")', value, re.I)
    if place:
        return 'lokasi ' + place.group(1)
    return _LABELS.get(value.lower(), value)


def _sentence(text):
    return text[:1].upper() + text[1:]


# (full-line pattern, Indonesian line or function of the match), tried in order on a tidied English reason.
_DETAIL_WORDS = {'budget': 'budget', 'size': 'luas', 'area': 'lokasi', 'unit type': 'jenis properti'}


def _DETAIL_ID(text):
    return ', '.join(_DETAIL_WORDS.get(w.strip(), w.strip()) for w in text.split(','))


_FURNISH_ID = {'unfurnished (kosongan)': 'kosongan', 'semi-furnished': 'semi furnish', 'fully furnished': 'full furnish'}
_INDONESIAN = [(re.compile(pattern, re.I), replacement) for pattern, replacement in (
    # Positive reasons.
    (r'Purpose: (buy or rent|buy|rent)', lambda m: 'Tujuan: ' + _PURPOSES[m.group(1).lower()]),
    (r'Unit: (.+)', lambda m: 'Unit: ' + _categories(m.group(1))),
    (r'Same location: (.+)', r'Lokasi sama: \1'),
    (r'Same cluster: (.+)', r'Cluster sama: \1'),
    (r'Same area: (.+)', r'Area sama: \1'),
    (r'Same building: (.+)', lambda m: 'Gedung sama: ' + _alts(m.group(1))),
    (r'Inside requested area: (.+)', lambda m: 'Di dalam area yang diminta: ' + m.group(1)),
    (r'Inside ([^:]+): (.+)', r'Di dalam \1: \2'),
    (r'Nearby alternative: (.+)', r'Alternatif lokasi terdekat: \1'),
    (r'Legal status: (.+)', lambda m: 'Status legal: ' + _alts(m.group(1))),
    (r'Unit type: (.+)', lambda m: 'Tipe unit: ' + _alts(m.group(1))),
    (r'(Tower|Blok) (?!not stated)(.+)', lambda m: f'{m.group(1)} ' + _alts(m.group(2))),
    (r'Floor level fits', 'Level lantai sesuai'),
    (r'Floor (\S+)', r'Lantai \1'),
    (r'Financing: (\S+) possible', r'Pembiayaan: bisa \1'),
    (r'Facing matches', 'Hadap sesuai'),
    (r'Building condition: old house / land value ✓', 'Kondisi bangunan: rumah lama / hitung tanah ✓'),
    (r'Building condition: new / move-in ready ✓', 'Kondisi bangunan: baru / siap huni ✓'),
    # v4.8: an explicit buyer requirement the listing shows ("Wajib minimalist", "Ngga mau TS").
    (r'Buyer requirement met: (.+?)( ✓)?', lambda m: f'Syarat buyer terpenuhi: {m.group(1)}{m.group(2) or ""}'),
    (r'Furnishing: (.+) ✓', lambda m: f'Furnitur: {_FURNISH_ID.get(m.group(1), m.group(1))} ✓'),
    (r'Lease term fits \(minimum (.+)\)', lambda m: f'Masa sewa sesuai (minimal {_period(m.group(1))})'),
    (r'Bedrooms match', 'Jumlah kamar tidur sesuai'),
    (r'Floors match', 'Jumlah lantai sesuai'),
    (r'Width matches', 'Lebar sesuai'),
    (r'Road width \(ROW\) matches', 'Lebar jalan (ROW) sesuai'),
    (r'Rent price within budget', 'Harga sewa masuk budget'),
    (r'Price within budget', 'Harga masuk budget'),
    (r'(.+) — within budget', lambda m: _amount(m.group(1)) + ' — masuk budget'),
    # Items to check (the "Needs check:" prefix is already removed).
    (r'Purpose not confirmed \((.+)\)', lambda m: f'Tujuan belum pasti ({_PURPOSES.get(m.group(1).lower(), m.group(1))})'),
    (r'Purpose not stated', 'Tujuan tidak disebutkan'),
    (r'Location not confirmed — buyer only accepts (.+)', lambda m: 'Lokasi belum pasti — buyer hanya mau ' + _alts(m.group(1))),
    (r'Rent period not stated', 'Periode sewa tidak disebutkan'),
    (r'Rent price basis differs \(per m² vs period\)', 'Dasar harga sewa berbeda (per m² vs periode)'),
    (r'Rent is per m² but the (\w+) area is not stated', lambda m: f'Sewa per m², tetapi luas {_measure(m.group(1))} tidak disebutkan'),
    (r'Rent budget is per m² but the (\w+) area is not stated',
     lambda m: f'Budget sewa per m², tetapi luas {_measure(m.group(1))} tidak disebutkan'),
    (r'Rent budget not stated', 'Budget sewa tidak disebutkan'),
    (r'Purchase budget not stated', 'Budget beli tidak disebutkan'),
    (r'Price looks like a rent price', 'Harga tampak seperti harga sewa'),
    (r'Price basis not confirmed', 'Dasar harga belum pasti'),
    (r'Price is per m² but the area is not stated', 'Harga per m², tetapi luasnya tidak disebutkan'),
    (r'Budget is per m² but the area is not stated', 'Budget per m², tetapi luasnya tidak disebutkan'),
    (r'Price basis differs', 'Dasar harga berbeda'),
    (r'Price must be below the buyer limit; negotiation needed', 'Harga harus di bawah batas buyer; perlu negosiasi'),
    (r'Listing price unclear \(wide range\)', 'Harga listing tidak jelas (rentang terlalu lebar)'),
    (r'Rent price not stated', 'Harga sewa tidak disebutkan'),
    (r'Listing price not stated', 'Harga listing tidak disebutkan'),
    (r'(.+) is (\d+)% above the (maximum )?budget (.+)',
     lambda m: _sentence(f"{_amount(m.group(1))} {m.group(2)}% di atas budget {'maksimal ' if m.group(3) else ''}{m.group(4)}")),
    (r'(.+) is (\d+)% below the (minimum )?budget (.+)',
     lambda m: _sentence(f"{_amount(m.group(1))} {m.group(2)}% di bawah budget {'minimal ' if m.group(3) else ''}{m.group(4)}")),
    (r'Message contains several requests; needs separating', 'Pesan berisi beberapa permintaan; perlu dipisah'),
    (r'Property is still rented out; handover date', 'Properti masih disewa; cek tanggal serah terima'),
    (r'Condition/price after construction work', 'Kondisi/harga setelah pekerjaan bangunan selesai'),
    (r'Legal status not stated — buyer only accepts (.+)', lambda m: 'Status legal tidak disebutkan — buyer hanya mau ' + _alts(m.group(1))),
    (r'Unit type not stated — buyer wants (.+)', lambda m: 'Tipe unit tidak disebutkan — buyer mau ' + _alts(m.group(1))),
    (r'(Tower|Blok) not stated — buyer only accepts (.+)',
     lambda m: f'{_sentence(m.group(1).lower())} tidak disebutkan — buyer hanya mau ' + _alts(m.group(2))),
    (r'Floor level not stated \(buyer wants (.+)\)', lambda m: f'Level lantai tidak disebutkan (buyer mau {_floor(m.group(1))})'),
    (r'Floor preference not met \(buyer prefers (.+)\)', lambda m: f'Preferensi lantai tidak terpenuhi (buyer lebih suka {_floor(m.group(1))})'),
    (r'Exact floor not stated \(buyer wants (.+)\)', lambda m: f'Lantai persisnya tidak disebutkan (buyer mau {_floor(m.group(1))})'),
    (r'Listing quotes a cash price; (\S+) not confirmed', r'Listing menyebut harga cash; \1 belum pasti'),
    (r'Extracted details need review', 'Detail hasil baca perlu ditinjau'),
    (r'Transaction type not confirmed', 'Jenis transaksi (jual/sewa) belum pasti'),
    (r'Message contains several offers/prices; held back until each property is separated',
     'Pesan berisi beberapa penawaran/harga; ditahan sampai tiap properti dipisah'),
    (r'Specific location (.+) not confirmed', r'Lokasi spesifik \1 belum pasti'),
    (r'City-centre location not confirmed', 'Lokasi pusat kota belum pasti'),
    (r'No evidence of (.+)', r'Tidak ada bukti \1'),
    (r"Buyer's required road/street", 'Jalan yang diwajibkan buyer'),
    (r'(.+) not stated in a comparable unit', lambda m: _sentence(f'{_measure(m.group(1))} tidak disebutkan dalam satuan yang sebanding')),
    (r'Facing not stated', 'Hadap tidak disebutkan'),
    (r'Facing not stated \(buyer refuses (.+)\)', lambda m: f'Hadap tidak disebutkan (buyer menolak {_alts(m.group(1))})'),
    (r'Minimum lease not stated \(renter wants (.+)\)', lambda m: f'Masa sewa minimal tidak disebutkan (penyewa mau {_period(m.group(1))})'),
    (r'View (.+) not confirmed', r'View \1 belum pasti'),
    (r'Width × length not stated', 'Lebar × panjang tidak disebutkan'),
    (r'Dimensions within tolerance', 'Dimensi masih dalam toleransi'),
    (r'Feasibility of building (.+) floors', r'Kelayakan membangun \1 lantai'),
    (r'(Bedrooms|Floors) not stated', lambda m: _sentence(f'{_measure(m.group(1))} tidak disebutkan')),
    (r'Road access width', 'Lebar akses jalan'),
    (r'Buyer wants it unfurnished \(kosongan\); listing is furnished', 'Buyer mau kosongan; listing sudah ada furnitur'),
    (r'Building condition \(buyer wants an old house to rebuild\)', 'Kondisi bangunan (buyer cari rumah lama untuk dibangun ulang)'),
    (r'Building condition \(buyer wants a new house\)', 'Kondisi bangunan (buyer cari rumah baru)'),
    (r'Building condition \(buyer wants a move-in ready house\)', 'Kondisi bangunan (buyer cari rumah siap huni)'),
    (r'Building condition \(buyer accepts an old house only if it needs little renovation\)',
     'Kondisi bangunan (buyer mau rumah lama hanya jika minim renovasi)'),
    (r'Requirement not verified: (.+)', r'Syarat buyer belum terbukti: \1'),
    (r'Buyer budget not stated', 'Budget buyer tidak disebutkan'),
    (r'Buyer request has only (\d+) of (\d+) required details(?: \(missing (.+)\))?',
     lambda m: f'Request buyer baru berisi {m.group(1)} dari {m.group(2)} detail wajib'
               + (f' (kurang {_DETAIL_ID(m.group(3))})' if m.group(3) else '')),
    (r'Furnishing not stated \(buyer wants it (.+)\)', lambda m: f'Furnitur tidak disebutkan (buyer mau {_FURNISH_ID.get(m.group(1), m.group(1))})'),
    (r'Furnishing: buyer wants it (.+); listing is (.+)',
     lambda m: f'Furnitur: buyer mau {_FURNISH_ID.get(m.group(1), m.group(1))}; listing {_FURNISH_ID.get(m.group(2), m.group(2))}'),
    (r'Buyer approval for a nearby location \(within (.+)\)', r'Perlu persetujuan buyer untuk lokasi terdekat (dalam \1)'),
    (r'Exact cluster not confirmed \(buyer named (.+)\)', r'Cluster persisnya belum pasti (buyer menyebut \1)'),
    (r'Exact location not recognised', 'Lokasi persisnya tidak dikenali'),
    (r'Listing names several places; exact cluster not confirmed', 'Listing menyebut beberapa tempat; cluster persisnya belum pasti'),
    (r'Listing names a place in another area; exact location not confirmed',
     'Listing menyebut tempat di area lain; lokasi persisnya belum pasti'),
    (r'Same development \((.+)\); exact cluster not confirmed', r'Kawasan sama (\1); cluster persisnya belum pasti'),
    (r'Listing area (.+) is near (.+); exact cluster not confirmed', r'Area listing \1 dekat \2; cluster persisnya belum pasti'),
    (r'Exact location not confirmed \(buyer named (.+)\)', r'Lokasi persisnya belum pasti (buyer menyebut \1)'),
    (r'Exact location not confirmed', 'Lokasi persisnya belum pasti'),
    (r'Buyer named only the city; exact area not confirmed', 'Buyer hanya menyebut kota; area persisnya belum pasti'),
    (r'Requested location not confirmed', 'Lokasi yang diminta belum pasti'),
    (r'Requested location is only mentioned later in the message', 'Lokasi yang diminta baru disebut di bagian akhir pesan'),
    (r'Specific area/address not confirmed', 'Area/alamat spesifik belum pasti'),
    (r'Apartment name not stated — buyer asked for (.+)', lambda m: 'Nama apartemen tidak disebutkan — buyer minta ' + _alts(m.group(1))),
    (r'Citraland Utama boundary not confirmed', 'Batas Citraland Utama belum pasti'),
    (r'Preferred area not confirmed \((.+)\)', r'Area pilihan buyer belum pasti (\1)'),
    (r'(Land area|Building area) could not be read; pair held back', lambda m: _sentence(f'{_measure(m.group(1))} tidak terbaca; pasangan ditahan')),
    (r'(Land area|Building area) not stated in listing', lambda m: _sentence(f'{_measure(m.group(1))} tidak disebutkan di listing')),
    (r'(Land area|Building area) (.+?) is (below|above) the requested (.+)',
     lambda m: _sentence(f"{_measure(m.group(1))} {m.group(2)} {'di bawah' if m.group(3).lower() == 'below' else 'di atas'} permintaan {m.group(4)}")),
    (r'Listing price could not be read; pair held back', 'Harga listing tidak terbaca; pasangan ditahan'),
    (r'Rent offer not confirmed \(listing states only a sale price\)', 'Penawaran sewa belum pasti (listing hanya menyebut harga jual)'),
    (r'Budget amount could not be read; pair held back', 'Nilai budget tidak terbaca; pasangan ditahan'),
    (r'Several listings in one message; held back until each property is separated',
     'Beberapa listing dalam satu pesan; ditahan sampai tiap properti dipisah'),
    (r'Price fits neither stated budget; pair held back', 'Harga tidak cocok dengan budget mana pun; pasangan ditahan'),
    # Buyer requirements and flags: "<requirement> not confirmed" or the bare label.
    (r'(.+) not confirmed', lambda m: _sentence(_label(m.group(1)) + ' belum pasti') if _label(m.group(1)) != m.group(1).strip() else None),
    (r'(.+)', lambda m: _sentence(_label(m.group(1))) if _label(m.group(1)) != m.group(1).strip() else None),
)]


def indonesian(text):
    """The Indonesian wording of one tidied reason; an unknown line keeps its English text."""
    line = str(text or '').strip()
    for pattern, replacement in _INDONESIAN:
        m = pattern.fullmatch(line)
        if not m:
            continue
        result = replacement(m) if callable(replacement) else m.expand(replacement)
        if result is None:
            continue
        return result.replace('(within the Warm range)', '(masih dalam rentang Warm)')
    return line


def jev_callout(target):
    """(title, [(kind, text)]) of a pair the AI review approved (``target['jev']``, see ai_review.approved_pairs), else None.
    Generic on purpose: the PDF names neither the model nor a confidence, only the Indonesian reasons."""
    jev = (target or {}).get('jev')
    if not jev:
        return None
    items = [('positive', str(text)) for text in jev.get('reasons') or [] if str(text).strip()]
    items += [('check', str(text)) for text in jev.get('issues') or [] if str(text).strip()]
    if jev.get('override'):
        items.insert(0, ('positive', 'Disetujui manual oleh admin.'))
    return 'Saran AI', items or [('check', 'Tidak ada alasan dari AI.')]


def callout_items(target):
    """[(kind, Indonesian text)] for the reasons callout of a pair page: positives, then items to check."""
    if not target:
        return []
    positive, checks = split_reasons(target.get('explanation'))
    return [('positive', indonesian(text)) for text in positive] + [('check', indonesian(text)) for text in checks]


def _callout_layout(items, width):
    """(size, [column items], height): two balanced columns of bullets that shrink, never grow, to fit."""
    column = (width - 2 * CALLOUT_PADDING - 14) / 2 - CALLOUT_ICON
    size = CALLOUT_FONT_SIZE
    while True:
        style = ParagraphStyle('reason', fontName='Helvetica', fontSize=size, leading=size * 1.28, textColor=HexColor('#25324b'),
                               wordWrap='CJK')
        placed = []
        for kind, text in items:
            markup = escape(_cp1252(text))
            if kind == 'check':
                markup = f'<font name="Helvetica-Bold" color="#b45309">Perlu dicek:</font> ' + markup
            paragraph = Paragraph(markup, style)
            _, height = paragraph.wrap(column, 1000)
            placed.append((kind, paragraph, height))
        heights = [height + CALLOUT_ITEM_GAP for _, _, height in placed]
        split = min(range(len(placed) + 1), key=lambda k: (max(sum(heights[:k]), sum(heights[k:])), -k))
        columns = [placed[:split], placed[split:]]
        body = max(sum(heights[:split]), sum(heights[split:])) - CALLOUT_ITEM_GAP
        title = size + 6
        total = 2 * CALLOUT_PADDING + title + body
        if total <= CALLOUT_MAX_HEIGHT or size <= CALLOUT_MIN_SIZE:
            return size, columns, total
        size = max(CALLOUT_MIN_SIZE, size - 0.5)


def _check_mark(pdf, x, y, size):
    """Green round check, drawn as shapes (the built-in fonts have no ✓)."""
    pdf.setFillColor(HexColor('#16a34a'))
    pdf.circle(x, y, size / 2, fill=1, stroke=0)
    pdf.setStrokeColor(HexColor('#ffffff'))
    pdf.setLineWidth(size * 0.16)
    pdf.setLineCap(1); pdf.setLineJoin(1)
    path = pdf.beginPath()
    path.moveTo(x - size * 0.24, y + size * 0.01)
    path.lineTo(x - size * 0.06, y - size * 0.18)
    path.lineTo(x + size * 0.25, y + size * 0.18)
    pdf.drawPath(path, fill=0, stroke=1)


def _attention_mark(pdf, x, y, size):
    """Amber round "!" for an item to check."""
    pdf.setFillColor(HexColor('#d97706'))
    pdf.circle(x, y, size / 2, fill=1, stroke=0)
    pdf.setStrokeColor(HexColor('#ffffff'))
    pdf.setLineWidth(size * 0.15)
    pdf.setLineCap(1)
    pdf.line(x, y + size * 0.24, x, y - size * 0.04)
    pdf.setFillColor(HexColor('#ffffff'))
    pdf.circle(x, y - size * 0.22, size * 0.08, fill=1, stroke=0)


def draw_callout(pdf, items, top, title=CALLOUT_TITLE):
    """The "why recommended" box under the status bar; returns its bottom edge."""
    x, width = MARGIN, PAGE_WIDTH - 2 * MARGIN
    size, columns, height = _callout_layout(items, width)
    bottom = top - height
    pdf.saveState()
    pdf.setFillColor(HexColor('#f8fafc'))
    pdf.setStrokeColor(HexColor('#dbe3ef'))
    pdf.setLineWidth(0.8)
    pdf.roundRect(x, bottom, width, height, 7, fill=1, stroke=1)
    pdf.setFillColor(HexColor('#1645a0'))
    pdf.setFont('Helvetica-Bold', size)
    pdf.drawString(x + CALLOUT_PADDING, top - CALLOUT_PADDING - size * 0.85, latin(title))
    column_width = (width - 2 * CALLOUT_PADDING - 14) / 2
    mark = min(10.0, size + 0.5)
    for index, entries in enumerate(columns):
        cursor = top - CALLOUT_PADDING - size - 6
        left = x + CALLOUT_PADDING + index * (column_width + 14)
        for kind, paragraph, used in entries:
            (_check_mark if kind == 'positive' else _attention_mark)(pdf, left + mark / 2, cursor - size * 0.62, mark)
            paragraph.drawOn(pdf, left + CALLOUT_ICON, cursor - used)
            cursor -= used + CALLOUT_ITEM_GAP
    pdf.restoreState()
    return bottom


def evidence(source, target, direction):
    """Highlight spans for the (source, target) texts as printed; empty when anything is unexpected."""
    if not target:
        return (), ()
    try:
        from matching_rules import evidence_spans
        request, listing = (source, target) if direction == 'buyer' else (target, source)
        spans = evidence_spans(request, listing, target.get('explanation'), message_text(request), message_text(listing))
    except Exception:  # a highlight must never cost the agent the page
        return (), ()
    return (spans['request'], spans['listing']) if direction == 'buyer' else (spans['listing'], spans['request'])


def phrase_spans(text, phrases):
    """(start, end) of the first occurrence of each phrase in ``text`` (case and spacing tolerant), merged."""
    spans = []
    for phrase in phrases or ():
        words = str(phrase or '').split()
        if not words:
            continue
        found = re.search(r'\s+'.join(re.escape(word) for word in words), text or '', re.I)
        if found:
            spans.append((found.start(), found.end()))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


def jev_evidence(source, target, direction):
    """Highlights from Jev's evidence phrases; a side without a phrase found falls back to the engine's spans."""
    phrases = ((target or {}).get('jev') or {}).get('evidence') or {}
    request, listing = (source, target) if direction == 'buyer' else (target, source)
    found = {'request': phrase_spans(message_text(request), phrases.get('buyer')),
             'listing': phrase_spans(message_text(listing), phrases.get('listing'))}
    if not found['request'] or not found['listing']:
        engine = evidence(source, target, direction)
        engine = {'request': engine[0], 'listing': engine[1]} if direction == 'buyer' else {'request': engine[1], 'listing': engine[0]}
        found = {side: found[side] or engine[side] for side in found}
    return (found['request'], found['listing']) if direction == 'buyer' else (found['listing'], found['request'])


# ---------------------------------------------------------------- covers
def _arrow(pdf, x, y, length, weight, color):
    """A right arrow drawn as a shape (the built-in fonts have no →); (x, y) is the left end on the centre line."""
    pdf.saveState()
    pdf.setStrokeColor(HexColor(color)); pdf.setFillColor(HexColor(color))
    pdf.setLineWidth(weight); pdf.setLineCap(1)
    head = weight * 2.6
    pdf.line(x, y, x + length - head * 0.8, y)
    path = pdf.beginPath()
    path.moveTo(x + length, y); path.lineTo(x + length - head, y + head * 0.75); path.lineTo(x + length - head, y - head * 0.75)
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)
    pdf.restoreState()


def direction_width(direction, font, size):
    first, second = SECTION_STYLES[direction]['words']
    return stringWidth(first, font, size) + stringWidth(second, font, size) + size * 1.9


def draw_direction(pdf, direction, x, y, font, size, color):
    """"Listing → Buyer" at (x, y) with a drawn arrow."""
    first, second = SECTION_STYLES[direction]['words']
    pdf.setFillColor(HexColor(color))
    pdf.setFont(font, size)
    pdf.drawString(x, y, first)
    start = x + stringWidth(first, font, size) + size * 0.3
    _arrow(pdf, start, y + size * 0.33, size * 1.3, max(1.0, size * 0.09), color)
    pdf.drawString(start + size * 1.6, y, second)


def _phone_text(value):
    """``6287852258118`` -> ``+62 878-5225-8118``, the way the app shows numbers."""
    digits = ''.join(c for c in str(value or '') if c.isdigit())
    national = digits[2:] if digits.startswith('62') else digits.lstrip('0')
    return '+62 ' + '-'.join(part for part in (national[:3], national[3:7], national[7:]) if part) if national else ''


def _logo_path():
    """The Auto Audit logo: the image baked into the API container, else the repo's public asset."""
    logo = Path('/assets/brand-auto-audit.png')
    return logo if logo.exists() else Path(__file__).resolve().parent.parent / 'public/brand-auto-audit.png'


def draw_cover(pdf, cover, logo, generated):
    """First page of a section in the export of every match: whose matches follow, and how many."""
    if cover.get('combined'):
        draw_section_cover(pdf, cover, logo, generated)
        return
    right = PAGE_WIDTH - MARGIN
    pdf.drawImage(logo, MARGIN, PAGE_HEIGHT - 77, width=170, height=69, mask='auto', preserveAspectRatio=True)
    pdf.setFont('Helvetica', 10)
    pdf.setFillColor(HexColor('#53617b'))
    pdf.drawRightString(right, PAGE_HEIGHT - 35, 'Dibuat: ' + generated)
    pdf.setStrokeColor(HexColor('#dbe3ef'))
    pdf.line(MARGIN, PAGE_HEIGHT - 85, right, PAGE_HEIGHT - 85)
    pdf.setFont('Helvetica', 14)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 250, cover['kind'])
    pdf.setFillColor(HexColor('#1645a0'))
    title = latin(cover['title']) or '-'
    size = 30
    while size > 12 and pdf.stringWidth(title, 'Helvetica-Bold', size) > right - MARGIN:
        size -= 1
    pdf.setFont('Helvetica-Bold', size)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 290, title)
    source = 'buyer' if cover['direction'] == 'buyer' else 'listing'
    lines = [(f'{cover["sources"]} {source}', '#25324b')]
    if cover['hot']: lines.append((f'{cover["hot"]} kecocokan Hot', '#b91c1c'))
    if cover['warm']: lines.append((f'{cover["warm"]} kecocokan Warm', '#b45309'))
    if cover.get('custom'): lines.append((f'{cover["custom"]} kecocokan Custom', '#6d28d9'))
    if cover['unmatched']: lines.append((f'{cover["unmatched"]} belum cocok', '#53617b'))
    pdf.setFont('Helvetica-Bold', 15)
    for row, (text, color) in enumerate(lines):
        pdf.setFillColor(HexColor(color))
        pdf.drawString(MARGIN, PAGE_HEIGHT - 340 - row * 26, text)
    pdf.showPage()


def draw_section_cover(pdf, cover, logo, generated):
    """Full-colour first page of one direction of one person in the export of both directions."""
    style = SECTION_STYLES[cover['direction']]
    right = PAGE_WIDTH - MARGIN
    pdf.setFillColor(HexColor(style['background']))
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
    # The logo is dark: it sits on a white card.
    pdf.setFillColor(HexColor('#ffffff'))
    pdf.roundRect(MARGIN, PAGE_HEIGHT - 104, right - MARGIN, 88, 14, fill=1, stroke=0)
    pdf.drawImage(logo, MARGIN + 12, PAGE_HEIGHT - 95, width=170, height=69, mask='auto', preserveAspectRatio=True)
    pdf.setFont('Helvetica', 10)
    pdf.setFillColor(HexColor('#53617b'))
    pdf.drawRightString(right - 16, PAGE_HEIGHT - 55, 'Dibuat: ' + generated)
    pdf.setFillColor(HexColor(style['muted']))
    pdf.setFont('Helvetica-Bold', 13)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 175, f"BAGIAN {cover.get('part', 1)} DARI {cover.get('parts', 1)}", charSpace=1.5)
    draw_direction(pdf, cover['direction'], MARGIN, PAGE_HEIGHT - 228, 'Helvetica-Bold', 40, style['text'])
    pdf.setFillColor(HexColor(style['muted']))
    pdf.setFont('Helvetica', 15)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 258, style['about'])
    pdf.setStrokeColor(HexColor(style['muted']))
    pdf.setLineWidth(0.8)
    pdf.line(MARGIN, PAGE_HEIGHT - 285, right, PAGE_HEIGHT - 285)
    pdf.setFont('Helvetica', 14)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 322, cover['kind'])
    title = latin(cover['title']) or '-'
    size = 30
    while size > 12 and stringWidth(title, 'Helvetica-Bold', size) > right - MARGIN:
        size -= 1
    pdf.setFillColor(HexColor(style['text']))
    pdf.setFont('Helvetica-Bold', size)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 360, title)
    y = PAGE_HEIGHT - 388
    phone = _phone_text(cover.get('phone'))
    if phone and re.sub(r'\D', '', phone) not in re.sub(r'\D', '', title):  # a phone group's title already shows it
        pdf.setFillColor(HexColor(style['muted']))
        pdf.setFont('Helvetica', 13)
        pdf.drawString(MARGIN, y, 'Telp: ' + phone)
        y -= 24
    y -= 30
    pairs = sum(int(cover.get(key) or 0) for key, _ in COUNT_CHIPS)
    pdf.setFillColor(HexColor(style['text']))
    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(MARGIN, y, f"{cover['sources']} {style['source']} · {pairs} halaman pasangan")
    y -= 40
    x = MARGIN
    for key, word in COUNT_CHIPS:
        count = int(cover.get(key) or 0)
        if not count:
            continue
        background, color, _ = STATUS_BARS[key]
        text = f'{count} {word}'
        width = stringWidth(text, 'Helvetica-Bold', 13) + 24
        pdf.setFillColor(HexColor(background))
        pdf.roundRect(x, y - 8, width, 26, 13, fill=1, stroke=0)
        pdf.setFillColor(HexColor(color))
        pdf.setFont('Helvetica-Bold', 13)
        pdf.drawString(x + 12, y + 1, text)
        x += width + 10
    pdf.setFillColor(HexColor(style['muted']))
    pdf.setFont('Helvetica', 11)
    pdf.drawString(MARGIN, 110, f"Setiap halaman berikut: kiri {style['source']}, kanan {style['target']} yang cocok, dengan nomor urut.")
    pdf.drawString(MARGIN, 94, 'Kata yang distabilo kuning menunjukkan bagian pesan yang cocok.')
    pdf.showPage()


# ---------------------------------------------------------------- pages
def build_report(pairs, direction, section=None, cover=None, first_pair=1, combined=False, company_name=None):
    """One page per pair. With ``section`` the pages belong to one person (sender or sales contact) inside a larger
    export: the header names that person, each pair page carries its number ("No. N", from ``first_pair``), and the
    footer page numbers are added when the parts are merged (see stamp_page_numbers). ``combined``: the export holds
    both directions, so pages show which one they belong to. ``company_name`` names the "company" ownership tier."""
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle('Property Matchmaker')
    months = ['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember']
    now = datetime.now(ZoneInfo('Asia/Jakarta'))
    generated = f'{now.day:02d} {months[now.month-1]} {now.year}'
    logo = ImageReader(str(_logo_path()))  # decoded once, not once per page
    right = PAGE_WIDTH - MARGIN
    labels = ['Buyer request','Property listing'] if direction == 'buyer' else ['Property listing','Buyer request']
    section = latin(section)[:60] if section is not None else None
    if cover:
        draw_cover(pdf, cover, logo, generated)
    for index, (source, target) in enumerate(pairs, 1):
        pdf.drawImage(logo, MARGIN, PAGE_HEIGHT - 77,
                      width=170, height=69, mask='auto', preserveAspectRatio=True)
        pdf.setFont('Helvetica', 10)
        pdf.setFillColor(HexColor('#53617b'))
        pdf.drawRightString(right, PAGE_HEIGHT - 35, 'Dibuat: ' + generated)
        pdf.drawRightString(right, PAGE_HEIGHT - 55, section if section is not None else f'Pilihan {index} / {len(pairs)}')
        if combined:
            style = SECTION_STYLES[direction]
            width = direction_width(direction, 'Helvetica-Bold', 8.5) + 16
            pdf.setFillColor(HexColor(style['background']))
            pdf.roundRect(right - width, PAGE_HEIGHT - 80, width, 15, 7.5, fill=1, stroke=0)
            draw_direction(pdf, direction, right - width + 8, PAGE_HEIGHT - 75.5, 'Helvetica-Bold', 8.5, style['text'])
        pdf.setStrokeColor(HexColor('#dbe3ef'))
        pdf.line(MARGIN, PAGE_HEIGHT - 85, right, PAGE_HEIGHT - 85)
        status_bar(pdf, pair_status(target), first_pair + index - 1 if section is not None else None, direction if combined else None,
                   ownership_label(target, direction, company_name))
        label_y, body_top = LABEL_Y, BODY_TOP
        jev = jev_callout(target)
        items = jev[1] if jev else callout_items(target)
        if items:
            bottom = draw_callout(pdf, items, STATUS_BAR_Y - CALLOUT_GAP, title=jev[0] if jev else CALLOUT_TITLE)
            label_y = bottom - 10 - 32
            body_top = label_y - (LABEL_Y - BODY_TOP)
        ids = [source.get('public_id'), target.get('public_id') if target else None]
        texts = [message_text(source), message_text(target) if target else 'Belum ada kecocokan.']
        part_labels = [part_label(source), part_label(target) if target else None]
        marks = jev_evidence(source, target, direction) if jev else evidence(source, target, direction)
        for col, x in enumerate([MARGIN, MARGIN + COLUMN_WIDTH + COLUMN_GAP]):
            pdf.setFillColor(HexColor('#edf3fc'))
            pdf.roundRect(x, label_y, COLUMN_WIDTH, 32, 6, fill=1, stroke=0)
            pdf.setFillColor(HexColor('#1645a0'))
            pdf.setFont('Helvetica-Bold', 11)
            title = labels[col] + (' | ' + ids[col] if ids[col] else '')
            pdf.drawString(x + 12, label_y + 11, title)
            paragraph, used_height = fit_paragraph(texts[col], height=body_top - BODY_BOTTOM, label=part_labels[col], highlights=marks[col])
            paragraph.drawOn(pdf, x + 12, body_top - used_height)

        # Only the recommendation on the right has a WhatsApp action.
        if target and target.get('contact_phone'):
            x = MARGIN + COLUMN_WIDTH + COLUMN_GAP + 12
            phone = ''.join(c for c in target['contact_phone'] if c.isdigit())
            if phone.startswith('0'):
                phone = '62' + phone[1:]
            elif phone.startswith('8'):
                phone = '62' + phone
            pdf.setFillColor(HexColor('#146348'))
            pdf.roundRect(x, BUTTON_Y, TEXT_WIDTH, 25, 5, fill=1, stroke=0)
            pdf.setFillColor(HexColor('#ffffff'))
            pdf.setFont('Helvetica-Bold', 11)
            pdf.drawString(x + 10, BUTTON_Y + 8, 'WhatsApp: +' + phone)
            pdf.linkURL(contact_url(target, 'property' if direction == 'buyer' else 'buyer'),
                        (x, BUTTON_Y, x + TEXT_WIDTH, BUTTON_Y + 25), relative=0)
        if section is None:
            pdf.setFillColor(HexColor(FOOTER_COLOR))
            pdf.setFont('Helvetica', 9)
            pdf.drawRightString(right, FOOTER_Y, f'Property Matchmaker | {index}')
        pdf.showPage()
    pdf.save()
    return output.getvalue()


# ---------------------------------------------------------------- page numbers of a merged export
def _rounded(x, y, width, height, radius):
    k = radius * 0.5523
    r, top, end = radius, y + height, x + width
    return (f'{x + r:.2f} {y:.2f} m {end - r:.2f} {y:.2f} l {end - r + k:.2f} {y:.2f} {end:.2f} {y + r - k:.2f} {end:.2f} {y + r:.2f} c '
            f'{end:.2f} {top - r:.2f} l {end:.2f} {top - r + k:.2f} {end - r + k:.2f} {top:.2f} {end - r:.2f} {top:.2f} c '
            f'{x + r:.2f} {top:.2f} l {x + r - k:.2f} {top:.2f} {x:.2f} {top - r + k:.2f} {x:.2f} {top - r:.2f} c '
            f'{x:.2f} {y + r:.2f} l {x:.2f} {y + r - k:.2f} {x + r - k:.2f} {y:.2f} {x + r:.2f} {y:.2f} c h')


def stamp_page_numbers(writer, start, count):
    """Footer "Property Matchmaker | Hal. N dari M" on the pypdf ``writer`` pages [start, start + count), N from 1:
    every person's pages (one file of a ZIP, one section group of a combined PDF) are numbered on their own.
    The footer sits on a small white label so it reads on the coloured covers too. Only the page's content list
    and font resources change; the page content itself is never parsed."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, IndirectObject, NameObject

    def stream(data):
        value = DecodedStreamObject()
        value.set_data(data)
        return writer._add_object(value)

    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding')}))
    opening = stream(b'q\n')
    red, green, blue = (int(FOOTER_COLOR[i:i + 2], 16) / 255 for i in (1, 3, 5))
    for number in range(1, count + 1):
        page = writer.pages[start + number - 1]
        if '/Resources' not in page:
            page[NameObject('/Resources')] = DictionaryObject()
        resources = page['/Resources']
        if '/Font' not in resources:
            resources[NameObject('/Font')] = DictionaryObject()
        resources['/Font'][NameObject('/XMFooter')] = font
        text = f'Property Matchmaker | Hal. {number} dari {count}'
        width = stringWidth(text, 'Helvetica', 9)
        x = float(page.mediabox.right) - MARGIN - width
        footer = (f'Q\nq\n1 1 1 rg\n{_rounded(x - 7, FOOTER_Y - 5, width + 14, 16, 8)} f\n{red:.3f} {green:.3f} {blue:.3f} rg\n'
                  f'BT /XMFooter 9 Tf {x:.2f} {FOOTER_Y:.2f} Td ({text}) Tj ET\nQ\n').encode('latin-1')
        contents = page.raw_get('/Contents') if '/Contents' in page else None
        resolved = contents.get_object() if contents is not None else None
        if isinstance(resolved, ArrayObject):
            existing = list(resolved)
        elif contents is None:
            existing = []
        else:
            existing = [contents if isinstance(contents, IndirectObject) else writer._add_object(contents)]
        page[NameObject('/Contents')] = ArrayObject([opening, *existing, stream(footer)])
