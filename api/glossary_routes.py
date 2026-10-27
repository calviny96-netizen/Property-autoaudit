"""Structured glossary and location-knowledge endpoints (v4.1).

The company glossary teaches the parser how agents write: 'synonym' rewrites text,
'location' names a place (optionally inside a parent area), 'category' maps a word to
an asset type. Saving only saves and marks results out of date; POST /reprocess
applies it to existing data. The legacy dict API (GET/PUT /glossary) lives in workspace.py.
GET /glossary/terms lists the built-in property vocabulary (legal status, unit types, …), read-only.
"""
import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import parser
from db import connect
from entities import GROUP_KEY
from location_index import LocationIndex, key
from matching_scope import mark_reprocess_pending, reprocess_status
from reindex import glossary_entries

try:
    from locations import normalize_place as _place
except ImportError:  # older engine builds keep the helper private
    def _place(value):
        return re.sub(r"\s+", " ", re.sub(r"[^\w&' ]+", " ", str(value or "").lower())).strip()

router = APIRouter(prefix='/glossary')
KINDS = ('synonym', 'location', 'category')
MAX_ENTRIES, MAX_TEXT, MAX_NOTE = 2000, 100, 500
REASON = 'Glossary updated'
CATEGORY_LABELS = {
    'warehouse': 'Warehouse', 'factory': 'Factory', 'shophouse': 'Shophouse', 'villa': 'Villa', 'land': 'Land',
    'apartment': 'Apartment', 'office': 'Office', 'hotel': 'Hotel', 'house': 'House',
    'commercial_building': 'Commercial building',
}


def _lock(conn):
    # Short per-company settings lock (see matching_scope): serialises concurrent saves.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")


def _default_glossary():
    return dict(getattr(parser, 'DEFAULT_GLOSSARY', {}) or {})


def _default_parents():
    return dict(getattr(parser, 'DEFAULT_LOCATION_PARENTS', {}) or {})


def categories():
    labels = getattr(parser, 'CATEGORY_LABELS', {}) or {}
    return [{'key': name, 'label': CATEGORY_LABELS.get(name) or labels.get(name) or name.replace('_', ' ').capitalize()}
            for name in parser.CATEGORY_PATTERNS]


def default_entries():
    """Built-in, read-only knowledge: synonyms, then the place hierarchy (child → parent)."""
    entries = [{'term': term, 'canonical': canonical, 'kind': 'synonym', 'parent': None}
               for term, canonical in sorted(_default_glossary().items())]
    entries += [{'term': child, 'canonical': child, 'kind': 'location', 'parent': parent}
                for child, parent in sorted(_default_parents().items())]
    return entries


@router.get('/entries')
def get_entries():
    with connect() as conn:
        entries = glossary_entries(conn)
    return {'entries': entries, 'defaults': default_entries(), 'categories': categories()}


# ---------------------------------------------------------------- built-in property terms
def _term(key, term, aliases, meaning):
    return {'key': key, 'term': term, 'aliases': list(aliases), 'meaning': meaning}


