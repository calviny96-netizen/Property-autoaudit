"""Eligibility before ranking. Unknown evidence can never certify a HOT match.

Statuses (v4.8): hard attributes are purpose, unit category (incl. an apartment's unit type), area, legal
title and tower/block; price, land and building area are size/price attributes.
- reject (None): a hard attribute or a refused feature proven different, or price/size outside the Warm band;
- custom: anything unclear — a "Needs check" item about missing or unverifiable information (a hard requirement not
  stated in the listing, a location not confirmed at the buyer's level of detail, an explicit buyer requirement the
  listing does not show, an unknown building condition or furnishing, no buyer budget, a vague request) or a
  nearby-cluster alternative; score capped at 79;
- warm: everything proven; the only deviations are price and/or land/building size inside the Warm allowance (or a
  score below HOT_MIN_SCORE); score capped at 79;
- hot: every hard attribute and every explicit buyer requirement proven, no "Needs check" item, score ≥ HOT_MIN_SCORE (90).

Explanation order: the purpose and unit lines, positive reasons, then the Custom items (the strict-location line
"Location not confirmed — buyer only accepts …" first), then the other unclear "Needs check: …" items, then the Warm
deviations, then summary lines ("Asset category matches: …", "Location …%", "Land area: …%", "Building area: …%",
"Price: …%" or "…: not requested (no score)"). Summary lines are internal scoring detail; the UI hides them.
"""
import re
from functools import lru_cache
from parser import (apply_glossary, strip_landmarks, _number, _price_candidates, _range_from_line, _split_negation, location_score,
                    _PHRASE_DROP, _PHRASE_STOP, _APARTMENT_COMPLEX, _APARTMENT_RE, known_buildings,
                    split_contact, strip_top_contact, clean_text, CATEGORY_LABELS, LEGAL_LABELS, UNIT_TYPE_LABELS, _BANKABLE, _FREE_VIEW,
                    financing_need, financing_offer, floor_level, floor_requirement, legal_requirement, legal_statuses,
                    tower_names, unit_types, _PREFERENCE, lease_need, lease_offer, city_centre, facing_directions,
                    per_m2_terms, CITY_STREETS, HOOK, HOOK_WORDS)
from location_index import empty_index
from location_data import SURABAYA_KECAMATAN
from locations import GENERIC_PLACE_WORDS, ROOT_PLACES, SURABAYA_REGIONS, default_places, expand_place_suffixes

NEEDS = 'Needs check: '
PERIODS = ('per_year', 'per_month')


def _strip_landmarks(text):
    return '\n'.join(strip_landmarks(line) for line in text.split('\n'))
# Parser requirement keywords (Indonesian) as shown to agents.
REQUIREMENT_LABELS = {
    'siap huni': 'move-in ready', 'siap pakai': 'ready to use', 'akses kontainer': 'container access',
    'jalan raya': 'main road location', 'hook': 'corner lot', 'shm': 'SHM certificate', 'hgb': 'HGB certificate',
    'non lsd': 'non-LSD land', 'dekat tol': 'near a toll road', 'parkiran luas': 'ample parking',
    'full furnish': 'fully furnished', 'minimalis modern': 'modern minimalist style',
}


def _range(row, prefix):
    return row.get(prefix + '_min'), row.get(prefix + '_max')


_CONDITION = re.compile(r'\b(?:jika|jk|kalau|kalo|klo|kl|bila|apabila|if)[ \t]*$|\blebar\b[^\n\d]{0,12}$')
# "2 lantai atau 1 lantai (tapi lega)": either storey count.
_OTHER_STOREYS = re.compile(r'[ \t]*(?:atau|or|/)[ \t]*(\d+(?:[.,]\d+)?)[ \t]*(?:lantai|lt|storeys?|floors?)\b')


@lru_cache(maxsize=32768)
def _count(text, unit):
    number = r'(?<![\d.,])(\d+(?:[.,]\d+)?)(?:\s*[-–/]\s*(\d+(?:[.,]\d+)?))?'
    if unit == 'bedrooms':
        # "KT 3", "kt.3", "min 3 kt", "3 kmr tidur", "minim 5 kamar", "2 atau 3 kt", "max. 3-4 kamar" (never "kamar mandi",
        # "kamar pembantu").
        word = r'(?:\bkt\b|kamar(?:\s*tidur)?(?!\s*(?:mandi|pembantu|art\b))|\bkmr(?:\s*(?:tidur|tdr))?(?!\s*mandi)|bedrooms?|\bbr\b)'
        qualifier = r'(?:(?:min(?:imal|im|imum)?|max|maks(?:imal)?|lebih dari|di atas|diatas|kurang dari)\.?\s*)?'
        count = r'(?<!\d)(?<!\d[.,])(?<![a-z²])([1-9])(?:[ \t]*(?:-|–|/|atau|s/?d|sampai)[ \t]*([1-9]))?(?!\d|[.,]\d)'
        glued = word.replace(r'\b', '', 1).replace(r'|\bkmr', '|kmr').replace(r'|\bbr\b', r'|br\b')  # "3kt", "2br"
        pattern = word + r'[ \t]*' + qualifier + r'[:=.]?[ \t]*' + count + '|' + count + r'[ \t]*(?:\+[ \t]*\d[ \t]*)?' + glued
        # "Lantai 1 ada 2 kamar", "KT di bawah": the rooms of one floor are not the bedroom count.
        floor_rooms = re.compile(r'(?:lantai|lt)[ \t]*\d+[ \t]*(?:ada[ \t]*)?$|\b(?:bawah|atas)[ \t]*(?:ada[ \t]*)?$')
        m = next((x for x in re.finditer(pattern, text) if not floor_rooms.search(text[max(0, x.start() - 15):x.start()])), None)
        if not m:
            return None
        values = [_number(v) for v in m.groups() if v is not None]
    else:
        # "lantai 20" in apartments describes unit elevation, not storey count. v4.6: "1 atau 2 lantai" is either.
        number = number.replace(r'[-–/]\s*', r'(?:[-–/]|atau|or)\s*')
        candidates = re.finditer(number + r'(?:[ \t]*lt|\s*(?:lantai|storeys?|floors?))\b', text)
        # LT at the start of the next line means land area, not floors; "Lebar 8 jika 1 Lantai", "Lebar 6 - 2 lantai" (a width
        # per storey count) are conditions, no count.
        m = next((v for v in candidates if (not v[0].endswith('lt') or max(_number(n) for n in v.groups() if n is not None) <= 20)
                  and not _CONDITION.search(text, max(0, v.start() - 25), v.start())), None)
        if not m:
            # v4.5: "Tingkat: 1", "2 tingkat", "Jumlah lantai: 2" state the storeys too.
            m = re.search(r'(?:(?<!bed )(?<!ranjang )(?<!kasur )\btingkat|\bj(?:umla|ml)h?[ \t]+lantai)[ \t]*[:=]?[ \t]*'
                          r'([1-9](?:[.,]5)?)(?![\d.,%])|(?<![\d.,])([1-9](?:[.,]5)?)[ \t]*tingkat\b(?![ \t]*(?:bed|ranjang|kasur))', text)
        if not m:
            return None
        values = [_number(v) for v in m.groups() if v is not None]
        other = _OTHER_STOREYS.match(text, m.end())
        if other:
            values.append(_number(other.group(1)))
    qualifier = text[max(0, m.start() - 15):m.end()]
    if re.search(r'\b(?:lebih dari|di atas|diatas)\b', qualifier):
        return min(values) + 0.01, float('inf')
    if re.search(r'\bkurang dari\b', qualifier):
        return 0, max(values) - 0.01
    if re.search(r'\bmin(?:imal|im)?\b', qualifier):
        return min(values), float('inf')
    if re.search(r'\b(?:max|maks(?:imal)?)\b', qualifier):
        return 0, max(values)
    return min(values), max(values)


def _rule_text(row):
    """Lower-case message text the rules read: the part's own text for a split bubble (segment_text),
    else the whole bubble, without the signature."""
    if '_rule_text' not in row:
        source = row.get('segment_text') or row.get('raw_text') or row.get('normalized_text') or ''
        # v4.8: "Manyar2an" / "manyar-an" reads as "manyar dsk" (the place and its surroundings).
        row['_rule_text'] = expand_place_suffixes(re.sub(r'[*_~]', '', split_contact(strip_top_contact(clean_text(source)))[0]).lower(),
                                                  default_places())
    return row['_rule_text']


# v4.3 Warm allowances beyond the Hot band: (setting, legacy v4.1 setting, default). The v4.2 meaning of
# price_down_pct / price_floor_pct (a 50 % floor under the budget) is gone: the Hot floor is hot_price_floor_pct.
_TOLERANCES = {'price': (('price_down_pct', None, 15.0), ('price_up_pct', 'price_tolerance_pct', 15.0)),
               'land': (('land_down_pct', 'land_tolerance_pct', 15.0), ('land_up_pct', 'land_tolerance_pct', 15.0)),
               'building': (('building_down_pct', 'building_tolerance_pct', 15.0), ('building_up_pct', 'building_tolerance_pct', 15.0))}


def _setting(settings, name):
    value = settings.get(name) if hasattr(settings, 'get') and name else None
    return float(value) if value is not None else None


def tolerances(settings, factor):
    """(below %, above %) Warm allowance beyond the Hot band for 'price' | 'land' | 'building'."""
    out = []
    for name, legacy, default in _TOLERANCES[factor]:
        value = _setting(settings, name)
        if value is None:
            value = _setting(settings, legacy)
        out.append(min(100.0, max(0.0, value if value is not None else default)))
    return tuple(out)


def hot_price_floor(settings):
    """Hot price band starts at budget × this % (default 80) when the buyer gives a single budget or a maximum."""
    value = _setting(settings, 'hot_price_floor_pct')
    return min(100.0, max(0.0, value if value is not None else 80.0))


def location_radius(settings):
    """Nearby-cluster radius in km (0–4; the location table only holds pairs within 4 km)."""
    value = _setting(settings, 'location_radius_km')
    return min(4.0, max(0.0, value if value is not None else 4.0))


def hot_band(need, price=False, hot_floor=80.0, hard_min=None):
    """(lo, hi) of the Hot band for a request [a, b]; None = open. Price with a single budget or a maximum B:
    [B × hot_floor %, B]; a range [a, b] (also "-an"): [a, b]; a minimum only: [a, ∞). Areas: [a, b] (a single
    value a = b; "LB max 300": (−∞, 300]). A hard minimum ("paling min 2300 ga mau dibawah itu") raises the floor."""
    a, b = (float(v) if v is not None else None for v in need)
    if a is not None and b is not None and a > b:
        b = a  # a malformed range ("LB 220 - 2 lantai"): the stated (first) value counts
    if price and b is not None and (a is None or a >= b):
        lo = b * hot_floor / 100
    else:
        lo = a
    if hard_min is not None:
        lo = max(lo, float(hard_min)) if lo is not None else float(hard_min)
    return lo, b


def band(need, have, down, up, price=False, hot_floor=80.0, hard_min=None):
    """(score, side) of a listing value against the request (R7). side: 'inside' = the Hot band, 'below' /
    'above' = the Warm band (Hot band widened by ``down`` % below its floor and ``up`` % above its ceiling; a hard
    minimum is never loosened), 'missing' = not stated; score 0 = outside the Warm band (reject).
    Score: 100 in the Hot band; towards the Warm floor 100 → 70 (price) / 60 (areas); towards the ceiling 100 → 60."""
    if need[0] is None and need[1] is None:
        return 100.0, 'inside'
    lo, hi = (float(v) if v is not None else None for v in have)
    if lo is None and hi is None:
        return 35.0, 'missing'
    lo, hi = (lo if lo is not None else hi), (hi if hi is not None else lo)
    if lo > hi:
        hi = lo  # never widen a malformed listing range to [2, 220]
    hot_lo, hot_hi = hot_band(need, price, hot_floor, hard_min)
    warm_lo = hot_lo * (100 - down) / 100 if hot_lo is not None else None
    if warm_lo is not None and hard_min is not None:
        warm_lo = max(warm_lo, float(hard_min))
    warm_hi = hot_hi * (100 + up) / 100 if hot_hi is not None else None
    # The listing value closest to the Hot band (a listing range may overlap it).
    if hot_lo is not None and hi < hot_lo:
        value = hi
    elif hot_hi is not None and lo > hot_hi:
        value = lo
    else:
        return 100.0, 'inside'
    slack = 1e-9 * max(abs(value), 1.0)
    if hot_hi is not None and value > hot_hi:
        if value > warm_hi + slack:
            return 0.0, 'above'
        span = warm_hi - hot_hi
        return (max(60.0, 100 - 40 * (value - hot_hi) / span) if span > 0 else 100.0), 'above'
    if value < warm_lo - slack:
        return 0.0, 'below'
    floor = 70.0 if price else 60.0
    span = hot_lo - warm_lo
    return (max(floor, min(100.0, floor + (100 - floor) * (value - warm_lo) / span)) if span > 0 else 100.0), 'below'


def _wanted_text(text):
    """Request text without what the buyer refuses ("tidak mau X", "X jangan ditawarkan", "jgn sampai X")."""
    return '\n'.join(_split_negation(line)[0] for line in text.split('\n'))


# "X yg diprioritaskan": the wish stands before the word; "prefer X", "diutamakan X", "kalau bisa X": after it.
_WISH_BEFORE = re.compile(r'(?:.*[,;:(/])?\s*([^,;:(/\n]*?)\s*\b(?:yg|yang)\s*$')
# "Blok GE1 no 22 & 23 sudah dapat": a block the buyer already bought is no requirement.
_DONE_LINE = re.compile(r'(?m)^.*\b(?:sudah|sdh|udah)\s+(?:dapat|dpt|laku|terjual|sold|closing|deal)\b.*$|^.*\b(?:sold|terjual)\b.*$')


def _preference_split(text):
    """(required text, preferred text) of a request: a wish ("Surabaya Barat, prefer Wiyung") is no requirement."""
    required, preferred = [], []
    for line in text.split('\n'):
        m = _PREFERENCE.search(line)
        if not m:
            required.append(line)
            continue
        before = _WISH_BEFORE.match(line[:m.start()])
        if before:
            required.append(line[:before.start(1)] + line[m.end():])
            preferred.append(line[before.start(1):m.end()])
        else:
            required.append(line[:m.start()])
            preferred.append(line[m.start():])
    return '\n'.join(required), '\n'.join(preferred)


def _required_locations(request, places):
    """The places a buyer requires: places named only as a wish ("prefer area Wiyung") are left out while
    another known place stays (the wish is checked softly)."""
    cached = request.get('_required_places')
    if cached is not None and cached[0] is places:
        return cached[1]
    locations = [p for p in request.get('locations') or [] if p]
    out = locations
    text = _rule_text(request)
    request['_preferred_places'] = []
    if _PREFERENCE.search(text):
        required_text, preferred_text = _preference_split(text)
        wished = [p for p in locations if p in preferred_text and p not in required_text]
        kept = [p for p in locations if p not in wished]
        # v4.8: "Area prefer CITRALAND / Graha - Pakuwon boleh ditawarkan": the other places are also accepted, so the
        # preferred one stays accepted too (and is checked as a preference).
        if wished and any(places.canonical(p) in places.known for p in kept) and not _ALSO_OK.search(text):
            out = kept
        request['_preferred_places'] = [p for p in wished if places.canonical(p) in places.known]
    # v4.8: "Rumah lokasi Manyar (dkt sklh Petra)": a landmark the buyer wants to be near is no area of its own while
    # another place is named.
    near_text = ' '.join(m.group() for m in _NEAR_LANDMARK.finditer(text))
    if near_text:
        rest = _NEAR_LANDMARK.sub(' ', text)
        landmarks = [p for p in out if p in near_text and p not in rest]
        if landmarks and any(places.canonical(p) in places.known for p in out if p not in landmarks):
            out = [p for p in out if p not in landmarks]
    qualifiers = request['_qualified_areas'] = _qualifying_areas(text, places, out)
    if qualifiers:
        out = [p for p in out if places.canonical(p) not in qualifiers]
    request['_required_places'] = (places, out)
    return out


_QUALIFIER_GAP = re.compile(r'[ \t,(\-–]*')
_NEAR_LANDMARK = re.compile(r'\b(?:dekat|dkt|deket|near|sebelah|samping|seberang)\b[^\n,;)]*')
_ALSO_OK = re.compile(r'\b(?:boleh|bisa|bs|juga|jg|ok|oke|masuk)\b[^\n]{0,15}?\b(?:di)?tawar\w*|\b(?:juga|jg)[ \t]+(?:boleh|bisa|ok|oke|masuk)\b'
                      r'|\b(?:boleh|bisa)[ \t]+(?:juga|jg)\b')
_AROUND_ANY = re.compile(r'\b(?:sekitar\w*|seputar\w*|dsk\w*|dkt|dekat|deket|near)\b')


def _qualifying_areas(text, places, locations):
    """v4.6: "Beli rumah villa bukit mas, Surabaya barat", "Citraland Surabaya": a region or city written right after a
    place inside it only says where that place is; it is no broader area the buyer accepts besides the place (unless the
    buyer also accepts places around it). Returns those regions/cities (canonical) when every mention is such a qualifier."""
    wanted = {places.canonical(p) for p in locations}
    # v4.8: a kecamatan right after a development says where it is too ("Greenlake Citraland Wiyung").
    broad = {p for p in wanted if p in SURABAYA_REGIONS or p in ROOT_PLACES or p in SURABAYA_KECAMATAN}
    source = apply_glossary(_wanted_text(text))
    if not broad or len(wanted) < 2 or _AROUND_ANY.search(source):
        return set()  # "Lokasi sekitar ITS dan sekitarnya": nearby places in the region are wanted too
    spans = [(m.start(), m.end(), places.canonical(m.group())) for m in places.pattern.finditer(source)]
    qualifying, free = set(), set()
    for i, (start, end, place) in enumerate(spans):
        if place not in broad:
            continue
        before = spans[i - 1] if i else None
        qualifies = before and before[2] in wanted and place in places.ancestors(before[2]) and \
            _QUALIFIER_GAP.fullmatch(source, before[1], start) is not None
        if before and not qualifies and place in SURABAYA_KECAMATAN and before[2] in wanted and before[2] not in broad \
                and places.region_of(before[2]) == places.region_of(place) and re.fullmatch(r'[ \t]+', source[before[1]:start]):
            qualifies = True
        if qualifies and ',' in source[before[1]:start]:
            # "mojokerto, Krian, sidoarjo, gresik": after a comma, an area inside a list of places is one more item.
            line_start, line_end = source.rfind('\n', 0, start) + 1, (source.find('\n', end) + 1 or len(source) + 1) - 1
            qualifies = not any(s[0] >= line_start and s[1] <= line_end and s is not before and s[0] != start for s in spans)
        (qualifying if qualifies else free).add(place)
    return qualifying - free


def _evidence_places(row, places):
    """Listing places used as location evidence: the title's places win over places named later
    unless the later place refines a title place (e.g. title "Surabaya Barat", body "Citraland")."""
    locations = [p for p in (row.get('locations') or []) if p]
    text = row.get('_place_text', '')
    lines = [line for line in text.splitlines() if line.strip()]
    title_places = places.recognize('\n'.join(lines[:3])) if len(lines) > 3 else []
    # v4.5: a shared street name the parser placed in another town ("Jl. Panglima Sudirman, Madiun") is no evidence.
    title_places = [p for p in title_places if p not in CITY_STREETS or p in locations]
    if not title_places:
        return locations
    # v4.8: next to a specific title place ("Kertajaya Indah - Surabaya") only places inside it refine it; the city or region
    # alone would let every later place through ("… sekolah NSA, Petra manyar" in a list of what is nearby).
    anchors = [t for t in title_places if t not in ROOT_PLACES and t not in SURABAYA_REGIONS] or title_places
    keep = []
    for p in locations:
        if p in title_places or p not in places.known or any(t in places.ancestors(p) for t in anchors):
            keep.append(p)
    return list(dict.fromkeys(title_places + keep))


def prepare_document(row, location_index=None):
    text = _rule_text(row)
    index = location_index or empty_index()
    doc_type = row.get('document_type') or row.get('classification')
    if doc_type == 'property_listing':
        row['_place_text'] = _strip_landmarks(text)
        row['_evidence'] = _evidence_places(row, index.places)
    if location_index:
        # A listing "dekat ITS, UNAIR, dan Pakuwon City" is not IN those places; a request without its refusals
        # (and without the places it only wishes for when it requires another one).
        if doc_type == 'buyer_request':
            required = _required_locations(row, index.places)
            source = _wanted_text(_preference_split(text)[0] if required != (row.get('locations') or []) else text)
            # v4.8: places the parser named in other words ("Graha - Pakuwon boleh ditawarkan" → Graha Famili, Pakuwon Indah).
            source += ''.join('\n' + p for p in required if p and p not in source)
        else:
            source = row.get('_place_text', text)
        row['_geo_ids'] = location_index.resolve(source, listing=doc_type == 'property_listing')
        if doc_type == 'property_listing' and row['_geo_ids']:
            row['_geo_ids'] = _named_development_ids(row['_geo_ids'], row, location_index)
    return row


def _named_development_ids(ids, row, index):
    """v4.6: "Citra Harmoni / Cluster Stamford": a cluster (alias) of a development the listing does not name, in another
    region than a development it names, is dropped (as the parser drops the place, see development_overrides)."""
    places = index.places
    named = {places.canonical(p) for p in row.get('locations') or [] if p}
    regions = {places.region_of(d) for d in named if places.development(d) and places.development_of(d) is None} - {None}
    if not regions:
        return ids
    regions |= {places.region_of(p) for p in named}
    keep = []
    for ident in ids:
        area, region = index.area_of.get(ident), index.cluster_region(ident)
        if area and region and region not in regions and not named & set(index.area_chain(area)):
            continue
        keep.append(ident)
    return keep


_MONEY_UNITS = ((1e12, 'T'), (1e9, 'M'), (1e6, 'jt'))


def _money(value):
    """Rupiah the way agents write it: "Rp 1,25 M", "Rp 350 jt", "Rp 750.000"."""
    value = float(value)
    for size, unit in _MONEY_UNITS:
        if value >= size:
            return f"Rp {f'{value / size:.2f}'.rstrip('0').rstrip('.').replace('.', ',')} {unit}"
    return f"Rp {int(round(value)):,}".replace(',', '.')


def _area_range(listing, categories):
    land = _range(listing, 'land_area')
    if land != (None, None):
        return land
    building = _range(listing, 'building_area')
    if building != (None, None) and 'apartment' in categories:
        return building
    return None


def _scaled(price, area, op):
    lo, hi = price
    a_lo, a_hi = area
    a_lo = a_lo if a_lo is not None else a_hi
    a_hi = a_hi if a_hi is not None else a_lo
    if op == 'mul':
        return (float(lo) * float(a_lo) if lo is not None else None, float(hi) * float(a_hi) if hi is not None else None)
    return (float(lo) / float(a_hi) if lo is not None and a_hi else None, float(hi) / float(a_lo) if hi is not None and a_lo else None)


def _price_sides(request, listing, deal):
    """(need, need_basis, have, have_basis) for the transaction being compared."""
    need, need_basis = _range(request, 'price'), request.get('price_basis')
    if request.get('transaction_type') == 'both':
        rent_need = _range(request, 'rent_price')
        if deal == 'rent':
            if rent_need != (None, None):
                need, need_basis = rent_need, request.get('rent_price_basis')
            elif need_basis not in PERIODS:
                need, need_basis = (None, None), None
        elif need_basis in PERIODS:
            need, need_basis = (None, None), None
    have, have_basis = _range(listing, 'price'), listing.get('price_basis')
    if listing.get('transaction_type') == 'both' and deal == 'rent':
        rent_have = _range(listing, 'rent_price')
        if rent_have != (None, None) or have_basis not in PERIODS:
            have, have_basis = rent_have, listing.get('rent_price_basis')
    return need, need_basis, have, have_basis


def _area_text(need):
    a, b = (float(v) if v is not None else None for v in need)
    if a is not None and b is not None:
        return f'{a:g} m²' if a >= b else f'{a:g}–{b:g} m²'
    return f'min {a:g} m²' if a is not None else f'max {b:g} m²'


def _first(pair):
    return pair[0] if pair[0] is not None else pair[1]


def _rent_area(listing, kind):
    """(area range or None, 'land' | 'building') a rent per m² is for: the land when the price says so (or the listing
    is a plot), else the building."""
    land = kind == 'land' or set(listing.get('categories') or []) <= {'land'}
    area = _range(listing, 'land_area' if land else 'building_area')
    return (area if area != (None, None) else None), ('land' if land else 'building')


_PER = {'per_month': 'month', 'per_year': 'year'}


def _rent_per_m2(listing, need, need_basis, have, have_basis, req_text, text, unknown):
    """v4.5: a rent per m² of a period ("Rp58.500/m² BANGUNAN/bulan") × the area it is for is the rent of that period;
    a renter's budget per m² is compared with the listing's rent ÷ that area. Returns (need, need_basis, have,
    have_basis, shown). An unknown area or period leaves the price unproven: a soft "Needs check" (Warm at best)."""
    if need_basis != 'per_m2' and have_basis != 'per_m2':
        return need, need_basis, have, have_basis, None
    if need_basis == have_basis:
        need_period, have_period = per_m2_terms(req_text, _first(need))[0], per_m2_terms(text, _first(have))[0]
        if need_period and have_period:
            return need, need_period, have, have_period, None
        if need_period or have_period:
            # "50rb/m²/bulan" vs "400rb/m²": the periods may differ 12×, so nothing is proven.
            unknown.append(NEEDS + 'rent period not stated')
            return need, need_basis, (None, None), need_basis, None
        return need, need_basis, have, have_basis, None
    if have_basis == 'per_m2':
        period, kind = per_m2_terms(text, _first(have))
        area, word = _rent_area(listing, kind)
        if not period:
            unknown.append(NEEDS + 'rent price basis differs (per m² vs period)')
        elif not area:
            unknown.append(NEEDS + f'rent is per m² but the {word} area is not stated')
        else:
            total = _scaled(have, area, 'mul')
            per = _PER[period]
            return need, need_basis, total, period, \
                f'{_money(_first(have))}/m² per {per} × {_first(area):g} m² ≈ {_money(_first(total))} per {per}'
        return need, need_basis, (None, None), need_basis, None
    period, kind = per_m2_terms(req_text, _first(need))
    area, word = _rent_area(listing, kind)
    if not area:
        unknown.append(NEEDS + f'rent budget is per m² but the {word} area is not stated')
        return need, need_basis, (None, None), need_basis, None
    if not period:
        unknown.append(NEEDS + 'rent period not stated')
        return need, need_basis, (None, None), need_basis, None
    # A rent total without a period counts as yearly, unconfirmed (R7).
    per, have_period = _scaled(have, area, 'div'), have_basis if have_basis in PERIODS else None
    shown = f'{_money(_first(have))} ÷ {_first(area):g} m² ≈ {_money(_first(per))}/m²' + (f' per {_PER[have_period]}' if have_period else '')
    return need, period, per, have_period, shown


def _rent_like(basis, text, value):
    """A yearly/monthly price, also per m² ("Rp 50.000/m²/bulan")."""
    return basis in PERIODS or (basis == 'per_m2' and bool(per_m2_terms(text, value)[0]))


def _assess_price(request, listing, deal, settings, req_text, categories):
    """(value, detail, reasons, unknown, active) or None when the price rules the pair out."""
    need, need_basis, have, have_basis = _price_sides(request, listing, deal)
    reasons, unknown = [], []
    text = _rule_text(listing)
    if need == (None, None):
        if request.get('transaction_type') == 'both' and (_range(request, 'price') != (None, None)
                                                          or _range(request, 'rent_price') != (None, None)):
            # "Cari rumah beli/sewa … Budget 2 M" names one budget only: the other purpose's price is unchecked.
            unknown.append(NEEDS + ('rent budget not stated' if deal == 'rent' else 'purchase budget not stated'))
        if deal == 'sale' and _rent_like(have_basis, text, _first(have)) and listing.get('transaction_type') != 'both':
            # "Dijual … Harga Rp 250 jt/tahun": a sale listing whose only price is a rent price is no purchase for
            # a buyer (R2), with or without a budget; an unconfirmed purpose only needs a check.
            if request.get('transaction_type') == 'sale':
                return None
            unknown.append(NEEDS + 'price looks like a rent price')
        return 0.0, 'Price: not requested (no score)', reasons, unknown, False
    down, up = tolerances(settings, 'price')
    shown = None
    if have != (None, None):
        if deal == 'rent':
            need, need_basis, have, have_basis, shown = _rent_per_m2(listing, need, need_basis, have, have_basis, req_text,
                                                                     text, unknown)

            # R7: rent compared per year; a missing period is assumed yearly but unconfirmed.
            def yearly(value, basis):
                if basis == 'per_month':
                    return tuple(v * 12 if v is not None else None for v in value), True
                if basis == 'per_year':
                    return value, True
                if basis == 'per_m2':
                    return value, None
                return value, False
            need, need_ok = yearly(need, need_basis)
            have, have_ok = yearly(have, have_basis)
            if have == (None, None):
                pass  # unproven (noted above)
            elif need_ok is None or have_ok is None:
                if need_basis != have_basis:
                    unknown.append(NEEDS + 'rent price basis differs (per m² vs period)')
                    have = (None, None)
            elif not (need_ok and have_ok):
                unknown.append(NEEDS + 'rent period not stated')
        elif need_basis and have_basis and (need_basis in PERIODS) != _rent_like(have_basis, text, _first(have)):
            # R2: a total (or per m²) sale budget never matches a yearly/monthly rent price, nor the reverse.
            return None
        elif need_basis == have_basis or not need_basis or not have_basis:
            if not need_basis or not have_basis:
                unknown.append(NEEDS + 'price basis not confirmed')
            if need_basis in PERIODS or have_basis in PERIODS:
                unknown.append(NEEDS + 'price looks like a rent price')
        elif have_basis == 'per_m2' and need_basis == 'total':
            area = _area_range(listing, categories)
            if area:
                per = have
                have = _scaled(have, area, 'mul')
                area_text = f'{area[0] if area[0] is not None else area[1]:g} m²'
                shown = f'{_money(per[0] if per[0] is not None else per[1])}/m² × {area_text} ≈ {_money(have[0] if have[0] is not None else have[1])}'
            else:
                unknown.append(NEEDS + 'price is per m² but the area is not stated')
                have = (None, None)
        elif have_basis == 'total' and need_basis == 'per_m2':
            area = _area_range(listing, categories)
            if area:
                total = have
                have = _scaled(have, area, 'div')
                area_text = f'{area[0] if area[0] is not None else area[1]:g} m²'
                shown = f'{_money(total[0] if total[0] is not None else total[1])} ÷ {area_text} ≈ {_money(have[0] if have[0] is not None else have[1])}/m²'
            else:
                wanted_area = _range(request, 'land_area')
                if wanted_area != (None, None):
                    budget = _scaled(need, wanted_area, 'mul')
                    if budget[0] is None and need[1] is not None and wanted_area[1] is None:
                        budget = (None, None)
                    if budget != (None, None):
                        shown = f'Budget {_money(need[1] if need[1] is not None else need[0])}/m² × requested {wanted_area[0] if wanted_area[0] is not None else wanted_area[1]:g} m² ≈ {_money(budget[1] if budget[1] is not None else budget[0])}'
                        need = budget
                    else:
                        unknown.append(NEEDS + 'budget is per m² but the area is not stated')
                        have = (None, None)
                else:
                    unknown.append(NEEDS + 'budget is per m² but the area is not stated')
                    have = (None, None)
        else:
            unknown.append(NEEDS + 'price basis differs')
            have = (None, None)
    if need[1] is not None and have[0] is not None and float(have[0]) >= float(need[1]) and re.search(r'\bunder\b|di bawah|dibawah', req_text):
        # "Under 3 M" is the buyer's hard limit.
        if listing.get('negotiable'):
            unknown.append(NEEDS + 'price must be below the buyer limit; negotiation needed')
        else:
            return None
    if have[0] is not None and have[1] is not None and 0 < float(have[0]) and float(have[1]) > 1.5 * float(have[0]):
        # A wide listing range ("380 jt – 10,38 M") is no price that fits a budget by overlapping it.
        unknown.append(NEEDS + 'listing price unclear (wide range)')
    # R7: Hot = [budget × hot floor %, budget] (a range: [min, max]); Warm = Hot widened by the allowances. One band
    # for every basis, symmetric for both directions of the pair; "nego" changes nothing.
    floor_pct = hot_price_floor(settings)
    value, side = band(need, have, down, up, price=True, hot_floor=floor_pct)
    if value == 0:
        return None
    rent_word = 'Rent ' if deal == 'rent' else ''
    head = None
    if have != (None, None) and side in ('above', 'below'):
        offered = float(have[0] if side == 'above' and have[0] is not None else have[1] if have[1] is not None else have[0])
        head = shown or f"{rent_word.lower()}price {_money(offered)}"
    if have == (None, None):
        if not any('price' in u or 'rent' in u for u in unknown):
            unknown.append(NEEDS + ('rent price not stated' if deal == 'rent' else 'listing price not stated'))
    elif side == 'above':
        budget = float(need[1])
        percent = max(1, round((offered - budget) / budget * 100)) if budget else 0
        maximum = need[0] is not None and float(need[0]) < budget
        unknown.append(NEEDS + f"{head} is {percent}% above the {'maximum ' if maximum else ''}budget {_money(budget)} (within tolerance)")
    elif side == 'below':
        # Below the Hot floor (80 % of a single budget, or under the minimum of a range) is Warm.
        minimum = need[0] is not None and (need[1] is None or float(need[0]) < float(need[1]))
        budget = float(need[0] if minimum else need[1])
        percent = max(1, round((budget - offered) / budget * 100)) if budget else 0
        unknown.append(NEEDS + f"{head} is {percent}% below the {'minimum ' if minimum else ''}budget {_money(budget)}")
    else:
        reasons.append((shown + ' — within budget') if shown else (rent_word + 'price within budget').capitalize())
    return value, f'Price: {value:.0f}%', reasons, unknown, True


def _deal(request, listing):
    """Transaction compared for this pair, or False when the intents conflict (R1)."""
    want, offer = request.get('transaction_type') or 'unknown', listing.get('transaction_type') or 'unknown'
    if want == 'unknown' or offer == 'unknown':
        known = offer if want == 'unknown' else want
        return (known if known in ('sale', 'rent') else None), True
    if want == 'both':
        return (offer if offer in ('sale', 'rent') else 'both'), False
    if offer in (want, 'both'):
        return want, False
    return False, False


def _regions(row, index, wanted=False):
    """Regions/cities a document names, from parser places and resolved index clusters. ``wanted``: a request
    that names a city besides places outside it ("wilayah Surabaya ke arah Waru") wants every region of that
    city too; next to a place inside it ("Citraland Surabaya") the city only qualifies that place."""
    key = '_wanted_regions' if wanted else '_regions'
    if key not in row:
        names = list(_required_locations(row, index.places) if wanted else row.get('locations') or [])
        for ident in row.get('_geo_ids') or []:
            if ident in index.clusters:
                names += index.cluster_places(ident)
        places = index.places
        regions = {r for r in (places.region_of(p) for p in names) if r}
        if wanted and regions:
            roots = {places.canonical(p) for p in names if places.region_of(p) is None} & ROOT_PLACES
            # "Surabaya ke arah Waru, tidak sampe Margomulyo atau DKP": the region holding every refused place
            # (Surabaya Barat) is not part of the city the buyer wants.
            refused = {places.canonical(_SHORT_PLACES.get(e.strip(' *_').lower(), e.strip(' *_').lower()))
                       for e in row.get('exclusions') or [] if e and e.strip(' *_')}
            refused_regions = {places.region_of(p) for p in refused if p in places.known}
            for root in roots:
                inside = {r for r in SURABAYA_REGIONS if root in places.ancestors(r)}
                if len(refused_regions) == 1 and None not in refused_regions:
                    inside -= refused_regions
                if not regions & inside:
                    regions |= inside
        row[key] = frozenset(regions)
    return row[key]


def _root_city(region):
    """The city a region belongs to ('surabaya barat' -> 'surabaya'; other regions are cities already)."""
    return 'surabaya' if region in SURABAYA_REGIONS else region


def _offered_regions(evidence, places):
    """Regions of the places a listing names. A city written next to an estate name wins over the estate's
    built-in region: "Graha Golf, Malang" is in Malang even though a Graha Golf exists in Surabaya Barat
    (estate names repeat across cities)."""
    names = [places.canonical(p) for p in evidence or ()]
    explicit = {p for p in names if p in ROOT_PLACES}
    regions = {r for r in (places.region_of(p) for p in names) if r}
    # "Raya Graha Golf, Araya Malang": a place whose own name carries its city states that city too.
    explicit |= {r for p in names if (r := places.region_of(p)) in ROOT_PLACES and r in p.split()}
    if explicit:
        kept = {r for r in regions if _root_city(r) in explicit}
        return frozenset(kept or {r for r in explicit if r != 'surabaya'})
    return frozenset(regions)


def service_area_conflict(categories, areas, request, listing, index):
    """v5.0: a company limits unit categories to cities ("houses, apartments and shophouses: Surabaya only;
    warehouses and land: Surabaya, Gresik, Sidoarjo"). Returns which side is proven outside every allowed city of
    the matched categories ('listing' or 'request'), else None. Unproven locations are not affected."""
    allowed = set()
    for category in categories:
        allowed.update(c.lower() for c in (areas or {}).get(category) or [])
    if not allowed:
        return None
    evidence = listing.get('_evidence')
    offered = {_root_city(r) for r in _offered_regions(evidence, index.places)} or {_root_city(r) for r in _regions(listing, index)}
    if offered and not offered & allowed:
        return 'listing'
    wanted = {_root_city(r) for r in _regions(request, index, wanted=True)}
    if wanted and not wanted & allowed:
        return 'request'
    return None


def _region_conflict(index, request, listing):
    """The listing is proven in another region: from the places the listing itself names (its title first,
    landmarks removed); resolved index clusters only count when it names none."""
    wanted = _regions(request, index, wanted=True)
    evidence = listing.get('_evidence')
    offered = _offered_regions(evidence, index.places) or _regions(listing, index)
    return bool(wanted and offered and not wanted & offered)


# v4.4: "lebar di atas 10", "lebar > 10 m", "lebar depan min 10" (never "lebar jalan": the road).
_SIZE_PATTERNS = [
    ('Width', re.compile(r'\blebar(?:\s+(?:depan|muka|tanah|lahan|kavling|kav|bangunan))?\s*[:=]?\s*'
                         r'(?:(min(?:imal|im)?|maks(?:imal)?|max|di\s*atas|lebih\s+dari|>|≥|di\s*bawah|kurang\s+dari|<|≤)\.?\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)')),
    ('Road width (ROW)', re.compile(r'\brow\s*(?:jalan\s*)?(?:(min(?:imal|im)?|maks(?:imal)?|max)\.?\s*)?[:=]?\s*(\d+(?:[.,]\d+)?)\s*(mobil|mbl|m(?:eter)?)?'))]


def _upper_bound(qualifier):
    """"max 6", "di bawah 6", "< 6": a size limit from above; anything else (also no qualifier) is a minimum."""
    return bool(qualifier) and bool(re.match(r'ma[kx]|di\s*bawah|kurang|<|≤', qualifier))