# Display text for the vocabulary the parser and assess_pair understand (v4.3 R8). Explanations are English
# (product UI); terms and aliases stay as agents write them, and an alias is only listed when the parser reads it. Legal titles and unit types follow the keys the
# parser exposes (parser.LEGAL_LABELS / parser.UNIT_TYPE_LABELS) so the list never claims more than it reads.
LEGAL_TERMS = {
    'shm': _term('shm', 'SHM', ('sertifikat hak milik', 'hak milik'), 'Freehold title: full, permanent ownership of the land.'),
    'hgb': _term('hgb', 'HGB / SHGB', ('hak guna bangunan', 'SHGB'),
                 'Right-to-build title for a fixed, renewable term; often upgradeable to SHM.'),
    'surat_ijo': _term('surat_ijo', 'Surat Ijo', ('surat hijau', 'IPT', 'izin pemakaian tanah'),
                       'Surabaya municipal land-use permit on city-owned land, not freehold. A buyer who wants Surat Ijo '
                       'never matches SHM/HGB, and the other way round.'),
    'strata': _term('strata', 'SHMRS / SHSRS / strata', ('SHM-RS', 'SHSRS', 'strata title', 'sertifikat strata', 'SHM strata'),
                    'Strata title for an apartment unit. "SHM strata" means this title, not a house SHM.'),
    'ppjb': _term('ppjb', 'PPJB', ('SPPJB',),
                  'Perjanjian pengikatan jual beli: binding sale agreement made before the certificate can be '
                  'transferred (common for new projects).'),
    'ajb': _term('ajb', 'AJB', (), 'Akta jual beli: deed of sale signed before a land deed official; it transfers the title.'),
    'petok': _term('petok', 'Petok D / Girik', ('petok', 'girik', 'letter C', 'leter C'),
                   'Old village land record, not a certificate; it must be converted to SHM first.'),
    'hpl': _term('hpl', 'HPL', (), 'Hak pengelolaan lahan: right of management over state land; titles on it are usually HGB.'),
}
UNIT_TERMS = {
    'studio': _term('studio', 'Studio', ('tipe studio', 'studio corner'), 'One open room with a bathroom, no separate bedroom.'),
    '1br': _term('1br', '1BR', ('1 BR', '1 bedroom', '1 kamar', '1KT'), 'One separate bedroom.'),
    '2br': _term('2br', '2BR', ('2 BR', '2 bedroom', '2 kamar', '2KT'), 'Two bedrooms.'),
    '3br': _term('3br', '3BR', ('3 BR', '3 bedroom', '3KT'), 'Three bedrooms; 4BR and more work the same way.'),
    'penthouse': _term('penthouse', 'Penthouse', ('pent house',), 'Premium unit on the top floor.'),
}
# 4BR, 5BR, … are read too; the 3BR line speaks for them.
UNIT_TERMS_SHOWN_BY = {f'{n}br': '3br' for n in range(4, 10)}
STATIC_GROUPS = (
    {'key': 'tower', 'label': 'Towers and blocks',
     'note': 'A buyer who names towers only gets those towers: a listing in another tower is rejected, one '
             'that does not state its tower is Custom.',
     'terms': [
         _term('tower', 'Tower A', ('Tw A', 'Tower Anderson', 'Yale Tower'), 'One tower, by letter or by name. Single letters count.'),
         _term('tower_list', 'Tower A atau C', ('tower A/C', 'Tower A atau C saja'), 'Either named tower (A or C), nothing else.'),
         _term('block', 'Blok', ('Blok GC',), 'Estate block, when the buyer names one.'),
     ]},
    {'key': 'financing', 'label': 'Financing',
     'note': 'A buyer who needs KPR or KPA is not matched with cash-only listings or with titles banks do not '
             'finance (Surat Ijo, Petok D / Girik, PPJB only). A cash buyer accepts every listing.',
     'terms': [
         _term('kpr', 'KPR', ('bisa KPR',), 'Kredit pemilikan rumah: house mortgage from a bank.'),
         _term('kpa', 'KPA', ('bisa KPA', 'pembelian KPA'), 'Kredit pemilikan apartemen: apartment mortgage from a bank.'),
         _term('cash_hard', 'Cash keras', ('cash only', 'cash keras saja'), 'Full payment in cash at once, no mortgage.'),
         _term('cash_staged', 'Cash bertahap', (), 'Cash paid in instalments to the seller or developer, without a bank.'),
         _term('cash', 'Cash', ('tunai',), 'A buyer who pays cash accepts every listing.'),
     ]},
    {'key': 'floor', 'label': 'Floor level',
     'note': 'Apartments and offices only. A listing on a floor the buyer rules out is rejected; an unknown '
             'floor needs a check (Warm). Not the same as LT (land area) or the storeys of a house.',
     'terms': [
         _term('floor_limit', 'Lantai di bawah 10', ('lantai max 18', 'lantai 5-15', 'lantai 10 ke atas'),
               'Highest or lowest floor, or floor range, the buyer accepts.'),
         _term('low_floor', 'Low floor', ('lantai bawah', 'lt bawah'), 'Floor 10 or lower.'),
         _term('mid_floor', 'Mid floor', ('lantai tengah',), 'Floors 8 to 20.'),
         _term('high_floor', 'High floor', ('lantai atas', 'lantai tinggi'), 'Floor 15 or higher.'),
         _term('floor', 'Lantai 20', ('Lt 20', 'lantai 31 sky garden'), 'The floor the listed unit is on.'),
     ]},
    {'key': 'condition', 'label': 'Furnishing and building condition',
     'note': 'Read on both sides. A proven opposite is rejected (kosongan vs full furnish, an old house to rebuild vs a '
             'renovated / move-in-ready house); a listing that does not say needs a check (Warm).',
     'terms': [
         _term('unfurnished', 'Kosongan', ('unfurnish', 'unfurnished', 'tanpa furniture', 'tanpa perabot', 'non furnish'),
               'Empty unit, no furniture.'),
         _term('semi_furnished', 'Semi furnish', ('semi furnished', 'semi kosongan'),
               'Partly furnished; Warm against full furnish or kosongan.'),
         _term('full_furnished', 'Full furnish', ('furnished', 'fully furnished', 'bonus furniture', 'siap huni lengkap'),
               'Fully furnished. AC or kitchen set alone on a listing counts as some furniture.'),
         _term('furnish_any', 'Boleh furnish/semi/kosong', ('furnish bebas',), 'Any furnishing is fine; not a requirement.'),
         _term('teardown', 'Hitung tanah', ('rumah hancur', 'rumah rusak', 'rumah tua', 'rumah lama', 'bangunan lama',
                                            'dibangun ulang', 'bisa dibongkar'),
               'Old house priced on land value, to rebuild. Never matches a new or renovated house.'),
         _term('new_house', 'Rumah baru', ('gress', 'brand new', 'tidak perlu renovasi'), 'New house; never matches an old house to rebuild.'),
         _term('renovated', 'Siap huni', ('baru renovasi', 'full renov', 'minimalis modern'),
               'Move-in ready or recently renovated.'),
         _term('needs_renovation', 'Perlu renovasi', ('butuh renovasi',), 'Needs renovation; counts as an old building.'),
     ]},
    {'key': 'other', 'label': 'Other abbreviations',
     'note': 'Recognised as descriptions, never as places or extra requirements.',
     'terms': [
         _term('kt', 'KT', ('kamar tidur',), 'Bedrooms (3+1 KT = three bedrooms plus a helper\'s room).'),
         _term('km', 'KM', ('kamar mandi',), 'Bathrooms.'),
         _term('lt', 'LT', ('luas tanah',), 'Land area in m².'),
         _term('lb', 'LB', ('luas bangunan',), 'Building area in m².'),
         _term('hook', 'Hook', ('pojok',), 'Corner plot.'),
         _term('row', 'Row jalan', ('row',), 'Width of the road in front, in metres or cars.'),
         _term('nett', 'Nett', ('net',), 'Net price for the seller, fees and taxes not included; usually fixed.'),
         _term('bu', 'BU', ('butuh uang',), 'The owner needs money soon, so the price is usually sharp.'),
         _term('nego', 'Nego', ('negotiable',), 'Price is negotiable.'),
         _term('hadap', 'Hadap', ('hadap selatan', 'hadap utara'), 'Direction the property faces.'),
         _term('carport', 'Carport', (), 'Covered parking space in front of the house.'),
         _term('permit', 'PBG / IMB', (), 'Building permit (PBG replaced the older IMB).'),
         _term('ipl', 'IPL', (), 'Monthly estate or building service charge.'),
         _term('view_free', 'View bebas', ('view apa saja', 'bebas view'), 'Any view is fine; not a requirement.'),
     ]},
)