_DIMENSIONS = re.compile(r'(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)')
# (request pattern, "Needs check" line, listing evidence that settles it or None). v4.8: what the buyer refuses ("tidak mau",
# "no", "jangan", ❌) is checked phrase by phrase (_requirement_items), no longer as one blanket "exclusions" item.
_REQUEST_FLAGS = [(re.compile(p), NEEDS + label, re.compile(e) if e else None) for p, label, e in [
    (r'(?:murah|\bb\.?[ \t]?u\b|bawah pasar|dibawah pasaran|di bawah pasaran|\bmiring\b|under[ \t]*market|\bnjop\b|market[ \t]+price)',
     'below-market price needs a comparison; "negotiable" is not proof', None),
    (r'parkir|parkiran', 'parking availability and terms', r'parkir|parkiran|parking|carport|garasi|garage'),
    (r'banjir', 'neighbourhood/zoning conditions',
     r'\b(?:bebas|anti|tidak|tdk|gak|ga|no|non|aman|bukan)\s*(?:dari\s+|daerah\s+|area\s+|kawasan\s+)?banjir'),
    (r'macet|residensial|zona|zoning', 'neighbourhood/zoning conditions', None),
    (r'\b(?:marmer|rumah jelek)\b', 'requested building condition', None),
    (r'peruntukan|tempat les|\busaha\b|untuk kantor', 'suitability for the intended use', None),
    # v4.3: KPR/KPA is compared with what the listing says (financing check), no blanket flag.
    (r'untuk dibuat kantor', 'special access or use', None),
    # v4.8: "Jalan bisa truk ban double", "akses kontainer 40 feet": the listing has to state the truck/container access.
    (r'(?:akses|jalan|jln|row|masuk|bisa)\b[^\n]{0,25}\b(?:truk|truck|tronton|fuso|cdd|kontainer|container|trailer|trailler)\b',
     'special access or use', r'\b(?:truk|truck|tronton|fuso|cdd|kontainer|container|trailer|trailler)\b'),
    (r'carport|garasi', "buyer's extra facilities or restrictions", r'carport|garasi|garage'),
    (r'tidak ada tetangga', "buyer's extra facilities or restrictions", None)]]
# "selain itu tidak mau" / "lokasi lain tidak mau" only restate a strict location,
# which the strict-location check enforces; they are not an exclusion list.
_STRICT_REFUSAL = re.compile(r'\b(?:selain itu|lokasi lain|area lain|daerah lain|di luar (?:itu|area itu|lokasi itu))\s*'
                             r'(?:juga\s*)?(?:tidak|tdk|gak|ga|g|nggak|ngga|gk)\s*mau\b'
                             r'|\b(?:tidak|tdk|gak|ga|g|nggak|ngga|gk)\s*mau\s+(?:lokasi|area|daerah)\s+lain\b')


# v4.7: the buyer wants an old house to tear down / rebuild (land value): "rumah hancur", "hitung tanah", "mau di bangun
# ulang", "bisa dibongkar". A listing that sells a new, renovated, move-in-ready or furnished house is a proven conflict.
_TEARDOWN = re.compile(r'\brumah[ \t]+(?:hancur|rusak|tua|lama|jelek[ \t]+hitung)\b|\bbangunan[ \t]+(?:lama|tua|rusak|hancur)\b'
                       r'|\bhitung[ \t]*(?:harga[ \t]+)?tanah\b|\b(?:di[ \t]*)?bangun[ \t]*ulang\b'
                       r'|\b(?:mau|bisa|untuk|utk|akan)[ \t]+(?:di[ \t]*)?bongkar\b')
_TEARDOWN_NEGATED = re.compile(r'\b(?:tidak|tdk|gak|ga|nggak|ngga|gk|bukan|jangan|jgn|no)[ \t]+(?:mau[ \t]+)?$')
# v4.8: a brand-new house ("NEW GRESS", "Rumah baru", "new minimalis", "sedang progress pembangunan", "inden"); a second-hand
# house ("Second FULL RENOV", "bekas") is never new.
_BRAND_NEW = re.compile(r'\b(?:rumah|bangunan|unit|hunian)[ \t]+(?:baru|gress|gres|new)\b(?![ \t]+(?:di)?renov)|\bbaru[ \t]+(?:gress|gres|jadi|dibangun|selesai)\b'
                        r'|\bgress?\b|\bbrand[ \t]*new\b|\bfirst[ \t]+hand\b|\btangan[ \t]+pertama\b'
                        r'|\bnew[ \t]+(?:house|built|build|minimalis\w*|minimalist|modern|design|scandinavian|classic|klasik|american|'
                        r'tropical|tropis|japandi|industrial|style)\b'
                        r'|\b(?:sedang|masih|dalam)[ \t]+(?:progress?[ \t]+|proses[ \t]+)?(?:pembangunan|dibangun|di[ \t]+bangun)\b'
                        r'|\bprogress?[ \t]+pembangunan\b|\binden\b')
_SECOND_HAND = re.compile(r'\b(?:second|seken|sekon|2nd|bekas|tangan[ \t]+kedua)\b(?![ \t]*(?:floor|lantai))')
# Renovated, move-in ready, minimalist/modern or furnished: not an old house to tear down either.
_NOT_OLD = re.compile(r'\b(?:baru|habis|sudah|sdh|selesai|full|fully|fresh|total)[ \t]+(?:di)?renov\w*|\brenov\w*[ \t]+(?:total|full)\b'
                      r'|\brenovated\b|\bsiap[ \t]+huni\b|\bminimalis\w*|\bminimalist\b|\bmodern[ \t]+minimalis\b'
                      r'|\b(?:full|fully|semi)[ \t]*furnish\w*|\bfurnished\b|\bbonus[ \t]+(?:furnitur\w*|furniture|perabot\w*|furnish\w*)')
_OLD_BUILDING = re.compile(r'\brumah[ \t]+(?:hancur|rusak|tua|lama|lawas)\b|\bbangunan[ \t]+(?:lama|tua|rusak|hancur|lawas)\b'
                           r'|\bhitung[ \t]*(?:harga[ \t]+)?tanah\b|(?<!tidak )(?<!tdk )(?<!gak )(?<!ga )\b(?:butuh|perlu)[ \t]+renov\w*|\bbisa[ \t]+(?:di[ \t]*)?bongkar\b')


_WANTS_NEW = re.compile(r'\b(?:rumah|bangunan)[ \t]+(?:baru|gress|gres)\b(?![ \t]*(?:juga[ \t]+)?(?:boleh|bisa|gpp|ok|oke|masuk|dicoba))'
                        r'|\bbaru[ \t]+gress?\b|\bbrand[ \t]*new\b'
                        r'|\b(?:tidak|tdk|gak|ga|nggak|ngga|gk)[ \t]+(?:perlu|usah|butuh)[ \t]+renov\w*')
# v4.8: "tidak mau renov", "no renov", "gak mau renovasi lagi": a move-in ready house.
_WANTS_READY = re.compile(r'\bsiap[ \t]+huni\b|\b(?:(?:tidak|tdk|gak|ga|nggak|ngga|gk|ndak)[ \t]*mau|gamau|no|anti)[ \t]+renov\w*'
                          r'|\bminim(?:al|um)?[ \t]+renov\w*|\b(?:tanpa|no)[ \t]+renov\w*')
# v4.8: "Prioritas baru", "harus yang baru", "wajib gress": a new house.
_WANTS_NEW_MARKED = re.compile(r'\b(?:wajib|harus|hrs|prioritas\w*|prefer\w*|diutamakan|utamakan|hanya|only|khusus)[ \t]+'
                               r'(?:(?:yang|yg)[ \t]+)?(?:baru|gress?|new|brand[ \t]*new)\b')


def _wants_new(wanted):
    """'strict' for "rumah baru / gress / tidak perlu renovasi", 'ready' for "siap huni" / "tidak mau renov", else None."""
    if _OLD_ACCEPTED.search(wanted) and (_WANTS_NEW.search(wanted) or _WANTS_NEW_MARKED.search(wanted) or _WANTS_READY.search(wanted)):
        return 'mixed'
    if _WANTS_NEW.search(wanted) or _WANTS_NEW_MARKED.search(wanted):
        return 'strict'
    return 'ready' if _WANTS_READY.search(wanted) else None


# v4.7: a pair is only Hot when the buyer request states at least this many of the details below.
HOT_MIN_BUYER_DETAILS = 5
# v4.8: the lowest score of a Hot pair (every hard attribute and every explicit requirement proven, no "Needs check" item).
HOT_MIN_SCORE = 90.0
# The only "Needs check" items a Warm pair may carry: price and land/building size inside the Warm allowance.
_DEVIATION = re.compile(r'^Needs check: (?:.+ is \d+% (?:above|below) the (?:maximum |minimum )?budget .+'
                        r'|(?:land|building) area \S+ m² is (?:below|above) the requested .+|dimensions within tolerance'
                        r'|price must be below the buyer limit; negotiation needed)$')
_KEY_DETAILS = ('budget', 'size', 'area', 'unit type')


def _buyer_details(request, rq, text, wanted):
    """{detail: stated?} over the ten indicators of a buyer request (each counts once)."""
    def stated(*keys):
        return any(request.get(k) not in (None, '', [], ()) for k in keys)
    return {
        'purpose': (request.get('transaction_type') or 'unknown') in ('sale', 'rent', 'both'),
        'unit type': bool(request.get('categories')),
        'area': bool(request.get('locations') or request.get('strict_locations') or request.get('_geo_ids')),
        'budget': stated('price_min', 'price_max', 'rent_price_min', 'rent_price_max') or bool(rq['alt_budget']),
        'size': stated('land_area_min', 'land_area_max', 'building_area_min', 'building_area_max')
                or bool(rq['dimensions'] or rq['sizes'] or rq['sizes_refused'] or rq['units']),
        'bedrooms': bool(_count(text, 'bedrooms')),
        'legal status': bool(rq['legal']),
        'facing': bool(request.get('facing') or rq['facing_refused']),
        'storeys/floor/tower': bool(_count(text, 'floors') or rq['floor'] or rq['office_floor'] or rq['towers'] or rq['blocks']),
        'furnishing/condition': rq['furnishing'] not in (None, 'any') or rq['teardown'] or bool(rq['new_house']),
    }


def _wants_teardown(text):
    """The buyer asks for an old house to rebuild ("rumah hancur", "hitung tanah", "investor … bangun ulang"). v4.8: "kalau rumah
    lama minim renov" only accepts an old house on a condition."""
    for m in _TEARDOWN.finditer(text):
        before = text[max(0, m.start() - 20):m.start()]
        if not _TEARDOWN_NEGATED.search(before) and not _CONDITIONAL.search(before):
            return True
    return False


_CONDITIONAL = re.compile(r'\b(?:kalau|kalo|klo|kl|jika|jk|bila|apabila|atau)[ \t]+(?:ada[ \t]+)?(?:yang[ \t]+|yg[ \t]+)?$')
# "Kalau bisa rumah baru, kalau rumah lama minim renov", "second gpp asal minim renov": a new house or an old one in a good state.
_OLD_ACCEPTED = re.compile(r'\b(?:kalau|kalo|klo|kl|jika|jk|bila|apabila|atau)[ \t]+(?:ada[ \t]+)?(?:rumah|bangunan)[ \t]+(?:lama|tua|second|bekas)\b'
                           r'|\b(?:second|bekas|seken)[ \t]+(?:gpp|gapapa|oke|ok|boleh|bisa|juga)\b')


# Short forms agents write for places, until the built-in place list knows them as aliases.
_SHORT_PLACES = {'dkp': 'dukuh kupang'}
# "Bebas banjir", "Bukan gang buntu", "non TS": the listing negates what the buyer refuses.
_NEGATED = re.compile(r'\b(?:bebas|bukan|non|tidak|tdk|tanpa|anti|no|gak|ga|nggak|jauh)\s+(?:dari\s+|ada\s+|di\s+)?[-:]?\s*$')


@lru_cache(maxsize=8)
def _alias_spellings(places):
    """canonical place → the alias spellings that stand for it (built-in list, company glossary, short forms)."""
    out = {}
    for alias, canonical in places.aliases.items():
        out.setdefault(places.canonical(canonical), set()).add(alias)
    for short, full in _SHORT_PLACES.items():
        out.setdefault(places.canonical(full), set()).add(short)
    return out


_CORNER_TOKEN = re.compile(r'(?:(?:rumah|posisi|unit|tipe|type|lokasi|letak|tanah|kavling|kav|ruko)\s+)?' + HOOK_WORDS)


def _exclusion_rules(request, places):
    """[(excluded place or None, spelling patterns)]: a refused place is refused under every name it has
    ("dkp" and "Dukuh Kupang")."""
    cached = request.get('_rx')
    if cached is not None and cached[0] is places:
        return cached[1]
    rules = []
    for token in dict.fromkeys(e.strip(' *_').lower() for e in request.get('exclusions') or [] if e and e.strip(' *_')):
        if _CORNER_TOKEN.fullmatch(token):
            rules.append((None, [HOOK]))  # v4.6: a refused hook is refused as "Rumah Pojok", "posisi sudut", "corner"
            continue
        canonical = places.canonical(_SHORT_PLACES.get(token, token))
        spellings = {token, canonical} | _alias_spellings(places).get(canonical, set())
        rules.append((canonical if canonical in places.known else None,
                      [re.compile(r'(?<!\w)' + re.escape(v) + r'(?!\w)') for v in sorted(spellings, key=len, reverse=True) if v]))
    request['_rx'] = (places, rules)
    return rules


def _excluded(rules, text, places, listing_places):
    for place, patterns in rules:
        if place and any(place == p or place in places.ancestors(p) for p in listing_places):
            return True
        for pattern in patterns:
            if any(not _NEGATED.search(text[max(0, m.start() - 24):m.start()]) for m in pattern.finditer(text)):
                return True
    return False


# v4.3: "LT min 300 tidak bisa kurang", "LT 2500-3500 (paling min 2300 ga mau dibawah itu)": a size floor the Warm
# allowance never loosens.
_NOT_LESS = (r'(?:\b(?:ga|gak|tidak|tdk|nggak|ngga|gk|ndak|g)\s*(?:mau|bisa|boleh)\s*(?:di\s*bawah|dibawah|kurang)'
             r'|\bharga\s+mati\b|\bmutlak\b|\bno\s+less\b|\b(?:under|kurang|dibawah|di\s*bawah)\s+(?:dari\s+)?(?:ini|itu)\s+skip\b)')