def _parser_terms(display, labels_name, shown_by=None):
    """The display entries of the keys the parser reads (in display order), then any key it reads without
    display text under its own label. Older parsers without the constant get the whole display list."""
    labels = getattr(parser, labels_name, None)
    if not isinstance(labels, dict) or not labels:
        return [dict(term) for term in display.values()]
    read = {(shown_by or {}).get(key, key): label for key, label in labels.items()}
    return ([dict(term) for key, term in display.items() if key in read] +
            [_term(key, str(label), (), 'Recognised in requests and listings.') for key, label in read.items() if key not in display])


def property_term_groups():
    groups = [
        {'key': 'legal', 'label': 'Legal status',
         'note': 'A buyer who asks for a title only gets listings with that title, and never one with a title '
                 'the buyer refuses; a listing that does not state its title is Custom.',
         'terms': _parser_terms(LEGAL_TERMS, 'LEGAL_LABELS')},
        {'key': 'unit_type', 'label': 'Apartment unit types',
         'note': 'A buyer who asks for one unit type is never matched with another (studio is not 2BR); a listing '
                 'without a unit type is Custom. Houses use the bedroom count (KT).',
         'terms': _parser_terms(UNIT_TERMS, 'UNIT_TYPE_LABELS', UNIT_TERMS_SHOWN_BY)},
        *({**group, 'terms': [dict(term) for term in group['terms']]} for group in STATIC_GROUPS),
    ]
    for group in groups:
        group['terms'] = [{**term, 'aliases': list(term['aliases'])} for term in group['terms']]
    return groups


@router.get('/terms')
def property_terms():
    """Built-in property terms the matcher understands (read-only, the same for every company)."""
    return {'groups': property_term_groups()}


# ---------------------------------------------------------------- saving
class EntryInput(BaseModel):
    term: str | None = Field(default=None, max_length=1000)
    alias: str | None = Field(default=None, max_length=1000)  # legacy column name
    canonical: str | None = Field(default=None, max_length=1000)
    kind: str | None = Field(default=None, max_length=100)
    parent: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=10000)


class GlossaryInput(BaseModel):
    entries: list[EntryInput] = Field(max_length=20000)


def _norm(value):
    return ' '.join(str(value or '').split()).lower()


def _category_key(value):
    by_label = {label.casefold(): name for name, label in CATEGORY_LABELS.items()}
    return by_label.get(value) or value.replace(' ', '_')


def validate(items):
    """Clean and check entries; raises HTTPException(400) with a message for people."""
    if len(items) > MAX_ENTRIES:
        raise HTTPException(400, f'Add at most {MAX_ENTRIES:,} glossary entries.')
    category_keys = set(parser.CATEGORY_PATTERNS)
    out, seen = [], set()
    for row, item in enumerate(items, 1):
        term = _norm(item.term if item.term is not None else item.alias)
        if not term or len(term) > MAX_TEXT:
            raise HTTPException(400, f'Row {row}: the term needs 1–{MAX_TEXT} characters.')
        kind = _norm(item.kind) or 'synonym'
        if kind not in KINDS:
            raise HTTPException(400, f'"{term}": the type must be synonym, location or category.')
        canonical = _norm(item.canonical)
        if kind == 'category':
            canonical = _category_key(canonical)
            if canonical not in category_keys:
                names = ', '.join(c['label'] for c in categories())
                raise HTTPException(400, f'"{term}": choose one of these categories: {names}.')
        elif not canonical or len(canonical) > MAX_TEXT:
            raise HTTPException(400, f'"{term}": the standard name needs 1–{MAX_TEXT} characters.')
        parent = _norm(item.parent) or None
        if parent and len(parent) > MAX_TEXT:
            raise HTTPException(400, f'"{term}": the parent area can have at most {MAX_TEXT} characters.')
        note = str(item.note or '').strip() or None
        if note and len(note) > MAX_NOTE:
            raise HTTPException(400, f'"{term}": the note can have at most {MAX_NOTE} characters.')
        if term in seen:
            raise HTTPException(400, f'Duplicate term "{term}": each term can appear only once.')
        seen.add(term)
        # Only places sit inside a parent area.
        out.append({'term': term, 'canonical': canonical, 'kind': kind, 'parent': parent if kind == 'location' else None,
                    'note': note})
    _check_hierarchy(out)
    return sorted(out, key=lambda e: e['term'])


def _check_hierarchy(entries):
    """Parent areas (company entries over built-in ones) must form a tree."""
    parents, owner = _default_parents(), {}
    located = [e for e in entries if e['kind'] == 'location' and e['parent']]
    for e in located:
        canonical, parent = _place(e['canonical']), _place(e['parent'])
        if not parent or parent in (canonical, _place(e['term'])):
            raise HTTPException(400, f'"{e["term"]}": a place cannot be inside itself.')
        if canonical in owner and parents[canonical] != parent:
            raise HTTPException(400, f'"{e["term"]}": {canonical} is already inside {parents[canonical]} '
                                     f'(entry "{owner[canonical]}"). Use one parent area per place.')
        parents[canonical], owner[canonical] = parent, e['term']
    for e in located:
        chain = [_place(e['canonical'])]
        while chain[-1] in parents and len(chain) < 64:
            following = parents[chain[-1]]
            if following in chain:
                loop = ' → '.join([*chain[chain.index(following):], following])
                raise HTTPException(400, f'"{e["term"]}": the parent areas form a loop ({loop}).')
            chain.append(following)