_HARD_MIN = re.compile(r'\bmin(?:imal|im|imum)?\.?\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(?:m2|m²|meter|mtr|m\b)?[^\n\d]{0,40}?' + _NOT_LESS)
_HARD_STATED = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:m2|m²|meter|mtr|m\b)?\s*[(,.]?\s*' + _NOT_LESS)
_BUILDING_LINE = re.compile(r'\b(?:lb|luas\s+bangunan|bangunan)\b')
_LAND_LINE = re.compile(r'\b(?:lt|luas|tanah|lahan|luasan)\b')


def _hard_minimums(text):
    out = {}
    for line in text.split('\n'):
        m = _HARD_MIN.search(line) or _HARD_STATED.search(line)
        if not m:
            continue
        factor = 'building' if _BUILDING_LINE.search(line[:m.start()] or line) else 'land' if _LAND_LINE.search(line) else None
        if factor and factor not in out:
            out[factor] = _number(m.group(1))
    return out


# "Apartemen Eastcost atau Educity", "Apart.Denver/Cornell", "apartmen royal cityloft": the buildings a buyer names.
_BUILDING = re.compile(r'\b(?:apart(?:e)?ment?|apart|apt|[ck]ondo(?:minium)?)\b\.?[ \t]*([a-z][a-z /,&]{1,60})')
# A weak first word names a building only together with the next one ("Water Place", never "water" of "water heater").
_WEAK_BUILDING_WORDS = {'royal', 'grand', 'the', 'new', 'taman', 'green', 'puncak', 'bukit', 'pakuwon', 'graha', 'water', 'sky',
                        'sun', 'star', 'blue', 'gold', 'golden', 'silver', 'central', 'city'}
_BUILDING_SUFFIXES = {'indah', 'city', 'mall', 'residence', 'residences', 'mansion', 'park', 'garden', 'square', 'golf', 'world',
                      'land', 'raya', 'regency', 'town', 'hill', 'hills', 'village', 'point', 'place', 'plaza', 'permai', 'kertajaya',
                      'dharmahusada', 'family', 'famili'}


def _building_names(text):
    """Building names a buyer writes after "apartemen" that the built-in building list does not know (known
    buildings come whole from known_buildings: "water place" is Waterplace, never "water")."""
    names = set()
    for m in _BUILDING.finditer(text):
        for alternative in re.split(r'\s*(?:/|,|&|\batau\b|\bor\b|\bdan\b)\s*', m.group(1)):
            words = [w for w in alternative.split() if w not in _GENERIC_APARTMENT_WORDS and len(w) > 2]
            if words and known_buildings(alternative):
                pass
            elif words and words[0] in _WEAK_BUILDING_WORDS and len(words) > 1:
                # "Royal Cityloft", "Puncak Tidar": a weak first word only names a building with the next one. v4.8: the
                # second word alone is no building name ("Tidar" of "Puncak Tidar" is also in "Gunawangsa Tidar").
                names.add(' '.join(words[:2]))
            elif words:
                names.add(words[0])
            if not alternative.strip() or len(alternative.split()) > 3:
                break  # the phrase ran into the next words of the line
    return frozenset(names)


def _wanted_buildings(names):
    """"Apartemen lariz laviz pakuwon mall": a complex named next to buildings outside it only says where they are."""
    complexes = set(_APARTMENT_COMPLEX.values())
    specific = {n for n in names if n not in complexes}
    return frozenset(specific if specific and any(_APARTMENT_COMPLEX.get(n) is None for n in specific) and specific != names else names)


def _same_building(a, b):
    return a == b or _APARTMENT_COMPLEX.get(a) == b or _APARTMENT_COMPLEX.get(b) == a


def _in_building(offered, wanted):
    """The listing's building is the one wanted or a tower of the wanted complex (not the reverse: a buyer who
    wants Tanglin is not answered by "Pakuwon Mall")."""
    return offered == wanted or _APARTMENT_COMPLEX.get(offered) == wanted


def _named(name, text, squeezed):
    """The listing names this building/tower as a whole word ("Water Place" also as "waterplace"; a single word is
    never found inside another one: no "water" in "waterheater")."""
    whole = name.replace(' ', '')
    return bool(re.search(r'(?<!\w)' + re.escape(name).replace(r'\ ', r'\s*') + r'(?!\w)', text)) or (
        ' ' in name and len(whole) >= 5 and whole in squeezed)


_CLOSED_LIST = re.compile(r'\b(?:jangan|jgn|tidak|tdk|ga|gak|nggak|no)\s+(?:usah\s+)?(?:di)?tawar\w*\s+(?:blok|block|tower)\s+lain'
                          r'|\b(?:blok|block|tower)\s+lain(?:nya)?\s+(?:jangan|jgn|tidak|tdk|ga|gak|nggak)\b'
                          r'|\bselain\s+(?:blok|block|tower)\s+(?:itu|tsb|tersebut|di\s*atas)\s+(?:jangan|jgn|tidak|tdk|ga|gak)\b')
# What a unit looks at ("view pool", "city view"); filler after the word ("city view yang indah") names no view.
_VIEW_WORDS = re.compile(r'\b(?:city|kota|pool|kolam(?:\s+renang)?|laut|sea|ocean|pantai|beach|garden|taman|golf|lake|danau|gunung|'
                         r'mountain|river|sungai|bay|skyline|park|hutan|forest|sunrise|sunset|selat|suramadu|bridge|jembatan|'
                         r'lapangan|mall|tol|jalan\s+raya|bandara|airport|gedung)\b')


_VIEW_FILLER = {'yang', 'yg', 'indah', 'bagus', 'cantik', 'keren', 'lepas', 'luas', 'oke', 'ok', 'nya', 'banget', 'sekali',
                'langsung', 'terbuka', 'open', 'dan', 'atau', 'juga', 'ya', 'dong', 'terbaik', 'top', 'mewah', 'menarik'}


def _view(text):
    """The view a text asks for ("View pool", "View Yani Golf", "city view yang indah" → city), None for
    "view bebas" / "viewing" or none. A view never runs into the next line ("View bebas\nPembelian KPA")."""
    for m in re.finditer(r'\bview\b', text):
        line_end = text.find('\n', m.end())
        after = re.sub(r'^[ \t]*[:=]?[ \t]*(?:ke[ \t]+|to[ \t]+)?', '', text[m.end():line_end if line_end >= 0 else len(text)])
        if _FREE_VIEW.match(after) or re.search(r'\bbebas\s*$', text[max(0, m.start() - 8):m.start()]):
            continue
        phrase = re.match(r'([a-z]+(?:[ \t]+[a-z]+){0,2})(?=[ \t]*(?:[,()/.!]|$))', after)
        words = phrase.group(1).split() if phrase else []
        cut = next((i for i, w in enumerate(words) if w in _VIEW_FILLER), len(words))
        if words[:cut]:
            return ' '.join(words[:cut])
        known = _VIEW_WORDS.match(after) or re.search(r'(' + _VIEW_WORDS.pattern + r')[ \t]*$', text[max(0, m.start() - 20):m.start()])
        if known:
            return re.sub(r'\s+', ' ', known.group(known.lastindex or 0) if known.re is not _VIEW_WORDS else known.group())
    return None


def _request_features(request):
    """Request-only evidence, computed once per request instead of once per pair."""
    cached = request.get('_rq')
    if cached is not None:
        return cached
    text = _rule_text(request)
    wanted = request.get('_wanted_text')
    if wanted is None:
        wanted = request['_wanted_text'] = _wanted_text(text)
    flag_text = _STRICT_REFUSAL.sub(' ', text) if request.get('location_strict') else text
    flags = [(label, evidence) for pattern, label, evidence in _REQUEST_FLAGS if pattern.search(flag_text)]
    if len(re.findall(r'\b(?:request|renter|buyer)\b', text)) >= 5:
        flags.append((NEEDS + 'message contains several requests; needs separating', None))
    dims = _DIMENSIONS.search(text)
    required, refused = legal_requirement(text)
    # A wished-for tower/block ("Blok O yg diprioritaskan", "prefer Tower A") is no requirement, unless the buyer
    # refuses every other one ("prefer blok FF, P …, opsi lainnya blok A/AA …, jangan tawarkan blok lain").
    closed = _CLOSED_LIST.search(text)
    must = _DONE_LINE.sub(' ', _preference_split(wanted)[0] if _PREFERENCE.search(wanted) and not closed else wanted)
    towers, blocks = tower_names(must)
    refused_text = '\n'.join(_split_negation(line)[1] for line in text.split('\n'))
    request['_rq'] = cached = {
        'flags': flags,
        'districts': re.findall(r'\bdistrict\s+\w+', text),
        # "pusat kota Surabaya" is the Surabaya Pusat region (a hard area, parser); a bare "tengah kota" is checked here.
        'centre': bool(re.search(r'tengah kota|pusat kota', city_centre(text))),
        'evidence': [r for r in ('berpagar', 'kontainer', 'container', '40 feet', 'marmer') if r in text],
        'citraland_utama': 'citraland utama' in must,
        'road': bool(re.search(r'(?:hanya|only|wajib|harus).{0,30}(?:raya|jalan)|(?:raya|jalan).{0,30}(?:only|hanya)', text)),
        'sizes': [(label, m) for label, pattern in _SIZE_PATTERNS for m in [pattern.search(wanted)] if m],
        # v4.8: "tidak mau lebar 5": that width (or less) is refused, never a minimum.
        'sizes_refused': [(label, m) for label, pattern in _SIZE_PATTERNS for m in [pattern.search(refused_text)] if m and not m.group(1)],
        'view': _view(wanted), 'view_refused': _view(refused_text),
        'dimensions': dims.groups() if dims else None,
        'access': bool(re.search(r'\b(?:row|akses|jalan)\b.{0,20}\b(?:besar|lebar)\b', text)),
        'buildings': _building_names(wanted),
        'known_buildings': _wanted_buildings(known_buildings(_strip_landmarks(wanted))),
        'budget_text': bool(re.search(r'(?:budget|budged|budjet|bugdet|budger|buget|bujet|bajet|harga|under).{0,20}\d', text)),
        # v4.3 R8 property terms
        'legal': required, 'legal_refused': refused, 'units': unit_types(wanted), 'towers': towers, 'blocks': blocks,
        # "1BR ya bukan Studio", "no tower E & F": refused unit types and towers reject a listing that has them.
        'units_refused': unit_types(refused_text) - unit_types(wanted), 'towers_refused': tower_names(refused_text)[0] - towers,
        'facing_refused': frozenset(facing_directions(refused_text)) - frozenset(request.get('facing') or []),
        'floor': floor_requirement(wanted, True), 'office_floor': floor_requirement(wanted, False),
        'financing': financing_need(text), 'hard_min': _hard_minimums(text), 'lease': lease_need(text),
        'alt_budget': _alternative_budget(request, text),
        'extras': _extra_needs(wanted), 'furnishing': _furnishing(wanted, True), 'teardown': _wants_teardown(text), 'new_house': _wants_new(wanted),
    }
    cached['details'] = _buyer_details(request, cached, text, wanted)
    return cached


# Requirements no other rule reads ("Sumber mata air, SIPA", "Ex pabrik kayu", "yg ada mesin potong", "KHUSUS DEPAN
# DANAU", "1 BR kosongan"): (request pattern, listing evidence pattern or None = group 1 of the request match, label).
# A listing that does not show the evidence keeps the pair out of Hot ("Needs check: …").
_EXTRA_NEEDS = [(re.compile(p), re.compile(e) if e else None, label) for p, e, label in [
    (r'\bsumber[ \t]*(?:mata[ \t]*)?air\b|\bmata[ \t]*air\b|\bsumur[ \t]*bor\b|\bair[ \t]*tanah\b',
     r'\bsumber[ \t]*(?:mata[ \t]*)?air\b|\bmata[ \t]*air\b|\bsumur\b|\bair[ \t]*tanah\b|\bartesis\b', 'water source ("{}")'),
    (r'\bsipa\b', r'\bsipa\b|izin\s+pengambilan\s+air', 'water extraction permit (SIPA)'),
    (r'\b(?:ex|eks|bekas)[ \t.-]*(?:pabrik|parbik|gudang|usaha)[ \t]+(?!(?:yang|yg|dan|atau|di|ini|itu|aktif|lama|hitung|tanah|luas|'
     r'siap|masih|sudah|dekat|nol|raya|jalan|lokasi|area|daerah|kawasan|dijual|disewakan|harga|murah|strategis)\b)([a-z]{3,})', None,
     'former use ("{}")'),
    # "yg ada mesin potong", "dengan mesin" (never "gudang mesin": what the building is for).
    (r'\b(?:ada|ad|dengan|dgn|plus|beserta|include\w*|termasuk|lengkap)\b[^\n]{0,12}?(?P<q>\bmesin\b(?:[ \t]+(?!yang\b|yg\b|dan\b|atau\b)[a-z]{4,})?)',
     r'\bmesin\b', 'equipment ("{}")'),
    (r'\b(?:depan|hadap|menghadap|pinggir|tepi)[ \t]+(danau|lake|laut|pantai|sungai|kolam|golf)\b', None, 'location "{}"'),
    (r'\bloading[ \t]*dock', r'\bloading[ \t]*dock|\bdock\b', 'loading dock'),
    (r'\b(?:balkon|balcony)\b', r'\b(?:balkon|balcony)\b', 'balcony'),
    (r'\b(?:lift|elevator)\b', r'\b(?:lift|elevator)\b', 'lift'),
    (r'\b(?:jejer|gandeng|berjejer|bersebelahan)\b', r'\b(?:jejer|gandeng|berjejer|bersebelahan|sebelahan)\b',
     'adjacent units side by side (jejer)'),
    # v4.8: "gudang dimana kanan kirinya ada gudang orang lain (pergudangan)": a warehouse estate.
    (r'\bpergudangan\b|\bkanan[ \t]+kiri\w*[ \t]+(?:ada[ \t]+)?gudang\b|\bkomple?ks?[ \t]+gudang\b',
     r'\bpergud\w*|\bkomple?ks?[ \t]+(?:gudang|pergudangan)\b|\bkawasan[ \t]+(?:industri|pergudangan|gudang)\b|\b(?:industrial|warehouse)[ \t]+'
     r'(?:estate|park|complex)\b|\bbiz[ \t]*park\b|\bmargomulyo[ \t]+indah\b|\bsuri[ \t]+mulia\b', 'warehouse estate (pergudangan)'),
    # v4.8: "Rumah kost AKTIF": a boarding house, not any house.
    # (Never "untuk usaha kost", "untuk dibuat kos'an": a house or plot to turn into one.)
    (r'(?s)^(?!.*\b(?:untuk|utk|buat|dibuat|dijadikan|dibangun|bangun)[ \t]+(?:usaha[ \t]+|jadi[ \t]+)?kos)'
     r'.*?\b(?:kost|kos|kosan|kos-kosan)\b(?![ \t]*(?:kosongan|an\b))',
     r'\b(?:rumah[ \t]+)?kos(?:t|tan)?[ \t]+(?:aktif|putr[ai]|campur|eksklusif|exclusive|\d+[ \t]*kamar)|\brumah[ \t]+kos(?:t|tan)?\b'
     r'|\bkos-?kosan\b|\bboarding[ \t]+house\b|\bindekos\b|\bkamar[ \t]+kos', 'boarding house (kost)')]]
_UNFURNISHED = re.compile(r'(?<!semi )(?<!semi)\bkosongan\b|\bunfurnish\w*|\b(?:non|no|tanpa)[ \t-]*(?:furnish\w*|furnitur\w*|perabot\w*)')
_FURNISHED = re.compile(r'\b(?:full[ \t]*|fully[ \t]*|semi[ \t]*)?furnish(?:ed)?\b|\bfurnitur\w*|\bperabot\w*')
# v4.7 furnishing levels: 'empty' (kosongan), 'semi', 'full'; 'any' = the buyer accepts every level.
_SEMI = re.compile(r'\bsemi[ \t-]*(?:furnish\w*|furnitur\w*|kosongan|isi)\b')
_FULL = re.compile(r'\b(?:full|fully|ful)[ \t-]*(?:furnish\w*|furnitur\w*|isi|perabot\w*)\b|(?<!semi )(?<!semi-)(?<!semi)\bfurnish(?:ed)?\b'
                   r'|\bbonus[ \t]+(?:furnitur\w*|furniture|perabot\w*|furnish\w*)|\bsiap[ \t]+huni[ \t]+lengkap\b')
_FITTINGS = re.compile(r'\bkitchen[ \t]*set\b|\bac\b[^\n]{0,20}\b(?:kulkas|tv|sofa|lemari|kasur|bed|mesin cuci|water heater)\b'
                       r'|\b(?:lemari|kasur|sofa|springbed|kulkas)\b')
_ANY_FURNISHING = re.compile(r'\b(?:boleh|bisa|bebas|terserah)[ \t]+(?:furnish\w*|semi|kosong\w*|unfurnish\w*)[ \t]*(?:[/,]|atau|or)'
                             r'|\b(?:furnish\w*|semi|kosong\w*)[ \t]*/[ \t]*(?:semi|kosong\w*|furnish\w*)[ \t]*/'
                             r'|\bfurnish\w*[ \t]+(?:bebas|terserah|apa saja)\b')


# v4.8: furnishing words as agents type them ("furblnish", "furnis", "furnised", "fullfurnish", "FF", "SF").
_FURNISH_TYPO = re.compile(r'(?<![a-z])(full|fully|ful|semi|un)?[ \t-]?f(?:u|ur)[a-z]{0,2}?n[ie]?sh?(?:ed|d)?(?![a-z])')
_FURNISH_SHORT = re.compile(r'(?<![\w/.])(?<!blok )(?<!block )(?<!tower )(?<!type )(?<!tipe )(?<!unit )(?<!cluster )(ff|sf)(?![\w/])'
                            r'(?![ \t]*(?:[,/&]|dan|atau|or)[ \t]*[a-z]{1,2}\b)')


def _furnish_words(text):
    def word(m):
        prefix = m.group(1)
        return ('un' if prefix == 'un' else prefix + ' ' if prefix else '') + 'furnish'
    return _FURNISH_SHORT.sub(lambda m: 'full furnish' if m.group(1) == 'ff' else 'semi furnish', _FURNISH_TYPO.sub(word, text))


def _furnishing(text, buyer=False):
    """'empty' | 'semi' | 'full' | 'any' (buyer accepts every level) | None (not stated)."""
    text = _furnish_words(text)
    if buyer and _ANY_FURNISHING.search(text):
        return 'any'
    rest = _SEMI.sub(' ', text)
    found = {level for level, hit in (('semi', bool(_SEMI.search(text))), ('empty', bool(_UNFURNISHED.search(rest))),
                                      ('full', bool(_FULL.search(_UNFURNISHED.sub(' ', rest))))) if hit}
    if not found and not buyer and _FITTINGS.search(text):
        found = {'semi'}  # "AC, kitchen set, lemari": furniture without a level
    if len(found) == 1:
        return found.pop()
    return 'any' if buyer and found else None


_FURNISH_WORDS = {'empty': 'unfurnished (kosongan)', 'semi': 'semi-furnished', 'full': 'fully furnished'}


def _wants_unfurnished(wanted):
    """"1 BR kosongan": the buyer wants an empty unit; "Semi kosongan", "Boleh furnish/semi/kosong" accept furniture."""
    return _furnishing(wanted, True) == 'empty'


def _extra_needs(wanted):
    """[(label, evidence pattern)] of the buyer requirements in _EXTRA_NEEDS; a label quotes the buyer's words."""
    out = []
    for pattern, evidence, label in _EXTRA_NEEDS:
        m = pattern.search(wanted)
        if not m:
            continue
        word = m.group(1) if m.groups() and 'q' not in pattern.groupindex else None
        if evidence is None and not word:
            continue
        out.append((label.format(re.sub(r'\s+', ' ', m.group('q') if 'q' in pattern.groupindex else m.group(0))),
                    evidence or re.compile(r'(?<!\w)' + re.escape(word) + r'(?!\w)')))
    return out


_BUDGET_FIELDS = ('transaction_type', 'rent_price_min', 'rent_price_max', 'rent_price_basis', 'land_area_min', 'land_area_max')


def _alternative_budget(request, text):
    """The request with its second budget on the other basis ("Budget 2,5-3 M atau 9 jt/m²": 9 jt per m² next to the
    total), or None. A per-m² amount stays below 200 jt; a total is at least 100 jt."""
    basis = request.get('price_basis')
    if basis not in ('total', 'per_m2') or request.get('transaction_type') == 'rent':
        return None
    for c in _price_candidates(text):
        if c['basis'] not in ('total', 'per_m2') or c['basis'] == basis or c['kind'] == 'rent':
            continue
        lo, hi = c['approx'] or (c['min'], c['max'])
        if lo is not None and lo == hi:
            lo = None
        value = hi if hi is not None else lo
        if value is None or (c['basis'] == 'per_m2' and value >= 2 * 10**8) or (c['basis'] == 'total' and value < 10**8):
            continue
        return {**{k: request.get(k) for k in _BUDGET_FIELDS}, 'price_min': lo, 'price_max': hi, 'price_basis': c['basis']}
    return None


# "Harga hubungi 0812…" is a phone number, not an amount.
_UNREAD_PRICE = re.compile(r'(?:\b(?:harga|hrg|price|idr)\b|💰|\brp)[^\n\d]{0,15}(?!(?:\+?62|0)\s?8\d)\d|\d[\d.,]*\s*(?:jt|juta|mil+[iy]ard?)\b')
_UNREAD_LAND = re.compile(r'\b(?:lt|luas(?!\s*(?:bangunan|bang\b|semi|gross|sg|nett?o?\b|rumah|unit|kamar))(?:\s+tanah)?|l\.\s*tanah)\b'
                          r'[^\n\d]{0,12}\d+(?:[.,]\d+)?\s*(?:m2|m²|meter|mtr|m\b|[x×]\s*\d)')
_UNREAD_BUILDING = re.compile(r'\b(?:lb|luas\s+bangunan|l\.\s*bangunan)\b[^\n\d]{0,12}\d+(?:[.,]\d+)?\s*(?:m2|m²|meter|mtr|m\b)')


def _listing_features(listing):
    cached = listing.get('_ls')
    if cached is not None:
        return cached
    text = _rule_text(listing)
    place_text = listing.get('_place_text') or _strip_landmarks(text)
    flags = []
    if re.search(r'sudah tersewa|masih tersewa|masih disewa|sedang disewa', text):
        flags.append(NEEDS + 'property is still rented out; handover date')
    if re.search(r'proses finishing|proses renovasi|sedang dibangun|belum selesai|sebelum renovasi selesai|setelah renovasi selesai', text):
        flags.append(NEEDS + 'condition/price after construction work')
    width = re.search(r'(\d+(?:[.,]\d+)?)\s*[x×]\s*\d', text)
    dims = _DIMENSIONS.search(text)
    tower = re.search(r'\btower\s+([a-z]+)\b', text)
    apartment = re.search(r'\bapart(?:e)?ment?\s+([a-z]+)\b', text)
    # A part that still holds several properties: two land sizes and two prices of its own.
    land = set()
    for line in text.split('\n'):
        # Land sizes only: "Luas Semi Gross 36,75 / Luas Netto 29,4" are two measures of one unit.
        m = re.match(r'\W*(?:lt|luas(?!\s+(?:bangunan|semi|gross|sg|nett?o?|netto)\b)(?:\s+tanah)?|l\.\s*tanah)\b\s*[.:=]?\s*(.*)', line)
        if m:
            lo, hi = _range_from_line(m.group(1))
            if lo or hi:
                land.add(lo or hi)
    prices = {c['max'] if c['max'] is not None else c['min'] for c in _price_candidates(text)}
    towers, blocks = tower_names(text)
    # "*EDISI APARTEMEN* 1. GUNAWANGSA MERR … 11. WATERPLACE TOWER F …": numbered properties left in one message.
    numbered = len(re.findall(r'(?m)^\W*\d{1,2}\s*[.)]\s*\W*(?:apart\w*|rumah|ruko|gudang|tanah|kav\w*|villa|kantor|office|tower|' +
                              _APARTMENT_RE.pattern + r')', text))
    listing['_ls'] = cached = {
        'offers': len(prices) >= 2 and (len(land) >= 2 or numbered >= 3 or len(known_buildings(text)) >= 3 or
                                        len(re.findall(r'(?m)^\W*(?:di\s*jual|di\s*sewakan|for\s+(?:sale|rent))\b', text)) >= 3),
        # A price or size written in a way the parser could not read: never "not stated" (R7).
        'price_text': bool(_UNREAD_PRICE.search(text)), 'land_text': bool(_UNREAD_LAND.search(text)),
        'building_text': bool(_UNREAD_BUILDING.search(text)),
        'centre': bool(re.search(r'tengah kota|pusat kota', text)),
        'flags': flags,
        'sizes': {label: pattern.search(text) for label, pattern in _SIZE_PATTERNS},
        'width': _number(width[1]) if width else None,
        'dimensions': dims.groups() if dims else None,
        'tower': tower.group(1) if tower else None,
        'apartment': apartment.group(1) if apartment else None,
        'known_buildings': known_buildings(_strip_landmarks(text)),
        'bundled': len(re.findall(r'(?m)^\s*\d+[.)]\s*(?:dijual|disewakan)\b', text)) >= 2,
        'squeezed': re.sub(r'\s+', '', text),
        # v4.3 R8 property terms
        'legal': legal_statuses(text), 'units': unit_types(text), 'towers': towers, 'blocks': blocks,
        'floor': floor_level(text, True), 'office_floor': floor_level(text, False), 'financing': financing_offer(text),
        'lease': lease_offer(text),
        # v4.4: a building or tower named only as a landmark ("dekat … Eastcost Mall") is no proof of the building.
        'place_text': place_text, 'place_squeezed': re.sub(r'\s+', '', place_text),
    }
    return cached


def _hierarchy_match(places, wanted, offered, qualifiers=()):
    """'inside' when a listing place lies in the most specific requested place, 'general' when it
    only matches a broader requested area, else None. ``qualifiers``: the regions/cities the buyer wrote after a place
    to say where it is (left out of ``wanted``, see _qualifying_areas)."""
    known_wanted = [p for p in wanted if p in places.known]
    specific = [p for p in known_wanted if not any(p != o and p in places.ancestors(o) for o in known_wanted)]
    general = False
    for want in specific:
        if want in offered:
            return 'inside', want, want
    for want in specific:
        for have in offered:
            if places.relation(have, want) == 'inside':
                return 'inside', want, have
    for want in known_wanted:
        for have in offered:
            if places.relation(have, want) == 'inside':
                general = True
    if not general and qualifiers:
        # v4.6: "villa bukit mas, Surabaya barat" and a listing that only names that region (or an area in it holding the
        # place) leaves the place unconfirmed, as before; a listing naming another place in the region is proven elsewhere.
        narrowest = [h for h in offered if not any(h != o and places.canonical(h) in places.ancestors(o) for o in offered)]
        general = any(places.relation(have, want) == 'general' and any(
            places.canonical(have) == q or places.relation(have, q) == 'inside' for q in qualifiers)
            for want in specific for have in narrowest)
    return ('general', None, None) if general else (None, None, None)


# Words after a development name that name no cluster ("Pakuwon Indah murah", "Graha Famili blok FF", "… only").
_NOT_CLUSTER = re.compile(r'^(?:surabaya|sby|barat|timur|utara|selatan|pusat|tengah|sekitar\w*|dsk\w*|dll|dst|cluster|klaster|'
                          r'perum\w*|apart\w*|lokasi|lingkungan|kawasan|komplek\w*|posisi|depan|belakang|pinggir|dalam|luar|utama|'
                          r'boulevard|jalan|jl|jln|nomor|renov\w*|minimalis|modern|klasik|classic|mewah|luxury|second|bekas|mohon|'
                          r'tolong|kirim|send|info|please|pls|thanks|terima|dicari|mencari|carikan|lagi|juga|yaa?|dong|gan|bos|sis|'
                          r'kak|pak|bu|ko|ci|om|tante|hub\w*|contact|kontak|wa|telp)$')


def _unknown_cluster(index, request):
    """(development, word) when the buyer names a development and, right after it, a word that is no known place,
    cluster or property term ("Pakuwon Indah Pallazate"): a specific cluster the location table does not know."""
    cached = request.get('_unknown_cluster')
    if cached is not None and cached[0] is index:
        return cached[1]
    found = None
    for area, following in index.area_followers(request.get('_wanted_text') or _wanted_text(_rule_text(request))):
        if following and following[0] in ('cluster', 'klaster'):
            following = following[1:]
        word = following[0] if following else ''
        if ' '.join(following) and index.places.recognize(' '.join(following)):
            continue  # "… Lidah Wetan": a known place follows
        if len(word) >= 4 and word.isalpha() and not _NOT_CLUSTER.match(word) and not _PHRASE_STOP.search(word) \
                and not _PHRASE_DROP.search(word) and word not in GENERIC_PLACE_WORDS and word not in index.terms \
                and word not in index.area_terms and word not in _GENERIC_APARTMENT_WORDS \
                and index.places.canonical(word) not in index.places.known and len(index.resolve(f'{area} {word}')) != 1:
            found = (area, word)  # (a misspelling the index resolves to one cluster, "Pallazate", is a known cluster)
            break
    request['_unknown_cluster'] = (index, found)
    return found


def _unconfirmed_specific(index, req_locations, listing, evidence):
    """A requested place more specific than the development the listing was matched on ("Greenlake" inside
    CitraLand) that the listing does not confirm, else None."""
    places, ids = index.places, listing.get('_geo_ids') or []
    developments = set()
    for ident in ids:
        if ident in index.area_of:
            developments.update(index.area_chain(index.area_of[ident]))
    developments -= set(SURABAYA_REGIONS) | set(ROOT_PLACES)
    if not developments:
        return None
    wanted = [p for p in dict.fromkeys(places.canonical(x) for x in req_locations if x)
              if p in places.known and p not in developments and developments & set(places.ancestors(p))]
    if not wanted:
        return None
    offered = {places.canonical(x) for x in evidence if x}
    for ident in ids:
        if ident in index.clusters:
            offered.update(places.canonical(x) for x in index.cluster_places(ident))
    if any(w in offered or any(o in places.known and places.relation(o, w) == 'inside' for o in offered) for w in wanted):
        return None
    return wanted[0]


# Words after "apartemen" that name no building ("Apartment for rent", "Apartemen murah di …").
_GENERIC_APARTMENT_WORDS = {'di', 'daerah', 'area', 'surabaya', 'sby', 'yang', 'yg', 'mewah', 'murah', 'full', 'baru', 'siap', 'luas',
                            'type', 'tipe', 'studio', 'the', 'for', 'and', 'dan', 'atau', 'or', 'dijual', 'disewakan', 'jual', 'sewa',
                            'sale', 'rent', 'lease', 'dekat', 'lantai', 'tower', 'unit', 'connect', 'connecting', 'ready', 'bagus',
                            'cantik', 'strategis', 'view', 'pusat', 'tengah', 'kota', 'barat', 'timur', 'utara', 'selatan', 'gress',
                            'furnish', 'furnished', 'kosongan', 'second', 'harga', 'hanya', 'only', 'dengan', 'with', 'in', 'at',
                            'superblock', 'bisa', 'lokasi', 'premium', 'exclusive', 'eksklusif', 'apartemen', 'apartment', 'nya',
                            'lama', 'sekitar', 'sekitarnya', 'furniture', 'interior', 'bebas', 'apa', 'saja', 'aja', 'hrg', 'invest',
                            'investasi', 'beli', 'cari', 'dicari', 'kosong', 'semi', 'bed', 'bedroom', 'kamar', 'ada', 'mau', 'buat',
                            'untuk', 'utk', 'request', 'req', 'buyer', 'renter', 'nego', 'stock', 'hot', 'listing', 'luxury', 'murmer',
                            'pilihan', 'mana', 'manapun', 'apapun', 'segera', 'butuh', 'cepat', 'sby', 'jatim', 'indonesia'}
_FLOOR_UNITS = {'apartment', 'office'}


def _title(value):
    return ' '.join(w if w.isupper() else w.capitalize() for w in str(value).split())


def _either(values, labels=None, prefix=''):
    """"Tower A or C", "2BR or 3BR", "Surat Ijo"."""
    shown = [labels.get(v, v) if labels else (v.upper() if len(v) <= 3 else _title(v)) for v in sorted(values)]
    shown[0] = prefix + shown[0]
    return shown[0] if len(shown) == 1 else ', '.join(shown[:-1]) + ' or ' + shown[-1]


def _lease_text(months):
    """12 → "1 year", 24 → "2 years", 3 → "3 months"."""
    if months % 12 == 0:
        return f'{months // 12} year' + ('s' if months > 12 else '')
    return f'{months} month' + ('s' if months > 1 else '')


def _floor_text(need):
    lo, hi = need[0], need[1]
    if lo is None:
        return f'floor {hi} or lower'
    if hi is None:
        return f'floor {lo} or higher'
    return f'floor {lo}–{hi}'


def _term_checks(request, rq, ls, categories, text, custom, unknown, reasons):
    """R8 hard attributes: legal title, apartment unit type, tower/block, floor level, financing. Appends reasons,
    Custom items (a hard requirement the listing does not state) and soft items; returns False to reject."""
    apartment = 'apartment' in categories
    # Legal title: required ∩ stated ≠ ∅ and nothing refused; for apartments "SHM" means the strata SHM (SHMRS).
    required, refused, stated = rq['legal'], rq['legal_refused'], ls['legal']
    if apartment:
        required, refused, stated = ({'strata' if k == 'shm' else k for k in keys} for keys in (required, refused, stated))
    if stated & refused:
        return False
    if required:
        if not stated:
            custom.append(NEEDS + 'legal status not stated — buyer only accepts ' + _either(required, LEGAL_LABELS))
        elif not stated & required:
            return False
        else:
            reasons.append('Legal status: ' + _either(stated & required, LEGAL_LABELS) + ' ✓')
    # Apartment unit type (houses keep the bedroom-count rules).
    if apartment and ls['units'] & rq['units_refused']:
        return False
    if ls['towers'] & rq['towers_refused']:
        return False
    if apartment and rq['units']:
        if not ls['units']:
            custom.append(NEEDS + 'unit type not stated — buyer wants ' + _either(rq['units'], UNIT_TYPE_LABELS))
        elif not ls['units'] & rq['units']:
            return False
        else:
            reasons.append('Unit type: ' + _either(ls['units'] & rq['units'], UNIT_TYPE_LABELS) + ' ✓')
    # Towers ("Tower A atau C saja") and estate blocks ("Blok GC"): another one rejects, none stated is Custom.
    for wanted, offered, word in ((rq['towers'], ls['towers'], 'Tower'), (rq['blocks'], ls['blocks'], 'Blok')):
        if not wanted:
            continue
        if offered:
            if not offered & wanted:
                return False
            reasons.append(word + ' ' + _either(offered & wanted) + ' ✓')
        elif any(len(name) >= 3 and _named(name, ls['place_text'], ls['place_squeezed']) for name in wanted):
            reasons.append(word + ' ' + _either({n for n in wanted if len(n) >= 3 and _named(n, ls['place_text'], ls['place_squeezed'])}) + ' ✓')
        else:
            custom.append(NEEDS + f"{word.lower()} not stated — buyer only accepts {_either(wanted, prefix=word + ' ')}")
    # Floor level of an apartment/office unit ("Lantai di bawah 10" vs "Lantai 20").
    if categories & _FLOOR_UNITS:
        need, have = (rq['floor'], ls['floor']) if apartment else (rq['office_floor'], ls['office_floor'])
        if need:
            lo, hi, preferred = need
            if have is None:
                unknown.append(NEEDS + f'floor level not stated (buyer wants {_floor_text(need)})')
            elif (hi is not None and have[0] > hi) or (lo is not None and have[1] < lo):
                if not preferred:
                    return False
                unknown.append(NEEDS + f'floor preference not met (buyer prefers {_floor_text(need)})')
            elif (hi is not None and have[1] > hi) or (lo is not None and have[0] < lo):
                unknown.append(NEEDS + f'exact floor not stated (buyer wants {_floor_text(need)})')
            else:
                reasons.append(f'Floor {have[0]} ✓' if have[0] == have[1] else 'Floor level ✓')
    # Financing: a KPR/KPA buyer cannot buy a cash-only listing or a title no bank lends on.
    if rq['financing'] and request.get('transaction_type') != 'rent':
        word = rq['financing'].upper()
        offer, titles = ls['financing'], ls['legal']
        if offer == 'cash_only' or (titles and not titles & _BANKABLE and titles & {'surat_ijo', 'petok', 'ppjb'}):
            return False
        if offer == 'mortgage':
            reasons.append(f'Financing: {word} possible ✓')
        elif offer == 'cash_hard':
            unknown.append(NEEDS + f'listing quotes a cash price; {word} not confirmed')
    return True


# ---------------------------------------------------------------- v4.8: explicit buyer requirements (rule 4)
# A buyer line with a requirement marker ("Wajib minimalist", "Prioritas rumah baru", "Ngga mau TS") whose content no other rule
# reads must be shown by the listing; otherwise the pair is Custom ("requirement not verified"). Preferences count too: they
# never make a pair Hot unless the listing shows them. Move-in / survey timing, "segera", "cash ready" are no requirements.
_REQ_MARKER = re.compile(
    r'(?P<need>\b(?:wajib|harus|hrs|hanya|only|khusus|prioritas\w*|diprioritaskan|prefer\w*|diutamakan|utamakan|minimal|minim|min)\b)'
    r'|(?P<refuse>\b(?:(?:tidak|tdk|gak|ga|g|nggak|ngga|gk|ndak|enggak|engga)[ \t]*mau|gamau|ogah|no(?![ \t]*[.:]?[ \t]*\d)|jangan|jgn|'
    r'jngn|jgan|bukan|anti)\b|[❌❎✖✗✘⛔\U0001f6ab])')
_REQ_SUFFIX_MARKERS = re.compile(r'only|(?:di)?(?:utamakan|prioritaskan)')
_REQ_CLAUSE = re.compile(r'[,;!?()\[\]|•\n]|\.(?!\d)|\s[-–]\s')
# Location content is read by the location rules (strict places, exclusions, places the index does not know).
_REQ_PLACE_CUE = re.compile(r'\b(?:daerah|area|lokasi|wilayah|kawasan|seputar|sekitar\w*|lingkungan|cluster|klaster|perumahan|perum|'
                            r'komplek|kompleks|dsk\w*|arah|menuju)\b')
_NAMED_PROPERTY = re.compile(r'\b(?:ex|eks|bekas)\b|\b(?:hotel|pabrik|gudang|ruko|kantor|gedung|mall|plaza|apartemen\w*|apart|'
                             r'perumahan|perum|cluster|komplek\w*)\b')
_REQ_ALTERNATIVES = re.compile(r'\s+(?:atau|or|ato)\s+|\s*/\s*')
# Words other rules read (asset, purpose, sizes, rooms, legal title, facing, tower/unit type, price, payment, place words) and
# words that state no property requirement (fillers, timing, who the buyer is, how to send the offer).
_RULE_WORDS = frozenset((
    'rumah rmh ruko rukan gudang tanah lahan kavling kav kaveling apartemen apartement apartment apartmen apart apt unit villa vila '
    'kantor office pabrik hotel kost kos kosan gedung bangunan house warehouse condo kondominium soho studio penthouse hunian properti '
    'property komersial jual beli sewa kontrak cari dicari mencari carikan butuh dibutuhkan dijual disewakan buyer renter request req '
    'investor pembeli penyewa kpr kpa cash tunai keras bertahap cicil cicilan angsur angsuran bank take over lelang invest investasi '
    'sale rent lease buy luas luasan ukuran m2 meter mtr kamar kmr tidur tdr mandi bedroom bedrooms bed lantai tingkat storey storeys '
    'floor floors lebar panjang dimensi row jalan jln mobil mbl motor muka hadap hdp menghadap facing utara selatan timur barat laut '
    'daya tower blok block type tipe loft shm shgb hgb sertifikat sertipikat strata shmrs ppjb ajb petok girik letter surat ijo imb '
    'pbg legal legalitas hak milik guna pecah budget budjet harga hrg under max maks maksimal maximal maximum jt juta jtan miliar '
    'milyar rb ribu nego net nett per tahun thn bulan bln tahunan bulanan pertahun perbulan setahun sebulan surabaya sby sidoarjo '
    'gresik kota pusat tengah raya hook hoek pojok pojokan corner sudut yang ada mau aja saja yaa dong deh lagi juga dan atau '
    'and dengan dgn untuk utk buat bwt bisa boleh the dari itu ini tsb sudah sdh masih lebih sangat banget bgt harus wajib hanya only '
    'khusus min minim minimal minimum prefer preferably prioritas diprioritaskan utamakan diutamakan oke please pls plz tolong mohon '
    'kirim info spec spek foto japri langsung client klien serius serious cepat segera ready model posisi letak kalau kalo klo jika '
    'nya sama punya pakai gpp gapapa apa mana semua tidak tdk gak nggak ngga ndak jangan jgn bukan non tanpa kecuali selain skip '
    'anti hindari tawarkan ditawarkan tawar dikasih kasih share sharing masuk survey survei kapan hari besok minggu akhir awal '
    'tanggal tgl waktu closing deal transaksi cocok sesuai bagus baik nyaman owner pemilik principal direct agent agen agency cobroke '
    'broke marketing fee komisi end user enduser keluarga pribadi sendiri dipakai ditempati tinggal orang org ready stock stok '
    'listing hub hubungi telp telpon kontak contact japri dulu nanti sekarang langsung skrg asap urgent hot buyerku klienku dicarikan '
    'tersedia available tolong bantu thanks thank makasih terima kasih trims mksh pak bu bos sis gan kak ko ci om tante '
    'besar kecil lega luasnya ukurannya depan belakang samping atas bawah dalam luar sebelah terserah bebas apapun manapun '
    'terlalu sekali agak kurang cukup dipertimbangkan pertimbangkan considered paling dibawah diatas keatas kebawah ex eks '
    'mengikuti menyesuaikan wajar namun tapi tetapi jelas utama coba dapat sampai sampe lbh bujet all jutaan proper jarak menit '
    'jam arah lain lainnya sertakan tersewa disewa roi usia hektar hektare feet are kalo habis penting dekat dkt deket near').split())
# Words read by a rule only when that rule applies to the buyer (building condition, furnishing, view).
_CONDITION_WORDS = frozenset('baru gress gres new renov renof renovasi direnov siap huni lama tua hancur bongkar hitung bangun ulang second '
                             'bekas kondisi terawat layak brand jadi'.split())
_FURNISH_WORDS_ALL = frozenset('furnish furnished furniture furnitur semi kosongan kosong unfurnish unfurnished full fully isi '
                               'perabot interior'.split())
_KEEP_SHORT = frozenset({'ts'})
_REQ_SYNONYMS = {'minimalist': ('minimalis',), 'minimalis': ('minimalist',), 'ts': ('tusuk sate', 'tusuksate'), 'tusuk': ('ts',),
                 'kolam': ('pool',), 'renang': ('pool', 'swimming'), 'pool': ('kolam renang',), 'mewah': ('luxury', 'luxurious', 'elegan'),
                 'luxury': ('mewah',), 'taman': ('garden',), 'garden': ('taman',), 'sutet': ('sutt',), 'kuburan': ('makam',),
                 'makam': ('kuburan',), 'gang': ('gg',), 'buntu': ('cul de sac',), 'onegate': ('one gate',), 'security': ('satpam',),
                 'satpam': ('security',), 'cctv': ('cctv',),
                 'perumahan': ('perum', 'cluster', 'klaster', 'komplek', 'kompleks', 'residence', 'regency', 'estate', 'one gate',
                               'town house', 'townhouse', 'citraland', 'pakuwon', 'graha famili', 'graha family', 'villa'),
                 'pergudangan': ('komplek gudang', 'kompleks gudang', 'kawasan gudang', 'kawasan industri', 'industrial estate',
                                 'industrial park', 'warehouse complex', 'bizpark', 'biz park')}
_LISTING_NEGATED = re.compile(r'\b(?:bukan|non|tidak|tdk|tanpa|no|bebas|anti|gak|ga|nggak|ngga|free|jauh(?:[ \t]+dari)?)[ \t]*[-:]?[ \t]*'
                              r'(?:ada[ \t]+|dari[ \t]+|di[ \t]+)?$')


_IN_ESTATE = re.compile(r'\b(?:dalam|didalam|masuk|harus|wajib)[ \t]+(?:perumahan|perum|komplek\w*|cluster|klaster|kawasan[ \t]+perumahan)\b')
_REQ_NAMED_UNIT = re.compile(r'\b(?:tower|towe|tw|twr|blok|block|blk|type|tipe|unit|lantai|lt|no|nomor|kav|kavling)\.?[ \t]*'
                             r'[a-z0-9]{1,4}(?:[ \t]*(?:[,/&-]|dan|atau|or|s/?d)[ \t]*[a-z0-9]{1,4}\b)*\b')


def _requirement_words(phrase, places, consumed):
    """The words of a requirement phrase no other rule reads (places, rule vocabulary and fillers removed)."""
    phrase = apply_glossary(phrase).lower()  # "wbm" is Wisata Bukit Mas
    estate = _IN_ESTATE.search(phrase)  # "Wajib dalam perumahan": a gated estate, no place
    if estate:
        estate = 'pergudangan' if re.match(r'[ \t]*\w*gudang', phrase[estate.end():]) or 'gudang' in estate.group() else 'perumahan'
    cue = _REQ_PLACE_CUE.search(phrase)
    if cue:
        phrase = phrase[:cue.start()]
    if estate:
        phrase += ' ' + estate
    phrase = _REQ_NAMED_UNIT.sub(' ', phrase)  # "Tower ABC only", "blok FF": the tower/block rules
    if places is not None:
        for m in list(places.loose_pattern.finditer(phrase))[::-1]:
            phrase = phrase[:m.start()] + ' ' + phrase[m.end():]
    words = []
    for word in re.findall(r'[a-z]+', phrase):
        if (len(word) >= 3 or word in _KEEP_SHORT) and word not in _RULE_WORDS and word not in consumed and word not in words \
                and (places is None or places.canonical(word) not in places.known):
            words.append(word)
    return words


def _consumed_words(request, rq, places=None):
    """Rule vocabulary that applies to this buyer only (condition, furnishing, view, keyword requirements, extras)."""
    consumed = set()
    if rq['new_house'] or rq['teardown']:
        consumed |= _CONDITION_WORDS
    if rq['furnishing'] is not None:
        consumed |= _FURNISH_WORDS_ALL
    if rq['view'] or rq['view_refused']:
        consumed |= {'view'} | set(re.findall(r'[a-z]+', ' '.join(filter(None, (rq['view'], rq['view_refused'])))))
    for keyword in request.get('requirements') or []:
        consumed |= set(keyword.split())
    for label, _ in rq['extras']:
        consumed |= set(re.findall(r'[a-z]+', label.lower()))
    for pattern, _, _ in _REQUEST_FLAGS:
        consumed |= set(re.findall(r'[a-z]{3,}', re.sub(r'\\[a-zA-Z]', ' ', pattern.pattern))) - {'tidak', 'ada'}
    for names in (rq['towers'], rq['blocks']):
        if names:
            consumed |= set(names) | {''.join(sorted(names))}  # "Waterplace ABC only": towers A, B, C
    # Places the parser read, also ones the place list does not know ("Kavling Graha Only": the strict-location rule).
    refused_places = [e for e in request.get('exclusions') or [] if places is not None and places.canonical(e) in places.known]
    for place in [*(request.get('locations') or []), *(request.get('strict_locations') or []), *refused_places]:
        consumed |= set(re.findall(r'[a-z]+', str(place).lower()))
    return consumed


_PREFERENCE_MARKER = re.compile(r'prefer\w*|prioritas\w*|diprioritaskan|diutamakan|utamakan')


def _requirement_items(request, rq, places):
    """[(kind 'need' | 'refuse' | 'refuse_named', shown phrase, [alternatives: [words]], {preferred towers/blocks/facing})]
    of a buyer's marked lines, cached per request."""
    cached = request.get('_req_items')
    if cached is not None and cached[0] is places:
        return cached[1]
    consumed = _consumed_words(request, rq, places)
    items, seen = [], set()
    for line in _rule_text(request).split('\n'):
        clauses = [c for c in _REQ_CLAUSE.split(line)]
        for i, clause in enumerate(clauses):
            for m in _REQ_MARKER.finditer(clause):
                refuse = m.group('refuse') is not None
                marker = re.sub(r'\s+', ' ', m.group()).strip()
                after, before = clause[m.end():], clause[:m.start()]
                # "harus minimal 3 kamar": the next marker reads the phrase.
                following = _REQ_MARKER.search(after)
                if following and not re.search(r'[a-z0-9]', after[:following.start()]):
                    continue
                if following:
                    after = after[:following.start()]  # "wajib minimalis tidak mau TS": two requirements
                if _REQ_SUFFIX_MARKERS.fullmatch(marker) or not re.search(r'[a-z0-9]', after):
                    phrase, shown = before, f'{before.strip()} {marker}'
                    if not re.search(r'[a-z0-9]', before):
                        previous = next((c for c in clauses[i - 1::-1] if re.search(r'[a-z0-9]', c)), '') if i else ''
                        phrase, shown = previous, f'{previous.strip()} ({marker})'
                else:
                    phrase, shown = after, f'{marker} {after.strip()}'
                if phrase is before:
                    earlier = list(_REQ_MARKER.finditer(before))
                    if earlier:
                        phrase = before[earlier[-1].end():]  # "wajib minimalis tidak mau": only what follows "wajib"
                alternatives = [w for w in (_requirement_words(part, places, consumed) for part in _REQ_ALTERNATIVES.split(phrase)) if w]
                extras = {}
                if not refuse and _PREFERENCE_MARKER.fullmatch(marker):
                    # "prefer blok FF", "prioritas hadap timur": the tower/block and facing rules skip wishes, so the
                    # listing has to show them here.
                    towers, blocks = tower_names(phrase)
                    extras = {key: frozenset(value) for key, value in (('towers', towers), ('blocks', blocks)) if value}
                    if re.search(r'\b(?:hadap|hdp|facing|menghadap)\b', phrase):
                        directions = facing_directions(phrase)
                        if directions:
                            extras['facing'] = frozenset(directions)
                if not alternatives and not extras:
                    continue
                shown = re.sub(r'\s+', ' ', shown).strip(' :-')
                shown = shown if len(shown) <= 40 else shown[:39].rstrip() + '…'
                if refuse:
                    # "jangan tawarkan ex hotel Tanjung", "bukan ruko Pasar Atom": a named property (an asset or former use
                    # in the phrase) is refused by name; a feature ("no TS", "tidak mau gang") needs the listing to deny it.
                    named = bool(_NAMED_PROPERTY.search(phrase))
                    for words in alternatives:
                        key = ('refuse', tuple(words))
                        if key not in seen:
                            seen.add(key)
                            items.append(('refuse_named' if named else 'refuse', shown, [words], {}))
                else:
                    key = ('need', tuple(map(tuple, alternatives)), tuple(sorted((k, tuple(sorted(v))) for k, v in extras.items())))
                    if key not in seen:
                        seen.add(key)
                        items.append(('need', shown, alternatives, extras))
    request['_req_items'] = (places, items)
    return items


def _brand_new_spans(text):
    if _SECOND_HAND.search(text):
        return []
    return [(m.start(), m.end()) for m in _BRAND_NEW.finditer(text)]


# Words a listing shows only in a phrase of its own ("baru" is a new house, never "pompa baru").
_WORD_EVIDENCE = {'baru': _brand_new_spans, 'new': _brand_new_spans, 'gress': _brand_new_spans, 'gres': _brand_new_spans}


def _word_spans(word, text):
    """Spans of a requirement word (or its synonyms) in a listing text; long words match by their stem ("minimalist" ~
    "minimalis")."""
    if word in _WORD_EVIDENCE:
        return _WORD_EVIDENCE[word](text)
    spans = []
    for alt in (word, *_REQ_SYNONYMS.get(word, ())):
        if ' ' in alt or len(alt) <= 5:
            pattern = r'(?<![a-z])' + re.escape(alt).replace(r'\ ', r'\s*') + r'(?![a-z])'
        else:
            pattern = r'(?<![a-z])' + re.escape(alt[:max(5, len(alt) - 2)])
        spans += [(m.start(), m.end()) for m in re.finditer(pattern, text)]
    return spans


def _check_requirements(items, text, ls, facing, unknown, reasons):
    """Appends a "requirement not verified" item or a positive reason per marked requirement; False when the listing has
    what the buyer refuses. ``ls``: the listing features (towers, blocks), ``facing``: the listing's facing directions."""
    for kind, shown, alternatives, extras in items:
        if kind == 'need':
            shows = {'towers': ls['towers'], 'blocks': ls['blocks'], 'facing': frozenset(facing or ())}
            if all(shows[key] & wanted for key, wanted in extras.items()) and (
                    not alternatives or any(all(_word_spans(w, text) for w in words) for words in alternatives)):
                reasons.append(f'Buyer requirement met: “{shown}” ✓')
            else:
                unknown.append(NEEDS + f'requirement not verified: “{shown}”')
            continue
        words = alternatives[0]
        spans = {w: _word_spans(w, text) for w in words}
        plain = {w: [s for s in found if not _LISTING_NEGATED.search(text[max(0, s[0] - 24):s[0]])] for w, found in spans.items()}
        if all(plain[w] for w in words):
            return False  # the listing has what the buyer refuses ("posisi TS" for "no TS")
        if spans[words[0]] and not plain[words[0]]:
            reasons.append(f'Buyer requirement met: “{shown}” ✓')  # "bukan TS", "bebas banjir"
        elif kind == 'refuse_named' and not any(spans.values()):
            pass  # "jangan tawarkan ex hotel Tanjung": a listing that never names it is another property
        else:
            unknown.append(NEEDS + f'requirement not verified: “{shown}”')
    return True


_LOCATION_CUE = re.compile(r'\b(?:daerah|area|lokasi|wilayah|kawasan|seputar|sekitar)\b')
_PLACE_STOP = re.compile(r'[()\[\]!?;:.\d]|\b(?:budget|harga|lt|lb|luas|kt|km|kamar|hadap|prefer\w*|diutamakan|minimal|min|max|tidak|tdk|gak|'
                         r'ga|jika|kalau|kalo|klo|yg|yang|untuk|utk|buat|dengan|dgn|bisa|boleh|only|hanya|saja|aja|menuju|seperti|'
                         r'karena|supaya|agar|sampai|sampe|tapi|namun|juga|jg|dekat|dkt|deket|near|ke|dari|lebih|paling|yang|bukan|'
                         r'jangan|jgn|harus|wajib|cari|dicari|mau|ada|sudah|sdh|sangat|banget|bgt|ini|itu|tsb)\b')
_PLACE_FILLER = frozenset('strategis premium elit elite bagus ramai nyaman aman tenang istimewa prime top apik mantap oke dsk dskt '
                          'dsktnya sekitarnya sekitar seputar dekat dkt deket near kota sby surabaya timur barat utara selatan pusat '
                          'tengah raya jalan jl jln gang mana saja bebas juga graha pakuwon citra puncak industri pergudangan '
                          'gudang perkantoran perumahan kampus sekolah mall pasar tol bisnis komersil komersial jalur terdekat '
                          'sekitaran sana sini situ lancar padat ramai sepi kondusif jatim jawa timur indonesia rumput alas perlu '
                          'banjir selama lengkap minta belum okupansi disukai suka gambar site plan sbb penduduk middle low tempat '
                          'sktrnya sktr dsktrnya update info'.split())


_AREA_PART = re.compile(r'[ \t]*(?:(?:cluster|klaster|lokasi|bagian|area|posisi)[ \t]+)?(depan|belakang|tengah)\b')


def _area_parts(request, places):
    """v4.8: [(place, part)] for "Citraland Depan", "Citraland Cluster Depan & Tengah": the buyer wants one part of the
    development, a finer level than the development itself."""
    cached = request.get('_area_parts')
    if cached is not None and cached[0] is places:
        return cached[1]
    text = request.get('_wanted_text') or _wanted_text(_rule_text(request))
    out = []
    for m in places.pattern.finditer(text):
        part = _AREA_PART.match(text, m.end())
        if part and places.development(places.canonical(m.group())):
            out.append((places.canonical(m.group()), part.group(1)))
    request['_area_parts'] = (places, out)
    return out


def _unknown_places(request, places):
    """v4.8: words a buyer writes as a place ("daerah sby timur karang empat karang asem dsk") that the place list does not
    know, as one phrase, or None."""
    cached = request.get('_unknown_places')
    if cached is not None and cached[0] is places:
        return cached[1]
    found = []
    text = apply_glossary(_wanted_text(_preference_split(_rule_text(request))[0]))
    for line in text.split('\n'):
        for cue in _LOCATION_CUE.finditer(line):
            phrase = _PLACE_STOP.split(line[cue.end():])[0]
            for m in list(places.loose_pattern.finditer(phrase))[::-1]:
                phrase = phrase[:m.start()] + ' | ' + phrase[m.end():]
            for part in re.split(r'[|,/&]|\s[-–]\s|\b(?:dan|atau|or|and|n)\b', phrase):
                words = [w for w in re.findall(r'[a-z]+', part) if len(w) >= 3 and w not in _PLACE_FILLER and w not in _RULE_WORDS
                         and w not in GENERIC_PLACE_WORDS and not _PHRASE_STOP.search(w) and not _PHRASE_DROP.search(w)
                         and w not in _CONDITION_WORDS and w not in _FURNISH_WORDS_ALL]
                if words and len(words) <= 3 and ' '.join(words) not in found:
                    found.append(' '.join(words))
    value = ', '.join(found) or None
    request['_unknown_places'] = (places, value)
    return value


def assess_pair(request, listing, settings, semantic=50.0, location_index=None):
    """None when the pair is ruled out, else the scores, the explanation and the status (v4.8):
    hot = every hard attribute (purpose, unit, location at the buyer's level, legal title, tower/unit type) and every
    explicit buyer requirement proven, price/sizes in the Hot band, no "Needs check" item, score ≥ HOT_MIN_SCORE;
    warm = everything proven, only price and/or land/building size inside the Warm allowance (or a lower score);
    custom = anything unclear (a requirement not stated or not proven in the listing, a vague request) or a
    nearby-cluster alternative. ``pre_cap_score``: the score before the Warm/Custom cap of 79."""
    if request.get('document_type', request.get('classification', 'buyer_request')) != 'buyer_request':
        return None
    if listing.get('document_type', listing.get('classification', 'property_listing')) != 'property_listing':
        return None
    # R6: the purpose (buy / rent) decides first, then the unit type.
    deal, deal_unknown = _deal(request, listing)
    if deal is False:
        return None
    req_categories = set(request.get('categories') or [])
    categories = req_categories & set(listing.get('categories') or [])
    if not categories:
        return None
    req_text = _rule_text(request)
    text = _rule_text(listing)
    rq, ls = _request_features(request), _listing_features(listing)
    # R7: custom = hard requirements the listing does not state; unknown = soft "Needs check" items.
    reasons, unknown, custom = [], [], []
    if not _term_checks(request, rq, ls, categories, text, custom, unknown, reasons):
        return None
    # R6.4: the purpose line always comes first; an unconfirmed purpose is no positive reason.
    purpose = {'sale': 'buy', 'rent': 'rent', 'both': 'buy or rent'}.get(deal)
    lead = ['Purpose: ' + (purpose + ' ✓' if not deal_unknown else purpose + ' (not confirmed)') if purpose else 'Purpose: not stated']
    lead.append('Unit: ' + ', '.join(CATEGORY_LABELS.get(c, c).lower() for c in sorted(categories)) + ' ✓')
    if request.get('review_status', 'auto') != 'auto' or listing.get('review_status', 'auto') != 'auto':
        unknown.append(NEEDS + 'extracted details need review')
    if deal_unknown:
        # The buyer's purpose is a hard requirement; a listing that does not state its own needs a manual check.
        known_request = (request.get('transaction_type') or 'unknown') != 'unknown'
        (custom if known_request else unknown).append(NEEDS + 'transaction type not confirmed')
    if ls['offers']:
        # R7: the towers, unit types and places of several properties are mixed; nothing is proven for one of them.
        unknown.append(NEEDS + 'message contains several offers/prices; held back until each property is separated')
    index = location_index or empty_index()
    places = index.places
    if listing.get('_evidence') is None:
        listing.setdefault('_place_text', _strip_landmarks(text))
        listing['_evidence'] = _evidence_places(listing, places)
    # v5.0: outside the company's service area for this unit category ("Graha Golf, Malang" for a house buyer
    # of a Surabaya-only brand) is never recommended, however well the rest fits.
    if settings and settings.get('category_areas') and service_area_conflict(categories, settings['category_areas'], request, listing, index):
        return None
    # A refused place the listing only names as a landmark ("dekat Citraland") is not where it is.
    if request.get('exclusions'):
        rules = _exclusion_rules(request, places)
        listing_places = {places.canonical(p) for p in listing.get('locations') or [] if p}
        resolved_text = ''
        if location_index and listing.get('_geo_ids'):
            # v4.7: "Citraland Utara jangan ditawarkan" also refuses "Bukit Palma (CitraLand Utara)": the cluster the
            # index resolved, its development chain and the area named in its brackets.
            kind, target = index.scope(listing['_geo_ids'])
            names = []
            if kind == 'cluster':
                names = [index.clusters[target]['name']]
                listing_places |= {places.canonical(p) for p in index.cluster_places(target)}
                if target in index.area_of:
                    listing_places |= set(index.area_chain(index.area_of[target]))
            elif kind == 'area':
                listing_places |= set(index.area_chain(target))
            for name in names:
                listing_places |= {places.canonical(inner.strip().lower()) for inner in re.findall(r'\(([^)]*)\)', name)}
            resolved_text = '\n'.join(n.lower() for n in names)
        if _excluded(rules, ls['place_text'], places, listing_places) or \
                (resolved_text and _excluded([(None, patterns) for _, patterns in rules], resolved_text, places, set())):
            return None
    # Mandatory conditions that need evidence cannot disappear from the score.
    unknown += [label for label, evidence in rq['flags'] if not (evidence and evidence.search(text))]
    for refinement in rq['districts']:
        if refinement not in text: custom.append(NEEDS + 'specific location ' + refinement + ' not confirmed')
    if rq['centre'] and not ls['centre']:
        custom.append(NEEDS + 'city-centre location not confirmed')
    unknown += ls['flags']
    for requirement in rq['evidence']:
        if requirement not in text:
            unknown.append(NEEDS + 'no evidence of ' + requirement)
    if rq['road']:
        unknown.append(NEEDS + "buyer's required road/street")
    for (label, need), refused in [(size, False) for size in rq['sizes']] + [(size, True) for size in rq['sizes_refused']]:
        have = ls['sizes'][label]
        value = _number(have[2]) if have else None
        if label == 'Width' and value is None and ls['width']:
            value = ls['width']
        if label == 'Road width (ROW)' and have and (need[3] or '') != (have[3] or ''):
            value = None
        if value is None:
            unknown.append(NEEDS + label.lower() + ' not stated in a comparable unit')
        elif refused and value <= _number(need[2]):
            return None  # "tidak mau lebar 5" and a 5 m wide plot
        elif not refused and ((_upper_bound(need[1]) and value > _number(need[2])) or
                              (not _upper_bound(need[1]) and value < _number(need[2]))):
            return None
        else:
            reasons.append(label + ' matches')
    facing = set(request.get('facing') or [])
    available = set(listing.get('facing') or [])
    if available and rq['facing_refused'] and available <= rq['facing_refused']:
        return None  # "Tidak mau hadap barat" vs "Hadap Barat"
    if facing:
        if available and not facing.intersection(available):
            return None
        if not available:
            unknown.append(NEEDS + 'facing not stated')
        else:
            reasons.append('Facing matches')
    elif rq['facing_refused'] and not available:
        unknown.append(NEEDS + 'facing not stated (buyer refuses ' + _either(rq['facing_refused']) + ')')
    if deal == 'rent' and rq['lease']:
        # v4.4: "SEWA 3 BULAN" vs "Minim 2 thn" is a proven conflict; an unstated minimum only matters for a short lease.
        minimum, monthly = ls['lease']
        monthly = monthly or 'per_month' in (listing.get('price_basis'), listing.get('rent_price_basis'))
        if minimum is not None:
            if rq['lease'] < minimum:
                return None
            reasons.append('Lease term fits (minimum ' + _lease_text(minimum) + ')')
        elif rq['lease'] < 12 and not monthly:
            unknown.append(NEEDS + 'minimum lease not stated (renter wants ' + _lease_text(rq['lease']) + ')')
    if rq['view_refused'] and _view(text) == rq['view_refused']:
        return None  # "tidak mau view pool"
    if rq['view'] and _view(text) != rq['view'] and rq['view'] not in text:
        unknown.append(NEEDS + 'view ' + rq['view'] + ' not confirmed')
    if rq['dimensions']:
        if not ls['dimensions']:
            unknown.append(NEEDS + 'width × length not stated')
        else:
            for need, have in zip(rq['dimensions'], ls['dimensions']):
                value, _ = band((_number(need),) * 2, (_number(have),) * 2, *tolerances(settings, 'land'))
                if value == 0:
                    return None
                if value < 100:
                    unknown.append(NEEDS + 'dimensions within tolerance')
    apartment = 'apartment' in categories
    for unit, label in [('bedrooms', 'Bedrooms'), ('floors', 'Floors')]:
        if apartment:
            break  # an apartment's unit type (studio, 2BR) and floor level are compared above
        need, have = _count(req_text, unit), _count(text, unit)
        if not need:
            continue
        if unit == 'floors' and req_categories == {'land'}:
            unknown.append(NEEDS + f'feasibility of building {need[0]:g} floors')
            continue
        if have and (have[1] < need[0] or have[0] > need[1]):
            return None
        if have is None:
            unknown.append(NEEDS + label.lower() + ' not stated')
        else:
            reasons.append(label + ' match')
    if rq['access']:
        unknown.append(NEEDS + 'road access width')
    for label, evidence in rq['extras']:
        if not evidence.search(text):
            unknown.append(NEEDS + label + ' not confirmed')
    old_house = bool(_OLD_BUILDING.search(text))
    brand_new = bool(_BRAND_NEW.search(text)) and not _SECOND_HAND.search(text)
    if categories <= {'land'}:
        pass  # v4.8: a plot has no building condition ("Cari tanah/rumah lama" and "Dijual tanah")
    elif rq['teardown']:
        # v4.7: "rumah hancur … mau di bangun ulang" never fits a new / renovated / furnished house. v4.8: also "NEW GRESS",
        # "minimalis", "sedang progress pembangunan", "full renov".
        if old_house:
            reasons.append('Building condition: old house / land value ✓')
        elif brand_new or _NOT_OLD.search(text):
            return None
        else:
            unknown.append(NEEDS + 'building condition (buyer wants an old house to rebuild)')
    elif rq['new_house']:
        # "rumah baru / siap huni / tidak perlu renovasi" never fits "hitung tanah / perlu renovasi / rumah tua". v4.8: a
        # second-hand house ("Second FULL RENOV") is no new house; a renovated or move-in ready one only fits "siap huni".
        ready = brand_new or (rq['new_house'] == 'ready' and bool(_NOT_OLD.search(text)))
        if rq['new_house'] == 'mixed' and not brand_new:
            # "Kalau bisa rumah baru, kalau rumah lama minim renov": only a new house is proven; how much work another one
            # needs is unknown.
            unknown.append(NEEDS + 'building condition (buyer accepts an old house only if it needs little renovation)')
        elif ready:
            reasons.append('Building condition: new / move-in ready ✓')
        elif old_house:
            return None
        elif rq['new_house'] == 'strict':
            unknown.append(NEEDS + 'building condition (buyer wants a new house)')
        elif 'siap huni' not in (request.get('requirements') or []):  # else "move-in ready not confirmed" below
            unknown.append(NEEDS + 'building condition (buyer wants a move-in ready house)')
    wanted_furnish = rq['furnishing']
    if wanted_furnish and wanted_furnish != 'any':
        have_furnish = _furnishing(text)
        if have_furnish == wanted_furnish:
            reasons.append('Furnishing: ' + _FURNISH_WORDS[have_furnish] + ' ✓')
        elif have_furnish is None:
            unknown.append(NEEDS + 'furnishing not stated (buyer wants it ' + _FURNISH_WORDS[wanted_furnish] + ')')
        elif {wanted_furnish, have_furnish} == {'empty', 'full'} or (wanted_furnish == 'empty' and have_furnish == 'semi'):
            return None
        else:
            unknown.append(NEEDS + 'furnishing: buyer wants it ' + _FURNISH_WORDS[wanted_furnish] + '; listing is ' +
                           _FURNISH_WORDS[have_furnish])
    for requirement in request.get('requirements') or []:
        if requirement in ('shm', 'hgb'):
            continue  # legal titles are compared above (R8)
        if requirement not in (listing.get('requirements') or []):
            unknown.append(NEEDS + REQUIREMENT_LABELS.get(requirement, requirement) + ' not confirmed')
    # v4.8 rule 4: marked requirements no rule above reads ("Wajib minimalist", "Ngga mau TS").
    if not _check_requirements(_requirement_items(request, rq, places), text, ls, listing.get('facing'), unknown, reasons):
        return None
    # ---------------------------------------------------------------- building (apartment / tower name)
    building, in_building = None, set()
    wanted_buildings = rq['buildings'] | rq['known_buildings']
    if wanted_buildings and categories & _FLOOR_UNITS:
        in_building = {n for n in wanted_buildings if _named(n, ls['place_text'], ls['place_squeezed'])} or \
            {n for n in rq['known_buildings'] if any(_in_building(o, n) for o in ls['known_buildings'])}
        if in_building:
            building = 'proven'
        else:
            named_tower, named_apartment = ls['tower'], ls['apartment']
            related = any(_same_building(n, o) for n in rq['known_buildings'] for o in ls['known_buildings'])
            if (named_tower and len(named_tower) > 1 and named_tower not in wanted_buildings) or (not related and (
                    named_apartment and len(named_apartment) > 2 and named_apartment not in _GENERIC_APARTMENT_WORDS
                    and named_apartment not in req_text)):
                return None
            if rq['known_buildings'] and ls['known_buildings'] and not related:
                return None  # "Amor or Bella" vs "Apartemen La Riz Mansion": another building
            building = 'unconfirmed'
    # ---------------------------------------------------------------- location
    # Places named only as a wish ("Surabaya Barat, prefer Wiyung") are checked softly, not required.
    req_locations, locations = _required_locations(request, places), listing.get('locations') or []
    evidence = listing.get('_evidence')
    if evidence is None:
        listing.setdefault('_place_text', _strip_landmarks(text))
        evidence = listing['_evidence'] = _evidence_places(listing, places)
    if '_wanted_text' not in request:
        request['_wanted_text'] = _wanted_text(req_text)
    wanted_text = request['_wanted_text']
    strict = [p for p in (request.get('strict_locations') or []) if p] if request.get('location_strict', True) else []
    # Regions of the places the listing itself names: a resolved cluster elsewhere is no evidence.
    listing_regions = frozenset(r for r in (places.region_of(p) for p in evidence) if r)
    radius = location_radius(settings)
    proximity = location_index.compare(wanted_text, text, request.get('_geo_ids'), listing.get('_geo_ids'),
                                       strict=bool(strict) or None, listing_regions=listing_regions, radius_km=radius) \
        if location_index else {'kind': 'unindexed'}
    strict_state, strict_building = None, False
    if strict:
        place_text = listing.get('_place_text')
        if place_text is None:
            place_text = _strip_landmarks(text)
        strict_state = index.strict_verdict(strict, evidence, listing.get('_geo_ids') or [], place_text)
        if strict_state == 'unknown' and listing_regions and any(
                places.canonical(t) not in places.known and not index.strict_ids(places.canonical(t)) for t in strict):
            # "Kavling Graha Only OR Pakuwon Indah": an unknown target ("graha") lies where the buyer's other
            # targets (or requested places) lie; a listing proven in another region is outside all of them.
            scope = {places.region_of(places.canonical(t)) for t in strict if places.canonical(t) in places.known} or \
                {places.region_of(places.canonical(p)) for p in req_locations if places.canonical(p) in places.known}
            if scope and None not in scope and not listing_regions & scope:
                strict_state = 'outside'
        if strict_state != 'outside' and building == 'proven' and any(
                _in_building(o, k) for t in strict for k in (known_buildings(t) or {t}) for o in in_building | ls['known_buildings']):
            # "Benson only", "La Riz, La Viz only": the listing is in the very building the buyer insists on.
            strict_state, strict_building = 'inside', True
        if strict_state == 'outside' or (strict_state != 'inside' and proximity['kind'] in ('nearby', 'excluded')):
            return None
    elif proximity['kind'] == 'excluded':
        return None
    # Places from the listing's title win; a place named later may belong to another property or be a landmark.
    loc = location_score(req_locations, evidence)
    location_reason = None
    # Location items (R7): unconfirmed = Custom, proven elsewhere = reject; "buyer named only the city" stays soft.
    located = []
    state = want = None
    if strict_building:
        pass  # the building is the location evidence (below)
    elif strict_state == 'inside':
        loc = 100.0
        location_reason = proximity['reason'] if proximity['kind'] == 'exact' else \
            'Inside requested area: ' + ', '.join(_title(p) for p in strict)
    elif proximity['kind'] in {'exact', 'nearby'}:
        loc = proximity['score']
        location_reason = proximity['reason']
        if proximity['kind'] == 'nearby':
            # R7: a nearby-cluster alternative is Custom (the buyer has to approve the other cluster).
            custom.insert(0, NEEDS + f'buyer approval for a nearby location (within {radius:g} km)')
        elif not proximity['reason'].startswith('Same cluster'):
            # "Rumah Greenlake Citraland": the index matched the whole development, but the buyer named a place
            # inside it that this listing does not confirm (Bukit Palma is CitraLand, not Greenlake).
            named = _unconfirmed_specific(index, req_locations, listing, evidence)
            unknown_cluster = _unknown_cluster(index, request) if not named else None
            if named:
                loc = min(loc, 85.0)
                located.append(NEEDS + f'exact cluster not confirmed (buyer named {_title(named)})')
            elif unknown_cluster and not re.search(r'(?<!\w)' + re.escape(unknown_cluster[1]) + r'(?!\w)', text):
                # "Pakuwon Indah Pallazate": the buyer named a cluster the location table does not know; a listing
                # elsewhere in the development is not proven to be in it.
                loc = min(loc, 85.0)
                located.append(NEEDS + 'exact cluster not confirmed (buyer named '
                               f'{index.area_label(unknown_cluster[0])} {_title(unknown_cluster[1])})')
    else:
        if not strict and _region_conflict(index, request, listing):
            return None
        offered = evidence
        if not offered and listing.get('_geo_ids'):
            # No place named in the text, but the location index placed the listing (a cluster alias).
            kind, target = index.scope(listing['_geo_ids'])
            offered = index.cluster_places(target) if kind == 'cluster' else [target] if kind == 'area' else []
        state, want, have = _hierarchy_match(places, req_locations, offered, request.get('_qualified_areas') or ()) \
            if req_locations and offered else (None, None, None)
        # The index knows nothing about the listing ("Exact location not recognised"): the parsed places decide.
        index_doubt = proximity['kind'] == 'unknown' and not (state == 'inside' and proximity['reason'] == 'Exact location not recognised')
        if index_doubt and not strict:
            located.append(NEEDS + proximity['reason'][0].lower() + proximity['reason'][1:])
            if proximity.get('area') and proximity['reason'].startswith('Same development'):
                loc = max(loc, 85.0)
            elif proximity.get('area') and request.get('_qualified_areas'):
                # v4.6: "Graha Famili Surabaya" and a listing area near it keeps the score the city line gave it before.
                loc = max(loc, 65.0)
        if state == 'inside':
            if proximity['kind'] != 'unknown' or not proximity.get('area'):
                loc = 100.0
            location_reason = ('Same location: ' + _title(want)) if want == places.canonical(have) else \
                f'Inside {_title(want)}: {_title(have)}'
            # "Pakuwon Indah Pallazate" vs a listing in Villa Valencia: the cluster the buyer named is not confirmed.
            wanted_ids = request.get('_geo_ids') or []
            kind, target = index.scope(wanted_ids) if location_index else ('none', None)
            unknown_cluster = _unknown_cluster(index, request) if location_index else None
            development = index.area_chain(index.area_of[target]) if kind == 'cluster' and target in index.area_of else ()
            if places.canonical(want) in development and target not in (listing.get('_geo_ids') or []) and not _named(
                    re.sub(r'\s*\([^)]*\)', '', index.clusters[target]['name']).lower(), text, ls['squeezed']):
                loc = min(loc, 85.0)
                located.append(NEEDS + f"exact cluster not confirmed (buyer named {index.clusters[target]['name']})")
            elif unknown_cluster and not re.search(r'(?<!\w)' + re.escape(unknown_cluster[1]) + r'(?!\w)', text):
                loc = min(loc, 85.0)
                located.append(NEEDS + 'exact cluster not confirmed (buyer named '
                               f'{index.area_label(unknown_cluster[0])} {_title(unknown_cluster[1])})')
            if want in ROOT_PLACES and not strict:
                # A city alone rarely captures what the buyer wants; keep it out of HOT.
                loc = min(loc, 90.0)
                # Unknown fragments of an area line ("Sby-Gresik-Sidoarjo", "bisa akses tronton") are no places.
                if {places.canonical(p) for p in req_locations if places.canonical(p) in places.known} - ROOT_PLACES:
                    located.append(NEEDS + 'exact location not confirmed')
                else:
                    unknown.append(NEEDS + 'buyer named only the city; exact area not confirmed')
        elif state == 'general' and not strict:
            # Only a broader area matches (e.g. listing in Surabaya Timur, buyer wants ITS).
            specific = [places.canonical(p) for p in req_locations if places.canonical(p) in places.known
                        and places.canonical(p) not in SURABAYA_REGIONS and places.canonical(p) not in ROOT_PLACES]
            placed = [h for h in offered if places.canonical(h) in places.known and places.kecamatan(h)]
            if specific and placed and not proximity.get('area') and all(places.far_apart(h, specific) for h in placed):
                # v4.8: "Karang Empat, Karang Asem dsk" (Tambaksari) and a listing in Medayu (Rungkut): far away.
                return None
            loc = 65.0
            located.append(NEEDS + 'exact location not confirmed')
        elif proximity['kind'] == 'unknown' and not strict and not located:
            located.append(NEEDS + 'requested location not confirmed')
        elif proximity['kind'] == 'unindexed' and not strict:
            later = False
            if req_locations and evidence and loc == 0:
                if not location_score(req_locations, locations):
                    return None
                # Only a place named later in the message matches (a second property, a landmark).
                loc, later = 50.0, True
                located.append(NEEDS + 'requested location is only mentioned later in the message')
            regions = {'surabaya barat', 'surabaya timur', 'surabaya utara', 'surabaya selatan', 'surabaya pusat'}
            specific = [v for v in req_locations if v != 'surabaya' and v not in regions]
            # A shared parent word is not proof of a requested neighbourhood/street.
            specific = [v for v in specific if not any(v != other and v in other for other in specific)]
            available_specific = [v for v in evidence if not any(v != other and v in other for other in evidence)]
            if not specific:
                specific = [v for v in req_locations if v != 'surabaya']
            common = set(req_locations) & set(evidence)
            if common and common <= ROOT_PLACES:
                # Only the city matches: never HOT on a city name alone.
                loc = min(loc, 90.0)
                if set(req_locations) <= ROOT_PLACES:
                    unknown.append(NEEDS + 'buyer named only the city; exact area not confirmed')
            only_city = bool(common) and set(req_locations) <= ROOT_PLACES
            if not later and not only_city and (not req_locations or not evidence or not set(specific or req_locations).intersection(evidence)):
                located.append(NEEDS + 'requested location not confirmed')
            if specific and not set(specific).intersection(available_specific):
                located.append(NEEDS + 'specific area/address not confirmed')
    if strict and strict_state != 'inside':
        loc = max(loc, 50.0)
    if building == 'proven':
        # "Apart. Denver/Cornell": the building the buyer named is the location evidence (v4.8: full credit, it is the
        # buyer's level of detail).
        located = [line for line in located if 'cluster' in line]
        if not location_reason:
            loc = max(loc, 100.0)
            location_reason = 'Same building: ' + _either(in_building)
    elif building == 'unconfirmed':
        shown = {n for n in rq['buildings'] if not (' ' in n and n.split()[-1] in rq['buildings'])} or set(rq['known_buildings'])
        located.append(NEEDS + 'apartment name not stated — buyer asked for ' + _either(shown))
    if rq['citraland_utama'] and 'citraland utama' not in text and not (location_reason and any(
            'citraland' not in p and any(places.relation(e, p) == 'inside' for e in evidence) for p in req_locations)):
        # "Citraland Utama / Graha Natura": a listing in Graha Natura needs no Citraland Utama boundary check.
        located.append(NEEDS + 'Citraland Utama boundary not confirmed')
    named_elsewhere = _unknown_places(request, places) if not strict and req_locations else None
    specific_hit = strict_state == 'inside' or building == 'proven' or proximity['kind'] == 'exact' or (
        state == 'inside' and want is not None and want not in SURABAYA_REGIONS and want not in ROOT_PLACES)
    if named_elsewhere and not specific_hit and not all(_word_spans(w, ls['place_text']) for w in named_elsewhere.replace(',', ' ').split()):
        # v4.8: the buyer named a place the place list does not know ("karang empat karang asem"); a listing that only
        # matches the region or city is not confirmed at the buyer's level.
        located.append(NEEDS + f'exact location not confirmed (buyer named “{named_elsewhere}”)')
    for place, part in _area_parts(request, places):
        # "Citraland Depan": a listing elsewhere in CitraLand does not show it is in the front part.
        inside = any(places.canonical(e) == place or places.relation(e, place) == 'inside' for e in evidence) or (
            location_reason is not None and _title(place).lower() in location_reason.lower())
        name = re.escape(place).replace(r'\ ', r'[\s-]*')
        if inside and not re.search(rf'{name}[ \t,-]+(?:cluster[ \t]+)?{part}\b|\b(?:cluster|klaster|bagian|posisi|area)[ \t]+{part}\b'
                                    rf'|\b{part}[ \t]+{name}', ls['place_text']):
            located.append(NEEDS + f'exact location not confirmed (buyer named {_title(place)} {_title(part)})')
    custom += located
    preferred = [places.canonical(p) for p in request.get('_preferred_places') or []]
    if preferred:
        # v4.8: a preferred place never makes a pair Hot unless the listing is in it.
        offered_places = {places.canonical(p) for p in evidence}
        for ident in listing.get('_geo_ids') or []:
            if ident in index.clusters:
                offered_places.update(places.canonical(x) for x in index.cluster_places(ident))
        if not any(w in offered_places or any(o in places.known and places.relation(o, w) == 'inside' for o in offered_places)
                   for w in preferred):
            unknown.append(NEEDS + 'preferred area not confirmed (' + ', '.join(_title(w) for w in preferred[:3]) + ')')
    if location_reason:
        reasons.insert(0, location_reason)
    values = {'location': loc, 'semantic': semantic,
              'data_quality': (float(request.get('extraction_confidence', 0)) + float(listing.get('extraction_confidence', 0))) * 50}
    active = {'location', 'semantic', 'data_quality'}
    details, held = [], []
    for prefix, factor, label in [('land_area', 'land', 'Land area'), ('building_area', 'building', 'Building area')]:
        need, have = _range(request, prefix), _range(listing, prefix)
        if need == (None, None):
            values[factor] = 0.0
            details.append(label + ': not requested (no score)')
            continue
        active.add(factor)
        # R7: Hot = [a, b]; Warm = [a × (1 − below %), b × (1 + above %)]; a hard minimum is never loosened.
        hard_min = rq['hard_min'].get(factor)
        value, side = band(need, have, *tolerances(settings, factor), hard_min=hard_min)
        if value == 0:
            return None
        values[factor] = value
        details.append(f'{label}: {value:.0f}%')
        if side == 'missing':
            if ls[factor + '_text'] and not (factor == 'land' and categories <= _FLOOR_UNITS):
                held.append(NEEDS + label.lower() + ' could not be read; pair held back')
            else:
                unknown.append(NEEDS + label.lower() + ' not stated in listing')
        elif side != 'inside':
            lo, hi = (float(v) if v is not None else None for v in have)
            offered = (hi if hi is not None else lo) if side == 'below' else (lo if lo is not None else hi)
            unknown.append(NEEDS + f"{label.lower()} {offered:g} m² is {side} the requested {_area_text(need)} (within tolerance)")
    if deal is None:
        # Neither side states the transaction: a yearly/monthly price can only be a rent.
        deal = 'rent' if request.get('price_basis') in PERIODS or listing.get('price_basis') in PERIODS else 'sale'
    deals = [deal] if deal != 'both' else ['sale', 'rent']
    best, rejected = None, False
    # "Budget 2,5-3 M atau 9 jt/m²": the buyer's other budget (per m² vs total) is an alternative; totals are compared
    # with totals and prices per m² with prices per m² (after the R5 conversions), never one read as the other.
    variants = (request, rq['alt_budget']) if rq['alt_budget'] else (request,)
    for option in deals:
        for variant in variants:
            result = _assess_price(variant, listing, option, settings, req_text, categories)
            rejected = rejected or result is None
            if result is not None and (best is None or result[0] > best[0]):
                best = result
    if best is None:
        return None
    price_value, price_detail, price_reasons, price_unknown, price_active = best
    # The budgeted purpose rules the pair out on price; the other purpose has no budget to check it against.
    unchecked_fallback = rejected and not price_active
    values['price'] = price_value
    if price_active:
        active.add('price')
    details.append(price_detail)
    reasons += price_reasons
    if ls['price_text'] and NEEDS + 'listing price not stated' in price_unknown:
        # "RP 6,9 MILLIARD NEGO" that the parser could not read is no price "not stated": hold it back.
        price_unknown = [u for u in price_unknown if u != NEEDS + 'listing price not stated']
        held.append(NEEDS + 'listing price could not be read; pair held back')
    unknown += price_unknown
    if deal == 'rent' and listing.get('transaction_type') == 'both' and _range(listing, 'rent_price') == (None, None) \
            and listing.get('price_basis') not in PERIODS:
        # "*LIST JUAL & SEWA*" over a unit with only a sale price: the rent offer itself is not proven.
        custom.append(NEEDS + 'rent offer not confirmed (listing states only a sale price)')
    weight = sum(float(settings[k + '_weight_pct']) for k in active)
    score = sum(values[k] * float(settings[k + '_weight_pct']) for k in active) / weight if weight else 0
    if not rq['budget_text'] and not rq['alt_budget'] and all(request.get(k) is None for k in
                                                         ('price_min', 'price_max', 'rent_price_min', 'rent_price_max')):
        # v4.7: a buyer without a readable budget is never Hot.
        unknown.append(NEEDS + 'buyer budget not stated')
    stated = rq['details']
    if sum(stated.values()) < HOT_MIN_BUYER_DETAILS:
        missing = [k for k in _KEY_DETAILS if not stated[k]]
        unknown.append(NEEDS + f'buyer request has only {sum(stated.values())} of {HOT_MIN_BUYER_DETAILS} required details'
                       + (' (missing ' + ', '.join(missing) + ')' if missing else ''))
    if rq['budget_text'] and request.get('price_min') is None \
            and request.get('price_max') is None and request.get('rent_price_min') is None and request.get('rent_price_max') is None:
        unknown.append(NEEDS + 'budget amount could not be read; pair held back')
        score = min(score, 59.0)
    if ls['bundled']:
        unknown.append(NEEDS + 'several listings in one message; held back until each property is separated')
        score = min(score, 59.0)
    if ls['offers'] or held:
        unknown += held
        score = min(score, 59.0)
    if unchecked_fallback:
        unknown.append(NEEDS + 'price fits neither stated budget; pair held back')
        score = min(score, 59.0)
    pre_cap = score
    if strict and strict_state != 'inside':
        # R9 (v4.1): everything else may match, but the location the buyer insists on is unproven.
        custom.insert(0, 'Location not confirmed — buyer only accepts ' + ', '.join(_title(p) for p in strict))
    unknown = list(dict.fromkeys(unknown))
    custom = list(dict.fromkeys(custom))
    unknown = [line for line in unknown if line not in custom]
    # v4.8: Warm only differs from Hot in price and/or land/building size inside the Warm allowance; anything else that
    # needs a check (missing or unverifiable information) is Custom. Hot needs a score of at least HOT_MIN_SCORE.
    deviations = [line for line in unknown if _DEVIATION.match(line)]
    unclear = [line for line in unknown if line not in deviations]
    status = 'custom' if custom or unclear else 'warm' if deviations or score < HOT_MIN_SCORE else 'hot'
    if status != 'hot':
        score = min(score, 79.0)
    if proximity['kind'] == 'nearby' and strict_state != 'inside':
        # Preserve distance ordering within the 60–79 band, even when other
        # factors would otherwise push all nearby alternatives to the same cap.
        score = min(score, 79.0 - proximity['distance_km'] * 3)
    summary = ['Asset category matches: ' + ', '.join(CATEGORY_LABELS.get(c, c).lower() for c in sorted(categories)), f'Location {loc:.0f}%']
    explanation = lead + list(dict.fromkeys(reasons)) + custom + unclear + deviations + summary + details
    return {'score': round(score, 2), **values, 'status': status, 'pre_cap_score': round(pre_cap, 2), 'explanation': explanation}


# ---------------------------------------------------------------- evidence (PDF highlights)
# The words in each message that back a positive reason of a pair, so a reader of the PDF sees at a glance why the two
# messages fit. Only factors that matched are marked: a "Needs check" item never highlights anything. Spans are found
# with the parser's own patterns (purpose words, categories, legal titles, unit types, towers, floors, prices, areas) and
# its place recogniser, in the text exactly as the PDF prints it; the sender's signature is never marked.
import parser as _parser
from locations import _STREET_PREFIX, default_places as _default_places

_POSITIVE_PURPOSE = re.compile(r'^Purpose: (buy or rent|buy|rent) ✓$')
_POSITIVE_UNIT = re.compile(r'^Unit: (.+) ✓$')
_POSITIVE_PLACE = re.compile(r'^(?:Same (?:location|cluster|area|building)|Inside requested area|Inside ([^:]+)): (.+)$')
_POSITIVE_LEGAL = re.compile(r'^Legal status: (.+) ✓$')
_POSITIVE_UNIT_TYPE = re.compile(r'^Unit type: (.+) ✓$')
_POSITIVE_TOWER = re.compile(r'^(Tower|Blok) (.+) ✓$')
_POSITIVE_AREA = re.compile(r'^(Land|Building) area: (\d+(?:\.\d+)?)%$')
_CATEGORY_KEYS = {label.lower(): key for key, label in CATEGORY_LABELS.items()}
_LEGAL_KEYS = {label.lower(): key for key, label in LEGAL_LABELS.items()}
_UNIT_TYPE_KEYS = {label.lower(): key for key, label in UNIT_TYPE_LABELS.items()}
# The word of a category inside a longer category match ("jual tanah" → "tanah").
_CATEGORY_WORDS = {key: re.compile(pattern) for key, pattern in {
    'warehouse': r'gudang|warehouse', 'factory': r'pabrik|factory|industri(?:al)?', 'shophouse': r'ruko|rumah\s+usaha',
    'villa': r'villa?', 'land': r'tanah|kav(?:ling)?|lahan', 'apartment': r'apart\w*|[ck]ondo\w*|penthouse',
    'office': r'(?:per)?kantor(?:an)?|office', 'hotel': r'hotel', 'house': r'rumah|hunian|house',
    'commercial_building': r'bangunan komersial|gedung'}.items()}
_FLOOR_EVIDENCE = [_parser._FLOOR_PATTERNS[True][0], _parser._FLOOR_PATTERNS[True][2],
                   *(pattern for pattern, _ in _parser._FLOOR_PATTERNS[True][1]), *(pattern for pattern, _ in _parser._FLOOR_WORDS)]
_DIRECTION_WORDS = re.compile(r'(?:utara|selatan|timur|barat|north|south|east|west)(?:[ \t]*(?:laut|daya))?')
_BEDROOMS = re.compile(r'\b(?:kt|kamar[ \t]*tidur|kmr|bedrooms?|br)[ \t]*[:=.]?[ \t]*\d\b'
                       r'|(?<![\d.,])\d[ \t]*(?:\+[ \t]*\d[ \t]*)?(?:kt|kamar(?:[ \t]*tidur)?|kmr|bedrooms?|br)\b(?![ \t]*mandi)')
_STOREYS = re.compile(r'(?<![\d.,])\d(?:[.,]5)?[ \t]*(?:lantai|lt|tingkat|storeys?|floors?)\b|\b(?:tingkat|j(?:umla|ml)h?[ \t]+lantai)[ \t]*[:=]?[ \t]*\d\b')
_PRICE_LABEL = re.compile(r'(?:harga(?:[ \t]+(?:jual|sewa))?|hrg|budget|budged|budjet|buget|bujet|bajet|price|rp\.?|idr|sewa|jual|'
                          r'under|max|maks(?:imal)?)[ \t]*[:=.]?[ \t]*$')
_PRICE_RANGE_BEFORE = re.compile(r'(?:rp\.?[ \t]*)?\d+(?:[.,]\d+)*[ \t]*(?:jt|juta|m|milyar|miliar)?[ \t]*(?:-|–|~|s/?d|sampai|hingga)[ \t]*$')
_AREA_VALUE = re.compile(r'\d[\d.,]*(?:[ \t]*(?:m2|m²|meter|mtr|m\b|ha\b))?')


def _lowered(text):
    """Lower case with the same length, so a span found in it is the same span of the printed text."""
    return ''.join(char.lower() if len(char.lower()) == 1 else char for char in text)


def _names(value):
    """"A, B or C" → ['A', 'B', 'C']."""
    return [part.strip() for part in re.split(r',\s*|\s+or\s+', value) if part.strip()]


def _literal(name):
    words = [re.escape(word) for word in re.split(r'[\s.\-]+', name.lower()) if word]
    return re.compile(r'(?<!\w)' + r'[\s.\-]*'.join(words) + r'(?!\w)') if words else None


def _found(patterns, low, keep=None):
    spans = []
    for pattern in patterns:
        for m in pattern.finditer(low):
            if m.end() > m.start() and (keep is None or keep(m)):
                spans.append((m.start(), m.end()))
    return spans


def _within(m, words):
    """The span of the category word inside a longer category match."""
    inner = words.search(m.group()) if words else None
    return (m.start() + inner.start(), m.start() + inner.end()) if inner else (m.start(), m.end())


def _price_spans(low):
    """Amounts on price lines ("Harga 1,7 M", "budget 2M", "Rp 8,5 jt/m2"), with the label before them."""
    spans, offset = [], 0
    for line in low.split('\n'):
        if _parser._PRICE_LINE.search(line):
            for m in _parser._MONEY_RE.finditer(line):
                amount = re.sub(r'\D', '', m.group(1))
                if not (m.group(2) or m.group().startswith('rp') or (len(amount) >= 7 and not re.match(r'(?:62|0)8', amount))):
                    continue  # "LT 120": a number without a money unit; "0812…": a phone number
                start, end = m.start(), m.end()
                if m.group(3):
                    end = m.start(3)  # "1 M-an" keeps only the amount
                before = _PRICE_RANGE_BEFORE.search(line[max(0, start - 30):start])
                if before:
                    start -= len(before.group())
                label = _PRICE_LABEL.search(line[max(0, start - 22):start])
                if label:
                    start -= len(label.group())
                for suffix in (_parser._PER_M2_AFTER, _parser._YEAR_AFTER, _parser._MONTH_AFTER):
                    after = suffix.match(line, end)
                    if after:
                        end = after.end()
                spans.append((offset + start, offset + len(line[:end].rstrip())))
        offset += len(line) + 1
    return spans


def _area_spans(low, kind):
    """"LT 120", "Luas tanah: 82,5 m²", "LT/LB 100/80": the label and its first value."""
    spans = []
    for m in _parser._AREA_RES[kind].finditer(low):
        value = _AREA_VALUE.search(m.group(1))
        if value:
            spans.append((m.start(), m.start(1) + value.end()))
    spans += _found([_parser._AREA_PAIR], low)
    return spans


def _place_spans(low, names, places):
    """The words that name a matched place: the place itself, a place inside it, or an alias of either."""
    wanted = {places.canonical(name) for name in names}
    spans = []
    for m in places.pattern.finditer(low):
        place = places.canonical(m.group())
        if place in ROOT_PLACES and place != 'surabaya' and _STREET_PREFIX.search(low[max(0, m.start() - 8):m.start()]):
            continue  # "Jl. Bali" is a street, not the city (as Places.recognize reads it)
        if place in wanted or any(places.relation(place, target) == 'inside' for target in wanted):
            spans.append((m.start(), m.end()))
    lowered = {name.lower() for name in names}
    spellings = list(names) + [alias for alias, building in _parser._APARTMENT_SPELLINGS.items() if building in lowered]
    spans += _found([p for p in map(_literal, spellings) if p], low)
    return spans


_AGENCY_LINE = re.compile(r'\b(?:ray\s*white|raywhite|propnex|era|century\s*21|c21|harcourts|brighton|xavier\s*marks|xmarks|'
                          r'lj\s*hooker|coldwell|winmax|realty|realtor|agency)\b', re.I)


def _signature_start(text):
    """Where the sender's signature (name, phone, agency) starts in a printed message, else its end."""
    try:
        contact = split_contact(text)[1]
    except Exception:  # never let a highlight break the report
        return len(text)
    index = text.rfind(contact) if contact else -1
    if index <= 0:
        return len(text)
    # "Ray White Citraland" right above "Hub Sari 0812…" belongs to the signature, not to the property.
    while index > 0:
        start = text.rfind('\n', 0, index - 1) + 1
        line = text[start:index].strip()
        if not line or len(line) > 60 or re.search(r'\d', line) or not _AGENCY_LINE.search(line):
            break
        index = start
    return index


def _merge(spans, limit):
    merged = []
    for start, end in sorted((max(0, s), min(e, limit)) for s, e in spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def evidence_spans(request, listing, explanation, request_text=None, listing_text=None, places=None):
    """{'request': [(start, end)], 'listing': [(start, end)]}: the words in each message that support a positive reason
    of the pair (``explanation`` as assess_pair returns it). Spans index ``request_text`` / ``listing_text`` (default:
    each row's part text or whole message); nothing behind a "Needs check" item and nothing in a signature is marked."""
    places = places or _default_places()
    lines = [str(line).strip() for line in explanation or [] if line]
    checks = ' '.join(line.lower() for line in lines if line.startswith(NEEDS))
    sides = {'request': (request or {}, request_text), 'listing': (listing or {}, listing_text)}
    out = {}
    for side, (row, text) in sides.items():
        if text is None:
            text = row.get('segment_text') or row.get('raw_text') or row.get('normalized_text') or ''
        low, spans, listing_side = _lowered(text), [], side == 'listing'
        for line in lines:
            if line.startswith(NEEDS):
                continue
            if m := _POSITIVE_PURPOSE.match(line):
                purposes = {'buy or rent': ('sale', 'rent')}.get(m.group(1), ('sale' if m.group(1) == 'buy' else 'rent',))
                for purpose in purposes:
                    if listing_side:
                        patterns, negated = [_parser._SALE_OFFER if purpose == 'sale' else _parser._RENT_OFFER], _parser._RENT_NOT_OFFER
                    else:
                        patterns = [_parser._REQ_SALE, _parser._REQ_WEAK_SALE] if purpose == 'sale' else [_parser._REQ_RENT]
                        patterns, negated = patterns + [_parser._SEEK], _parser._REQ_RENT_NOT
                    refused = [(n.start(), n.end()) for n in negated.finditer(low)]
                    spans += [s for s in _found(patterns, low) if not any(a <= s[0] < b for a, b in refused)]
            elif m := _POSITIVE_UNIT.match(line):
                for label in _names(m.group(1)):
                    key = _CATEGORY_KEYS.get(label.lower())
                    for pattern in _parser._CATEGORY_RES.get(key, ()):
                        spans += [_within(found, _CATEGORY_WORDS.get(key)) for found in pattern.finditer(low)]
            elif m := _POSITIVE_PLACE.match(line):
                names = ([m.group(1)] if m.group(1) else []) + _names(m.group(2))
                spans += _place_spans(low, names, places)
            elif m := _POSITIVE_LEGAL.match(line):
                keys = {_LEGAL_KEYS.get(label.lower()) for label in _names(m.group(1))}
                keys |= {'shm'} if 'strata' in keys else set()  # an apartment's "SHM" is its strata title
                spans += _found([pattern for key, pattern in _parser._LEGAL_RES if key in keys], low)
            elif m := _POSITIVE_UNIT_TYPE.match(line):
                keys = {_UNIT_TYPE_KEYS.get(label.lower()) for label in _names(m.group(1))}
                patterns = [_parser._UNIT_COUNT, _parser._UNIT_RANGE, _parser._UNIT_MIN, _parser._UNIT_AFTER, _parser._STUDIO, _parser._PENTHOUSE]
                spans += _found(patterns, low, keep=lambda found: bool(unit_types(found.group()) & keys))
            elif m := _POSITIVE_TOWER.match(line):
                names = {name.lower() for name in _names(m.group(2))}
                if m.group(1) == 'Tower':
                    lists = [_parser._TOWER_LIST, _parser._TOWER_NAMED]
                else:
                    lists = [_parser._BLOCK_LIST]
                spans += _found(lists, low, keep=lambda found: any(re.search(r'(?<![a-z])' + re.escape(n) + r'(?![a-z])', found.group())
                                                                   for n in names))
                spans += _found([p for p in map(_literal, [n for n in names if len(n) >= 3]) if p], low)
            elif re.match(r'^Floor (?:level|\S+) ✓$', line):
                spans += _found(_FLOOR_EVIDENCE, low)
            elif line.startswith('Financing: ') and line.endswith('possible ✓'):
                spans += _found([_parser._MORTGAGE_OK, _parser._MORTGAGE], low)
            elif line == 'Facing matches':
                for found in _parser._FACING_PHRASE.finditer(low):
                    direction = _DIRECTION_WORDS.search(found.group(1))
                    if direction:
                        spans.append((found.start(), found.start(1) + direction.end()))
            elif line.startswith('Lease term fits'):
                spans += _found([_parser._LEASE_MIN, _parser._LEASE_NEED], low)
            elif line == 'Bedrooms match':
                spans += _found([_BEDROOMS], low)
            elif line == 'Floors match':
                spans += _found([_STOREYS], low)
            elif line in ('Width matches', 'Road width (ROW) matches'):
                spans += _found([pattern for label, pattern in _SIZE_PATTERNS if line.startswith(label)], low)
            elif line.endswith('within budget'):
                spans += _price_spans(low)
            elif m := _POSITIVE_AREA.match(line):
                kind = m.group(1).lower()
                if float(m.group(2)) > 0 and f'{kind} area' not in checks:
                    spans += _area_spans(low, 'land' if kind == 'land' else 'building')
        out[side] = _merge(spans, _signature_start(text))
    return out