def _meaning(entries):
    # Notes explain a term to people; they never change parsing.
    return sorted((e['term'], e['canonical'], e['kind'] or 'synonym', e['parent'] or None) for e in entries)


@router.put('/entries')
def save_entries(payload: GlossaryInput):
    entries = validate(payload.entries)
    with connect() as conn:
        _lock(conn)
        before = glossary_entries(conn)
        conn.execute("DELETE FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id')")
        if entries:
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO xm.glossary(alias, canonical, kind, parent, note) VALUES (%s,%s,%s,%s,%s)',
                                [(e['term'], e['canonical'], e['kind'], e['parent'], e['note']) for e in entries])
        changed = _meaning(before) != _meaning(entries)
        if changed:
            mark_reprocess_pending(conn, REASON)
            conn.execute("INSERT INTO xm.audit_events(event_type,entity_type,details) VALUES('glossary_updated','glossary',%s::jsonb)",
                         (json.dumps({'entries': len(entries)}),))
        conn.commit()
        saved = glossary_entries(conn)
        status = reprocess_status(conn)
        conn.commit()
    # Nothing is rebuilt on save (job stays null); `reprocess` drives the "not updated yet" banner.
    return {'entries': saved, 'job': None, 'changed': changed, 'reprocess': status}


# ---------------------------------------------------------------- suggestions
# Words that describe a location rather than name one.
GENERIC_PHRASES = {'strategis', 'sekitarnya', 'dan sekitarnya', 'bebas', 'mana saja', 'pusat kota', 'lokasi strategis',
                   'premium', 'tengah kota', 'bebas banjir', 'pinggir jalan', 'nol jalan', 'jalan raya', 'one gate system',
                   'dimana saja', 'di mana saja', 'mana aja', 'semua area', 'terserah', 'any', 'all area'}
# Words that never appear in a place name: any of them marks a description ("area parkir luas").
NON_PLACE_WORDS = {
    'strategis', 'strategic', 'ramai', 'parkir', 'parkiran', 'luas', 'renov', 'renovasi', 'granit', 'granitile',
    'keramik', 'marmer', 'kamar', 'mandi', 'laundry', 'jemur', 'teras', 'dapur', 'balkon', 'carport', 'garasi',
    'cocok', 'segala', 'kuliner', 'terpisah', 'favorit', 'terbaik', 'termurah', 'terdepan', 'prestisius', 'ciamik',
    'padat', 'penduduk', 'sangat', 'super', 'banyak', 'dll', 'dsk', 'dskt', 'dsb', 'sekitarnya', 'murah', 'nego',
    'harga', 'budget', 'huni', 'furnish', 'furnished', 'unfurnished', 'lantai', 'ceiling', 'pool', 'kolam', 'banjir',
    'nyaman', 'tenang', 'bersih', 'sejuk', 'potensial', 'prospek', 'berkembang', 'pengembangan', 'investasi',
    'menguntungkan', 'berada', 'terletak', 'memadai', 'lengkap', 'fasilitas', 'keamanan', 'cctv', 'satpam',
    'security', 'listrik', 'pdam', 'sertifikat', 'shm', 'hgb', 'imb', 'pbg', 'ppjb', 'kpr', 'cash', 'dp', 'cicil',
    'traffic', 'sqm', 'm2', 'meter', 'mtr', 'jam', 'menit', 'mnt', 'termasuk', 'tinggal', 'ternama', 'pinggir',
    'hadap', 'buat', 'untuk', 'utk', 'bisa', 'boleh', 'sudah', 'belum', 'tidak', 'tdk', 'gak', 'ga', 'mau', 'yang',
    'yg', 'dengan', 'dgn', 'siap', 'kosongan', 'semi', 'full', 'ganti', 'cat', 'private', 'lift', 'view', 'usaha',
    'bisnis', 'segera', 'dijual', 'disewakan', 'jual', 'sewa', 'beli', 'dicari', 'cari', 'request', 'buyer',
    'straregis', 'startegis', 'strategi', 'tertinggi', 'hectare', 'hectares', 'hektar', 'aktivitas', 'keluarga',
    'cepat', 'cuci', 'ukuran', 'ekspedisi', 'favorite', 'gym', 'pribadi', 'terpasang', 'masuk', 'niaga', 'tikungan',
    'tusuk', 'sate', 'penting', 'lebar', 'maps', 'goo', 'gl', 'http', 'https', 'www', 'com', 'santai', 'ideal',
    'apik', 'atas', 'supermarket', 'hypermarket', 'oke', 'mantap', 'istimewa',
}
# Sizes and prices ("9,5 x 18 = 200 jt") are never place names.
_MEASURE = re.compile(r'\d\s*[x×]\s*\d|=|\d\s*(?:jt|juta|m|miliar|milyar|m2|meter|mtr|ha|hektar|lantai)\b')
# Words that describe a kind of place; a phrase made only of these (and numbers) is no place name.
GENERIC_WORDS = {
    'area', 'lokasi', 'daerah', 'kawasan', 'wilayah', 'lingkungan', 'di', 'ke', 'dan', 'atau', 'the', 'of', 'in', 'at',
    'dekat', 'dkt', 'jalan', 'jl', 'jln', 'raya', 'kota', 'pusat', 'tengah', 'utama', 'main', 'road', 'mainroad',
    'row', 'hook', 'pojok', 'nol', 'depan', 'belakang', 'samping', 'komersial', 'komersil', 'commercial', 'industri',
    'industrial', 'pergudangan', 'perkantoran', 'pertokoan', 'perdagangan', 'kantor', 'office', 'toko', 'resto',
    'restaurant', 'restoran', 'cafe', 'kafe', 'klinik', 'bank', 'bengkel', 'showroom', 'workshop', 'gudang',
    'warehouse', 'pabrik', 'factory', 'fnb', 'rumah', 'ruko', 'tanah', 'lahan', 'kavling', 'kav', 'unit', 'cluster',
    'perumahan', 'perum', 'komplek', 'kompleks', 'sekolah', 'kampus', 'mall', 'pasar', 'rs', 'tol', 'toll', 'exit',
    'pintu', 'gerbang', 'kembar', 'double', 'way', 'service', 'servis', 'taman', 'premium', 'elite', 'elit',
    'exclusive', 'eksklusif', 'mewah', 'bagus', 'baru', 'new', 'aman', 'asri', 'hijau', 'one', 'gate', 'system',
    'all', 'any', 'semua', 'mana', 'saja', 'aja', 'bebas', 'terserah', 'dimana', 'only', 'hanya', 'khusus', 'wajib',
    'harus', 'location', 'near', 'nearby', 'city', 'center', 'centre', 'central', 'barat', 'timur', 'utara',
    'selatan', 'sby', 'lt', 'lb', 'kt', 'km', 'm', 'rp', 'jt', 'juta', 'ada', 'akses', 'mudah', 'sekitar', 'ini',
    'itu', 'nya', 'prime', 'dalam', 'luar', 'golf', 'kost', 'kos', 'an', 'minimarket', 'indomaret', 'alfamart',
    'superindo', 'transmart', 'orang', 'zone',
}
# A phrase that starts like this describes what the property is near, not where it is.
PROXIMITY_WORDS = {'dekat', 'dkt', 'near', 'akses', 'sebelah', 'samping', 'seberang', 'depan', 'belakang', 'menuju',
                   'arah', 'sekitar', 'nearby', 'close'}
_URL = re.compile(r'https?://\S+|wa\.me/\S+|www\.\S+', re.I)
_PHONES = (re.compile(getattr(parser, 'PHONE_PATTERN', r'(?:\+?62|0)8[\d .-]{6,15}\d')),
           re.compile(r'(?<![\w+])(?:\+\s*)?(?:62|0)[\s.-]*8(?:[\s.()-]*\d){7,13}(?!\d)'),
           re.compile(r'(?<!\d)8\d{8,12}(?!\d)'))
SAMPLE_LENGTH = 160


def _without_phones(text):
    text = _URL.sub('', text)
    for pattern in _PHONES:
        text = pattern.sub('', text)
    return text


def junk(phrase):
    value = key(phrase)
    tokens = value.split()
    return (len(value) < 3 or len(re.sub(r'[^a-z]', '', value)) < 3 or value.replace(' ', '').isdigit()
            or value in GENERIC_PHRASES or len(tokens) > 6 or _without_phones(phrase) != phrase
            or re.search(r'[!?±]', phrase) or phrase.count('(') != phrase.count(')') or _MEASURE.search(phrase.lower())
            or tokens[0] in PROXIMITY_WORDS or any(t in NON_PLACE_WORDS for t in tokens)
            or all(t in GENERIC_WORDS or t.isdigit() for t in tokens))


class Recognizer:
    """Is a phrase already known as a place (built-in, glossary or location index)?"""

    def __init__(self, entries, index_data=None):
        self.entries = entries or None
        self.index = LocationIndex(index_data, glossary=entries)
        names = {*getattr(parser, 'KNOWN_LOCATIONS', ()), *_default_parents(), *_default_parents().values()}
        for term, canonical in _default_glossary().items():
            names |= {term, canonical}
        for e in entries:
            # A category word ('kost') is known too, but its canonical ('house') is no place name.
            names |= {e['term'], e.get('parent') or ''} | ({e['canonical']} if e['kind'] != 'category' else set())
        for cluster in (index_data or {}).get('clusters', []):
            names |= {cluster['name'], cluster.get('area') or '', *cluster.get('aliases', [])}
        self.keys = {key(name) for name in names if name} - {''}
        terms = sorted(self.keys, key=len, reverse=True)
        self.pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(t) for t in terms) + r')(?!\w)') if terms else None

    def __call__(self, phrase):
        rewritten = parser.apply_glossary(phrase, self.entries).lower()
        for value in {key(phrase), key(rewritten)}:
            if not value or value in self.keys or (self.pattern and self.pattern.search(value)):
                return True
        return bool(self.index.places.recognize(rewritten) or self.index.resolve(rewritten))


def excerpt(text, phrase):
    """A short single-line excerpt around ``phrase`` without phone numbers or links."""
    lines = [' '.join(line.split()) for line in _without_phones(re.sub(r'[*~]', '', text or '')).splitlines()]
    edge = ' -·/|,;:'  # separators left behind by a removed phone number
    value = ' · '.join(line.strip(edge) for line in lines if line.strip(edge))
    if len(value) <= SAMPLE_LENGTH:
        return value
    position = value.lower().find(phrase.lower())
    start = max(0, position - 50) if position >= 0 else 0
    end = min(len(value), start + SAMPLE_LENGTH)
    start = max(0, end - SAMPLE_LENGTH)
    prefix, suffix = ('…' if start > 0 else ''), ('…' if end < len(value) else '')
    body = value[start + len(prefix):end - len(suffix)].strip()
    return prefix + body + suffix


@router.get('/suggestions')
def suggestions(limit: int = 50):
    """Frequent place phrases in active documents that nothing recognises yet."""
    limit = max(1, min(int(limit), 200))
    with connect() as conn:
        entries = glossary_entries(conn)
        row = conn.execute("SELECT data FROM xm.location_indexes WHERE company_id=current_setting('xm.workspace_id')").fetchone()
        # Count distinct contents: one listing reposted in five groups is one mention (v4.2: a part of a
        # multi-context bubble is its own text; v4.10: the content key ignores signature and case).
        phrases = conn.execute(f"""SELECT x.phrase, count(DISTINCT {GROUP_KEY}) AS count
            FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
            CROSS JOIN LATERAL (SELECT DISTINCT lower(btrim(l)) AS phrase
                                FROM unnest(d.locations || coalesce(d.strict_locations, '{{}}'::text[])) l) x
            WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND length(x.phrase) >= 3
            GROUP BY x.phrase ORDER BY count DESC, x.phrase LIMIT 20000""").fetchall()
        known = Recognizer(entries, row['data'] if row else None)
        picked = []
        for item in phrases:
            if not junk(item['phrase']) and not known(item['phrase']):
                picked.append(item)
                if len(picked) >= limit:
                    break
        samples = {}
        if picked:
            for sample in conn.execute("""SELECT DISTINCT ON (x.phrase) x.phrase, coalesce(d.segment_text, r.raw_text) AS raw_text
                FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
                CROSS JOIN LATERAL (SELECT lower(btrim(l)) AS phrase
                                    FROM unnest(d.locations || coalesce(d.strict_locations, '{}'::text[])) l) x
                WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND x.phrase = ANY(%s)
                ORDER BY x.phrase, r.sent_at DESC NULLS LAST, r.id""", ([p['phrase'] for p in picked],)).fetchall():
                samples[sample['phrase']] = excerpt(sample['raw_text'], sample['phrase'])
    return {'items': [{'phrase': p['phrase'], 'count': p['count'], 'kind_hint': 'location',
                       'sample': samples.get(p['phrase'], '')} for p in picked]}
