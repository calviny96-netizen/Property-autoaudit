import collections
import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from decimal import Decimal, InvalidOperation

from locations import (DEFAULT_GLOSSARY, DEFAULT_LOCATION_ALIASES, DEFAULT_LOCATION_PARENTS, GENERIC_PLACE_WORDS,
                       KNOWN_LOCATIONS, ROOT_PLACES, SURABAYA_REGIONS, Places, default_places, expand_place_suffixes,
                       glossary_entries)


CATEGORY_PATTERNS = {
    "warehouse": (r"\bgudang\b", r"warehouse"),
    # "Tanah industri", "lahan zona industri", "gudang industri", "kawasan industri" name the zone, not a factory.
    "factory": (r"\bpabrik\b", r"factory",
                r"(?<!tanah )(?<!lahan )(?<!kavling )(?<!kav )(?<!plot )(?<!zona )(?<!zoning )(?<!kawasan )(?<!area )(?<!gudang )"
                r"(?<!pergudangan )(?<!warehouse )(?<!estate )(?<!rungkut )(?<!untuk )(?<!utk )(?<!buat )"
                r"\bindustri(?:al)?\b(?![ \t]+(?:warehouse|estate|park|zone|area|kavling))"),
    "shophouse": (r"\bruko\b", r"rumah\s+usaha"),
    "villa": (r"\bvilla?\b",),
    # "Ada lahan parkir(an)", "lahan parkir luas" describe parking, never a plot of land.
    "land": (r"\bkav(?:ling)?\b", r"\blahan\b(?![ \t]*(?:untuk[ \t]+|utk[ \t]+|buat[ \t]+)?parkir)", r"tanah\s+kosong", r"(?:jual|beli|cari|hanya|sewa)\s+tanah", r"hitung\s+tanah",
             r"^\W*(?:\d{1,2}\s*[.)]?\W*)?(?:(?:(?:di\s*)?(?:jual|sewa)\w*|for\s+(?:sale|rent|lease))\W+)?(?:(?!luas|harga|lt)[a-z]+\W+)?tanah\b"
             r"(?!\s+(?:dan|&)\s+bangunan)"),
    "apartment": (r"apart(?:e)?men", r"\bapartment\b", r"\bapart\b", r"\b[ck]ondominium\b", r"\bpenthouse\b", r"\b[ck]ondo\b"),
    "office": (r"\b(?:per)?kantor(?:an)?\b", r"office"),
    "hotel": (r"\bhotel\b",),
    # v4.5: a hospital, restaurant or place of worship ("rumah sakit", "rumah makan") is no house.
    "house": (r"\brumah\b(?![ \t]+(?:sakit|makan|ibadah|duka|potong|walet|bersalin)\b)", r"hunian",
              r"(?<!club )(?<!guest )\bhouse\b"),
    "commercial_building": (r"bangunan komersial", r"gedung"),
}
CATEGORY_LABELS = {
    "warehouse": "Warehouse", "factory": "Factory", "shophouse": "Shophouse", "villa": "Villa", "land": "Land",
    "apartment": "Apartment", "office": "Office", "hotel": "Hotel", "house": "House",
    "commercial_building": "Commercial building",
}
_CATEGORY_RES = {k: tuple(re.compile(p) for p in v) for k, v in CATEGORY_PATTERNS.items()}

NOISE_EXACT = {
    "mengirim gambar", "mengirim sticker", "message is deleted by user", "mengirim message_history_notice",
}
REQUEST_MARKERS = (
    "buyer request", "buyer need", "buyer cari", "dicari beli", "dicari sewa", "cari beli", "cari sewa",
    "renter request", "request renter", "request buyer", "investor request", "request gudang",
    "buyer req", "renter req", "investor req", "hot renter", "buyer lama request", "kebutuhan buyer",
    "req buyer", "req renter", "req investor", "request investor", "buyer only", "renter only",
)
LISTING_MARKERS = (
    "dijual", "disewakan", "jual cepat", "for sale", "for rent", "listing", "harga jual",
    "sale rent", "jual sewa", "harga sewa", "for lease", "dikontrakkan", "dikontrakan", "di jual", "di sewakan",
)


@dataclass
class ParsedDocument:
    classification: str
    confidence: float
    transaction_type: str = "unknown"
    categories: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    land_area_min: float | None = None
    land_area_max: float | None = None
    building_area_min: float | None = None
    building_area_max: float | None = None
    price_min: int | None = None
    price_max: int | None = None
    price_basis: str | None = None
    rent_price_min: int | None = None
    rent_price_max: int | None = None
    rent_price_basis: str | None = None
    negotiable: bool = False
    facing: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    location_strict: bool = False
    strict_locations: list[str] = field(default_factory=list)
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_phones: list[str] = field(default_factory=list)
    normalized_text: str = ""
    # v4.2: one bubble may hold several contexts; each part is its own document (see parse_bubble).
    segment_index: int = 0
    segment_count: int = 1
    segment_text: str | None = None
    # v4.10: md5 of the normalised message content (``content_hash``); copies with the same key are one entity.
    content_key: str | None = None

    def dict(self):
        return asdict(self)


def _unmark(text):
    """Text without WhatsApp emphasis marks; a mark between a number and a word becomes a space ("*2*DIJUAL")."""
    return re.sub(r"[*_~]", "", re.sub(r"(?<=\d)[*_~]+(?=[^\W\d_])|(?<=[^\W\d_])[*_~]+(?=\d)", " ", text))


# v4.4: invisible format characters (Unicode Cf: zero-width space, U+2060 word joiner, BOM, bidi marks/isolates) never
# hide a word from the rules ("*HARUS HADAP BARAT / UTARA*"); soft hyphens, joiners and emoji tags join, the rest space.
_FORMAT_JOIN = re.compile("[\u00ad\u200c\u200d\U000e0001\U000e0020-\U000e007f]")
_FORMAT_SPACE = re.compile("[\u180e\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]")


# v4.6: a fraction is a decimal ("1½ lantai" = 1,5 storeys, "13½ × 15", "Rp 1¼ M"); NFKC alone writes "1½" as "11⁄2".
_VULGAR = re.compile(r"(?:(\d+)[ \t]?)?([¼-¾⅐-⅞])")
_FRACTION_SLASH = re.compile(r"(?:(?<![\d.,])(\d+)[ \t]+)?(\d+)⁄(\d+)")
# "2 1/2 lantai", "3 1/2 lt", "1,1/2 lantai": an ASCII half storey (only before a storey word: "1/2" alone is ambiguous).
_ASCII_STOREY = re.compile(r"(?<![\d.,/])(\d{1,2})(?:[ \t]+|[ \t]*,[ \t]*)([13])/([24])(?![\d/])"
                           r"(?=[ \t]*(?:lantai|lntai|lt|tingkat|storeys?|floors?)\b)", re.I)


def _decimal(value):
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _slashed(m):
    whole, top, bottom = m.group(1), m.group(2), int(m.group(3))
    if not bottom:
        return m.group()
    if whole is None and len(top) > 1 and len(m.group(3)) == 1 and int(top[-1]) < bottom:
        whole, top = top[:-1], top[-1]  # "11⁄2" is how NFKC writes "1½"
    return _decimal(int(whole or 0) + int(top) / bottom)


def _fractions(text):
    text = _VULGAR.sub(lambda m: _decimal(int(m.group(1) or 0) + unicodedata.numeric(m.group(2))), text)
    text = _FRACTION_SLASH.sub(_slashed, text)
    return _ASCII_STOREY.sub(lambda m: _decimal(int(m.group(1)) + int(m.group(2)) / int(m.group(3)))
                             if int(m.group(2)) < int(m.group(3)) else m.group(), text)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", _fractions(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = _FORMAT_SPACE.sub(" ", _FORMAT_JOIN.sub("", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", clean_text(text).lower())


def _hash_text(text):
    """The normalisation message hashes were computed with (kept as it was so re-uploads stay duplicates)."""
    text = unicodedata.normalize("NFKC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", " ").replace("\ufeff", " ").replace("\u2060", " ").replace("\u200e", " ")
    return re.sub(r"\s+", " ", re.sub(r"[ \t]+", " ", text).strip().lower())


PHONE_PATTERN = r"(?:\+62[ .-]?|62|0)8[\d .-]{6,15}\d"
_PHONE_RE = re.compile(PHONE_PATTERN)


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return "62" + digits[1:] if digits.startswith("0") else digits


# --------------------------------------------------------------------------- contact / signature
# "Mohon dibantu hubungi yg bersangkutan langsung" asks to contact the poster of each item: no signature.
_CONTACT_MARKER = r"\b(?:contact|kontak|hubungi(?!\s+(?:yg|yang)\s+bersangkutan)|info\s+lanjut|marketing)\s*[:：]?"
_SIG_CORE = re.compile(r'(?im)^\s*(?:\d{1,2}\s*[.)]\s*)?[-*•>_ ]*(?:luas|lt\b|lb\b|budget|harga|hadap|cari beli|cari sewa|kamar|row\b)')
_MARKER_WORDS = (r"(?:more\s+info(?:rmation)?|for\s+more\s+info(?:rmation)?(?:\s*,?\s*(?:contact|call|hubungi))?|info(?:rmasi)?(?:\s+(?:lebih\s+lanjut|lanjut|lengkap|call|hub(?:ungi)?|"
                 r"&\s*viewing|dan\s+viewing|survey))?|hubungi(?:\s+(?:kami|saya|segera|sekarang|langsung|agent(?:nya)?(?:\s+langsung)?))?"
                 r"(?:\s+(?:sekarang|segera))?(?:\s+juga)?|"
                 r"hub(?:ungi|ung|ungin|i|gi)?|contact(?:\s+(?:person|us|me))?|kontak|cp|call(?:\s+(?:me|us))?|telp|tlp|telepon|phone|hp|"
                 r"chat(?:\s+(?:wa|whatsapp))?|wa|whatsapp|sms|marketing|langsung\s+wa|direct\s+wa)")
_MARKER_PREFIX = re.compile(r"^(?:(?:segera|silakan|silahkan|langsung|yuk|ayo|mohon|please|pls|plz|minat|survei|survey|"
                            r"&\s*kunjungan|kunjungan)\s+)*(?:" + _MARKER_WORDS +
                            r"(?![a-z])\s*(?:/\s*(?:wa|telegram|telp|tlp|sms|line|call|telepon)\s*)*[:：.\-]*\s*)+", re.I)
_MARKER_ANY = re.compile(r"(?<![a-z])" + _MARKER_WORDS + r"(?![a-z])\s*[:：]", re.I)
_NOT_NAME = re.compile(
    r"\b(?:harga|budget|budged|bugdet|luas|lt|lb|kt|km|hadap|shm|hgb|ppjb|listrik|watt|lantai|kamar|carport|garasi|"
    r"sertifikat|surat|nego|jual|dijual|sewa|disewakan|cari|dicari|request|buyer|renter|lokasi|row|jalan|dimensi|ukuran|"
    r"fasilitas|spesifikasi|spek|spec|terima\s*kasih|thanks|thank|silakan|silahkan|mohon|tolong|kirim|survey|viewing|"
    # "Please 📲" over the name: a courtesy word, never the sales person.
    r"please|pls|plz|plis|kindly|"
    r"japri|foto|http|www|instagram|telegram|maps|dropped|pin|catalog|katalog|hitung|unit|tipe|type|ready|furnish|"
    r"bisa|untuk|yang|yg|ada|dan|atau|di|ke|dari|sesuai|listing|siap|huni|baru|gress|murah|strategis|cocok|bebas|"
    r"banjir|net|nett|cash|hook|pojok|view|kolam|pool|semi|kosongan|cluster|perumahan|tower|lokasi|area|daerah|akses|"
    r"dekat|menit|selatan|utara|timur|barat|kondisi|bonus|free|gratis|promo|diskon|dp|cicil|angsuran|kpr|bank|"
    r"masih|sudah|harap|segera|dibantu|bantu|info|detail|lengkap|sekarang|juga|rumah|ruko|gudang|tanah|apartemen|apartment|"
    r"kavling|villa|kantor|gedung|pabrik|kost|kos|pajak|termasuk|ig|urgent|minim\w*|maks\w*)\b|https?:|\.com|#|@"
    r"|\b\d+\s*(?:th|thn|tahun|bl|bln|bulan)\b", re.I)
# Agency/brand words: a signature line made only of these is the office, not the sales person.
_AGENCY_WORDS = re.compile(r"\b(?:propnex|ray\s*white|raywhite|era|century\s*21|c21|harcourts|brighton|xavier\s*marks|xmarks|"
                           r"lj\s*hooker|coldwell(?:\s*banker)?|winmax|platinum|titanium|realty|realtor|real\s+estate|estate|agent|"
                           r"agen|broker|property|properti|agency|group|office|nlp|prime|five\s+stars?|db|indonesia|member|of|"
                           r"arebi|west|east|north|south|official)\b|[&|/.,()\s-]", re.I)
_TAGLINE_PHRASE = re.compile(r"\b(?:get\s+this|deliver|your\s+(?:best|real)|best\s+partner|trusted|specialist|"
                             r"sell\s*\|\s*buy|terima\s*kasih|thank)\b", re.I)
_TAGLINE = re.compile(r"[!?]|" + _TAGLINE_PHRASE.pattern, re.I)
_URL = re.compile(r"https?://\S+|wa\.me/\S+|api\.whatsapp\.com\S*|www\.\S+", re.I)


def _strip_symbols(value):
    value = _URL.sub(" ", value)
    value = re.sub(r"\b(?:wa\.?\s*me|api\.whatsapp\.com)\S*", " ", value, flags=re.I)
    value = re.sub(r"[*_~`\"“”]+", " ", value)
    # Emojis, arrows and decorative symbols are not part of a name.
    value = "".join(ch if (ch.isalnum() or ch in " &.'-|/:,()") else " " for ch in value)
    return re.sub(r"\s+", " ", value).strip()


def _agency_only(name):
    return bool(name) and not _AGENCY_WORDS.sub("", name)


@lru_cache(maxsize=16384)
def _clean_name(value):
    value = _strip_symbols(_PHONE_RE.sub(" ", value))
    previous = None
    while previous != value:
        previous = value
        value = _MARKER_PREFIX.sub("", value).strip(" :|-/.,")
    # "KIEM (LPWE)": bracketed notes are not part of the name; a stray bracket never is.
    outside = re.sub(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])", " ", value)
    value = re.sub(r"\s*[()\[\]]\s*", " ", outside if outside.strip(" :-/.,") else value)
    # "Vera Ang || PropNex", "Albert- Five Stars Property": the person comes first, the office after.
    parts = [part.strip(" :-/.,") for part in re.split(r"\s*\|+\s*|\s+[-–—]+\s*|\s*[-–—]+\s+", value) if part.strip(" :-/.,")]
    value = next((part for part in parts if not _agency_only(part)), parts[0] if parts else "")
    value = re.sub(r"\s+(?:wa|telp|tlp)$", "", value, flags=re.I).strip(" :-/.,")
    value = re.sub(r"\s+", " ", value)
    if value and value == value.lower():
        value = value.title()
    return value


def _name_like(line):
    if _TAGLINE.search(re.sub(r"[*_~]", " ", line)):
        return False
    name = _clean_name(line)
    return bool(name) and len(name) <= 50 and len(name.split()) <= 6 and not re.search(r"\d{3,}", name) \
        and not _NOT_NAME.search(name) and bool(re.search(r"[A-Za-z]{2}", name))


_SOCIAL_LINE = re.compile(r"^\W*(?:ig|instagram|tiktok|fb|facebook|web(?:site)?|line|e-?mail|youtube|yt|linktr\.?ee)\b", re.I)


_COURTESY_LINE = re.compile(r"(?:please|pls|plz|plis|kindly|silakan|silahkan|mohon|segera|yuk|ayo)(?:\s+(?:dm|chat|japri))?", re.I)


def _label_line(line):
    """Signature lines between a name and its number that are not a name: "HP/WA", "IG : x", links, taglines,
    a lone courtesy word ("Please 📲")."""
    if re.search(r"\d", line) and _PRICE_LINE.search(line.lower()):
        return False  # "💰 HARGA: Rp 3,7 M nego!" is the message, not the signature
    return _marker_only(line) or bool(_COURTESY_LINE.fullmatch(_strip_symbols(line))) or bool(_SOCIAL_LINE.match(line)) or (bool(_URL.search(line)) and not _clean_name(line)) \
        or bool(_TAGLINE_PHRASE.search(re.sub(r"[*_~]", " ", line))) or _strip_taglines(line.lower()).strip() != line.lower().strip()


def _marker_only(line):
    stripped = _strip_symbols(_PHONE_RE.sub(" ", line))
    return bool(re.match(r"^" + _MARKER_WORDS + r"(?![a-z])", stripped, re.I)) and not _clean_name(line)


def _signature_line(line):
    if not line.strip() or _PHONE_RE.search(line) or _URL.search(line):
        return True
    return len(line) <= 70 and not _SIG_CORE.search(line) and not re.search(
        r"\b(?:harga|budget|luas|lt|lb|kt|km|hadap|shm|hgb|lantai|dijual|disewakan|sewa)\b\s*[:=]?\s*\d", line, re.I)


def _contact_block(lines):
    """Return (start_line, first_phone_line, name, phones, at_bottom) of the final contact block."""
    phone_lines = [i for i, line in enumerate(lines) if _PHONE_RE.search(line)]
    if not phone_lines:
        return None
    last = phone_lines[-1]
    first = last
    for i in reversed(phone_lines[:-1]):
        between = lines[i + 1:first]
        if len(between) <= 6 and all(_signature_line(line) for line in between):
            first = i
        else:
            break
    at_bottom = all(_signature_line(line) for line in lines[last + 1:])
    head = lines[first]
    phone = _PHONE_RE.search(head)
    prefix = head[:phone.start()]
    markers = list(_MARKER_ANY.finditer(prefix)) or list(re.finditer(r"(?<![a-z])(?:hubungi|contact|kontak|more\s+info)\b", prefix, re.I))
    # Candidate names in reading order: (line index, text).
    candidates = []
    start = first
    j = first - 1
    while j >= 0 and first - j <= 5:
        line = lines[j]
        if not line.strip():
            break
        if _marker_only(line) and candidates:
            start = j  # "Info lebih lanjut" above the name: everything higher up is the message body
            break
        if _label_line(line):
            # "HP/WA", "WA :", "IG : x", links, "JUAL | BELI | SEWA | KPR", "Deliver the Best Property for You".
            start = j
            j -= 1
            continue
        if _name_like(line):
            candidates.insert(0, (j, line))
            start = j
            j -= 1
            continue
        break
    inline = prefix[markers[-1].start():] if markers else prefix
    if (markers or len(inline) <= 60) and _inline_name(inline):
        candidates.append((first, inline))
    elif markers and len(prefix[:markers[0].start()]) <= 60 and _inline_name(prefix[:markers[0].start()]):
        # "Vera Ang || PropNex  📲WA: 0812…": the name precedes the phone label.
        candidates.append((first, prefix[:markers[0].start()]))
    for k in range(first + 1, last + 1):
        line = lines[k]
        match = _PHONE_RE.search(line)
        if match and _inline_name(line[:match.start()]):
            candidates.append((k, line[:match.start()]))
        elif not match and line.strip() and _name_like(line) and k < last:
            candidates.append((k, line))
    if not candidates:
        # Some signatures put the name under the number ("wa.me/62… / Ferdy Brighton").
        candidates += [(k, line) for k, line in enumerate(lines[last + 1:last + 3], last + 1)
                       if line.strip() and _name_like(line)][:1]
    # An office line above the number ("PropNex | Platinum") gives way to a person named with a phone.
    people = [c for c in candidates if not _agency_only(_clean_name(c[1]))]
    candidates = people or candidates
    name_line, name = (candidates[0][0], _clean_name(candidates[0][1])) if candidates else (first, None)
    offset = phone.start() if markers else 0
    chosen, phones = [], []
    for k, line in enumerate(lines[first:], first):
        segment = line[offset:] if k == first else line
        found = [normalize_phone(m.group(0)) for m in _PHONE_RE.finditer(segment)]
        (chosen if k >= name_line and not chosen else phones).extend(found)
    return start, first, name or None, list(dict.fromkeys(chosen[:1] + phones + chosen[1:])), at_bottom


def _inline_name(prefix):
    if _TAGLINE.search(re.sub(r"[*_~]", " ", prefix)):
        return False
    name = _clean_name(prefix)
    return bool(name) and len(name) <= 50 and len(name.split()) <= 6 and not _NOT_NAME.search(name) \
        and bool(re.search(r"[A-Za-z]{2}", name))


def author_name(author):
    """A WhatsApp author as a display name: "~ Budi, +62 812-3456-7890" -> "Budi" (a bare number stays)."""
    text = " ".join(str(author or "").split())
    text = re.sub(r"^~\s*", "", text)
    return re.sub(r"\s*,\s*\+?\d[\d\s().-]{5,}$", "", text).strip(" ,") or text or None


def extract_contact(text):
    """Sales contact from the signature: the topmost name and that name's phone first."""
    block = _contact_block(clean_text(text).split("\n"))
    if not block:
        return None, []
    return block[2], block[3]


def strip_top_contact(text):
    """The message without a contact block written above it ("Hubungi / Fitria / Brighton Home Manyar / 0812…"
    over "DIJUAL RUMAH … RUNGKUT MAPAN"): the office's place is not where the property is."""
    lines = text.split("\n")
    phones = [i for i, line in enumerate(lines[:7]) if _PHONE_RE.search(line)]
    if not phones or not any(_HAS_WORD.search(line) for line in lines[phones[-1] + 1:]):
        return text
    head = lines[:phones[-1] + 1]
    if sum(bool(line.strip()) for line in head) > 6:
        return text

    def contact_line(line):
        if not line.strip() or _marker_only(line) or _label_line(line):
            return True
        phone = _PHONE_RE.search(line)
        rest = line[:phone.start()] if phone else line
        return not _HAS_WORD.search(_MARKER_PREFIX.sub("", _strip_symbols(rest))) or _name_like(rest) or _inline_name(rest)
    return "\n".join(lines[phones[-1] + 1:]).strip("\n") if all(contact_line(line) for line in head) else text


def split_contact(text):
    # Contact headings work both in multiline bubbles and flattened exports.
    markers = list(re.finditer(_CONTACT_MARKER, text, re.I))
    marker = next((m for m in reversed(markers) if not _SIG_CORE.search(text[m.end():])), None)
    cut = marker.start() if marker else len(text)
    if not marker:
        phones = list(_PHONE_RE.finditer(text))
        phone = phones[-1] if phones else None
        if phone and _SIG_CORE.search(text[phone.end():]):
            return re.sub(r'https?://\S+|wa\.me/\S+|' + PHONE_PATTERN, '', text), ''
        if phone:
            line_start = text.rfind("\n", 0, phone.start()) + 1
            prefix = text[line_start:phone.start()]
            if not re.search(r"\b(?:harga|budget|lt|lb|dijual|dicari)\b", prefix, re.I):
                cut = line_start
            else:
                name = re.search(r"[A-Za-z][A-Za-z ]{2,40}\s*[|:-]\s*$", prefix)
                cut = line_start + name.start() if name else phone.start()
    if not marker and cut < len(text):
        before = text[:cut].rstrip()
        paragraph_start = before.rfind('\n\n') + 2
        if paragraph_start > 1:
            block = before[paragraph_start:]
            if len(block) < 220 and not re.search(r'\b(?:harga|budge[dt]|luas|lt|lb|cari|request|rumah|ruko|tanah|apart(?:e)?men|gudang|kamar|syarat)\b'
                                                  r'|💰|\d\s*(?:jt|juta|milyar|miliar|m)\b', block, re.I):
                cut = paragraph_start
    if cut < len(text):
        # Name/agency lines directly above the signature phone belong to the signature.
        lines = text.split("\n")
        block = _contact_block(lines)
        if block and block[4] and block[0] < block[1]:
            start_offset = sum(len(line) + 1 for line in lines[:block[0]])
            if start_offset < cut and not _SIG_CORE.search(text[start_offset:]):
                cut = start_offset
    return text[:cut].strip(), text[cut:].strip()


# --------------------------------------------------------------------------- content key
# v4.10: one public ID per message *content*. The same request reposted with another signature ("Mohon infonya 🙏 /
# Hay / Xmpc / 0851…" vs "Mohon infonya hub: 0812… [ mr mario ]"), other casing ("KONIG" / "Konig"), emoji,
# punctuation or spacing is one buyer / listing. Words and numbers that carry meaning (places, prices, sizes) stay,
# so another budget or another place is another key.
_KEY_DROP = re.compile(r"https?://\S+|www\.\S+|\b(?:wa\.?\s*me|api\.whatsapp\.com)\S*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
                       r"|(?<!\w)@[^\W\d]\w*", re.I)
# A phone number, never the tail of a price ("Rp 1.080.000.000" holds "080.000.000").
_KEY_PHONE = re.compile(r"(?<![\w.,])" + PHONE_PATTERN)
_KEY_BOUNDARY = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
# Closing lines that belong to the signature even when no blank line separates them ("Mohon infonya 🙏",
# "Terima kasih", "Hub langsung ya kak"): only these words, no digits.
_CLOSING_WORDS = frozenset((
    "mohon", "minta", "tolong", "please", "pls", "plz", "plis", "kindly", "silakan", "silahkan", "yuk", "ayo", "segera",
    "info", "infonya", "informasi", "informasinya", "infokan", "bantu", "bantuan", "bantuannya", "dibantu", "share",
    "sharenya", "ya", "yah", "yaa", "ok", "oke", "kak", "ka", "kk", "pak", "bu", "bos", "boss", "gan", "sis", "bro",
    "om", "rekan", "rekan2", "teman", "teman2", "terima", "kasih", "makasih", "trims", "thanks", "thank", "you", "thx",
    "tq", "ty", "hub", "hubungi", "call", "chat", "wa", "japri", "dm", "pc", "langsung", "via", "saya", "kami",
    "salam", "closing", "sukses", "gbu", "amin"))
_KEY_WORD = re.compile(r"[^\W\d_]+|\d+")


_MASK = str.maketrans("0123456789", "".join(chr(0xE000 + i) for i in range(10)))
_UNMASK = {0xE000 + i: str(i) for i in range(10)}


def _mask_price_tails(text):
    """Digits of a "phone number" that continues a price ("Rp 1.080.000.000") hidden from ``split_contact``."""
    return _PHONE_RE.sub(lambda m: m.group().translate(_MASK) if m.start() and text[m.start() - 1] in "0123456789.," else m.group(),
                         text)


def _closing_line(line):
    words = _KEY_WORD.findall(line.casefold())
    return all(word in _CLOSING_WORDS for word in words)


def content_key(text: str | None) -> str:
    """The normalised content of a bubble or of one part of it: the text without the sales signature
    (``split_contact``; a contact block written above the message too; closing lines such as "Mohon infonya 🙏"
    right above it), URLs, wa.me links, phone numbers, e-mail addresses, @handles, emoji and symbols; NFKC,
    case-folded, punctuation and whitespace collapsed to single spaces, letters and digits split ("LT120" =
    "LT 120"). ``''`` when nothing is left."""
    cleaned = clean_text(text or "")
    if not cleaned:
        return ""
    body = split_contact(strip_top_contact(_mask_price_tails(cleaned)))[0].translate(_UNMASK) or cleaned
    lines = body.split("\n")
    while len(lines) > 1 and _closing_line(lines[-1]):
        lines.pop()
    body = "\n".join(lines)
    value = _KEY_PHONE.sub(" ", _KEY_DROP.sub(" ", body))
    value = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", value).casefold())
    value = "".join(ch if ch.isalnum() else " " for ch in value)
    return " ".join(_KEY_BOUNDARY.sub(" ", value).split())


def content_hash(text: str | None) -> str:
    """What ``xm.documents.content_key`` stores: md5 of ``content_key``; a text with no content left (only a
    signature or emoji) falls back to md5 of the text itself, the pre-v4.10 entity key."""
    key = content_key(text)
    return hashlib.md5((key if key else (text or "").strip()).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- glossary
def glossary_terms(glossary=None):
    # Descriptions explain a term; they are never evidence from a message.
    mappings = dict(DEFAULT_GLOSSARY)
    for e in glossary_entries(glossary):
        if e["kind"] != "category":
            mappings[e["term"]] = e["canonical"]
    return {k.lower(): v for k, v in mappings.items() if k and v}


class _GlossaryModel:
    def __init__(self, glossary):
        entries = glossary_entries(glossary)
        self.legacy = isinstance(glossary, dict) or not glossary
        self.rewrites = glossary_terms(glossary)
        keys = sorted(self.rewrites, key=len, reverse=True)
        self.rewrite_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)", re.I) if keys else None
        self.places = Places(entries) if any(e["kind"] == "location" for e in entries) else default_places()
        self.location_terms = {}
        self.synonym_places = []
        for e in entries:
            if e["kind"] == "location":
                self.location_terms[e["canonical"]] = e["canonical"]
            elif e["kind"] == "synonym" and not e["described"]:
                self.synonym_places.append(e["canonical"])
        self.categories = [(re.compile(r"(?<!\w)" + re.escape(e["term"]) + r"(?!\w)"), e["canonical"])
                           for e in entries if e["kind"] == "category" and e["canonical"] in CATEGORY_PATTERNS]
        legacy_places = set(KNOWN_LOCATIONS) | _EXTRA_PLACES
        user = {e["term"]: e for e in entries}
        candidates = dict.fromkeys(v for v in DEFAULT_GLOSSARY.values() if v in legacy_places)
        if self.legacy:
            # Legacy dictionaries: every plain (undescribed) company term may name a place.
            candidates.update(dict.fromkeys(v for k, v in self.rewrites.items()
                                            if v in legacy_places or (k in user and not user[k]["described"] and not _PROPERTY_TERMS.search(v))))
        else:
            candidates.update(dict.fromkeys(self.location_terms))
            candidates.update(dict.fromkeys(v for v in self.synonym_places if not _PROPERTY_TERMS.search(v)))
        names = sorted((n for n in candidates if n not in self.places.known), key=len, reverse=True)
        self.user_places_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)") if names else None

    def user_places(self, text):
        if not self.user_places_re:
            return []
        return list(dict.fromkeys(m.group() for m in self.user_places_re.finditer(text)))


_MODEL_CACHE = {}


_DEFAULT_MODEL = []


def _glossary_model(glossary):
    """Compiled glossary, cached per glossary object (callers reuse one object per run)."""
    if not glossary:
        if not _DEFAULT_MODEL:
            _DEFAULT_MODEL.append(_GlossaryModel(None))
        return _DEFAULT_MODEL[0]
    cached = _MODEL_CACHE.get(id(glossary))
    if cached and cached[0] is glossary and cached[1] == len(glossary):
        return cached[2]
    model = _GlossaryModel(glossary)
    if len(_MODEL_CACHE) > 16:
        _MODEL_CACHE.clear()
    # Keeping a reference to the glossary object prevents id() reuse while cached.
    _MODEL_CACHE[id(glossary)] = (glossary, len(glossary), model)
    return model


def apply_glossary(text, glossary=None):
    model = _glossary_model(glossary)
    if not model.rewrite_re:
        return text
    return model.rewrite_re.sub(lambda m: model.rewrites[m.group(0).lower()], text)


def location_ancestors(place, glossary=None):
    """Parent chain of a place: built-in Surabaya hierarchy plus company 'location' glossary entries."""
    return list(_glossary_model(glossary).places.ancestors(place))


def default_glossary_entries():
    """Read-only built-in entries (synonyms and the location hierarchy) for the glossary editor."""
    entries = [{"term": k, "canonical": v, "kind": "synonym", "parent": None} for k, v in DEFAULT_GLOSSARY.items()]
    entries += [{"term": child, "canonical": child, "kind": "location", "parent": parent}
                for child, parent in DEFAULT_LOCATION_PARENTS.items() if child not in DEFAULT_LOCATION_ALIASES]
    entries += [{"term": alias, "canonical": canonical, "kind": "location", "parent": DEFAULT_LOCATION_PARENTS.get(canonical)}
                for alias, canonical in DEFAULT_LOCATION_ALIASES.items()]
    return entries


def message_hash(chat_id: str, timestamp: str, author: str, text: str) -> str:
    value = "|".join((chat_id, timestamp, author, _hash_text(text)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- numbers and areas
def _number(value: str) -> float:
    raw = value = value.strip().replace(" ", "")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        tail = value.rsplit(",", 1)[1]
        value = value.replace(".", "").replace(",", "." if len(tail) <= 2 else "")
    elif value.count(".") == 1 and len(value.rsplit(".", 1)[1]) == 3:
        value = value.replace(".", "")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    try:
        return float(value)
    except ValueError:
        # Typos such as "1,2,5 M" or "1,5.0,0": the first separator is the decimal point.
        head, _, tail = re.sub(r"[^\d.,]", "", raw).partition(next((c for c in raw if c in ".,"), "."))
        return float((re.sub(r"\D", "", head) or "0") + "." + (re.sub(r"\D", "", tail) or "0"))


_HECTARE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:ha|hektar|hektare|hectare)\b")


def _hectares(value):
    """Hectares are small numbers: one separator is the decimal point ("2,2220 ha" = 2.222 ha, "LT 1.150 Ha (80x132)" =
    1.15 ha), never thousands (1150 ha)."""
    if len(re.findall(r"[.,]", value)) == 1:
        return float(value.replace(",", "."))
    return _number(value)


def _plain(value):
    """A number written without exponent or thousands separators ("12000", "2.5")."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


_BRACKET_LIMIT = re.compile(r"\((?=[^()]*\b(?:min(?:imal|im)?|max|maks(?:imal)?|paling|kurang|bawah)\b)[^()]*\)")


def _range_from_line(line: str) -> tuple[float | None, float | None]:
    # "LT 2500-3500 (paling min 2300 ga mau dibawah itu)": the stated range wins over the remark in brackets
    # (matching_rules reads such a remark as a hard minimum).
    if "(" in line:
        outside = _BRACKET_LIMIT.sub(" ", line)
        if outside != line and _DIGIT.search(outside):
            found = _range_from_line(outside)
            if found != (None, None):
                return found
    # Hectares are land areas too: "4 hektare" = 40,000 m²; "1-10ha" is hectares at both ends.
    # "2000-1Ha" starts in m² (2000 m² to 1 ha). Plain digits, never "2e+07" for 2000 ha.
    def hectares(m):
        a, b = _hectares(m.group(1)), _hectares(m.group(2))
        return f"{_plain(a if a > b else a * 10000)}-{_plain(b * 10000)} m2"
    line = re.sub(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(?:ha|hektar|hektare|hectare)\b", hectares, line)
    line = _HECTARE.sub(lambda m: f"{_plain(_hectares(m.group(1)) * 10000)} m2", line)
    # "diatas 100 m²", "lebih dari 100", "100 m² ke atas" are minimums; "dibawah 100" a maximum.
    line = re.sub(r"≥|>=|=>", "min ", re.sub(r"≤|<=|=<", "max ", line))  # "≥200 m²"
    line = re.sub(r"\b(?:di\s*atas|lebih\s+dari)\s*(?=\d)", "min ", line)
    line = re.sub(r"\b(?:di\s*bawah|kurang\s+dari)\s*(?=\d)", "max ", line)
    line = re.sub(r"(\d+(?:[.,]\d+)?)\s*((?:m2|m²|meter|mtr)?)\s*(?:ke\s*atas)\b", r"min \1 \2", line)
    # An explicitly stated area wins over rounded frontage/depth dimensions.
    explicit = re.search(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(?:m2|m²)\b", line)  # "4.451,7 m²"
    if explicit and not re.search(r"(?:\d\s*(?:-|–|s/d|sampai|sd)|[<>]|min|max|maks)", line[:explicit.start()]) \
            and not re.search(r"[x×]\s*$", line[:explicit.start()]):  # "12x18m²" is a dimension
        value = _number(explicit.group(1))
        return value, value
    stated = re.match(r"[\s:=.]*(\d+(?:[.,]\d+)?)\s*(?:meter|mtr|m)?\s*(?:[.,;|]?\s*(?:\(|dimensi|ukuran|uk\b|lebar)"
                      r"|(?:[;|/]\s*|[.,]\s+)\d+(?:[.,]\d+)?\s*[x×]\s*\d)", line)  # "4,5 x 17" is one dimension
    if stated:
        value = _number(stated.group(1))
        return value, value  # "LT 479. Dimensi 21x22": the stated area wins over the rounded dimensions
    dimension = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)", line)
    if dimension:
        result = _number(dimension.group(1)) * _number(dimension.group(2))
        return result, result
    minimum = re.search(r"\bmin(?:imal|im)?\.?\s*(\d+(?:[.,]\d+)?)", line)
    maximum = re.search(r"\b(?:maks(?:imal)?|max)\.?\s*(\d+(?:[.,]\d+)?)", line)
    if minimum or maximum:
        return _number(minimum[1]) if minimum else None, _number(maximum[1]) if maximum else None
    # "LB 220 - 2 lantai": a floor count after a dash is not the upper end of the area.
    line = re.sub(r"[-–]\s*\d+(?:[.,]\d+)?\s*(?:lantai|lt|tingkat|storeys?|floors?)\b", " ", line)
    ranged = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|s/d|sampai|sd)\s*(\d+(?:[.,]\d+)?)", line)
    if ranged:
        lo, hi = _number(ranged.group(1)), _number(ranged.group(2))
        return (lo, lo) if lo > hi else (lo, hi)  # an inverted range is malformed: the stated value counts
    value = re.search(r"(?:>|min(?:imal|im)?\.?|mulai)\s*(\d+(?:[.,]\d+)?)", line)
    if value:
        return _number(value.group(1)), None
    value = re.search(r"(?:<|maks(?:imal)?\.?|max)\s*(\d+(?:[.,]\d+)?)", line)
    if value:
        return None, _number(value.group(1))
    values_with_unit = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|meter|mtr)\b", line)
    if values_with_unit:
        parsed = _number(values_with_unit[-1])
        return parsed, parsed
    value = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|meter|mtr)?", line)
    if value:
        parsed = _number(value.group(1))
        return parsed, parsed
    return None, None


# "LT.. 375 m2", "LT (+ dimensi): 280 m2", "Luas Tanah (LT): 82,5 m²": dots and a bracketed note after the label.
_AREA_RES = {kind: re.compile(pattern + r"(?:[ \t]+(?:gudang|kantor|total|keseluruhan|sertifikat|full))?(?:[ \t]*\([^()\n\d]{0,20}\))?(?:\s*[.:;=])*\s*"
                              # v4.4: "Bangunan ideal ≥200 m²", "LB idealnya 300": the ideal size a buyer states.
                              r"(?:(?:sekitar|kurang\s+lebih|kurleb|kira[\s-]*kira|kira2|approx\.?|ideal(?:nya)?)\s*[:=]?\s*)?"
                              # "LT : +/- 480 m2", "LT (144m2)": a sign or a bracket may open the value.
                              r"((?:(?:min(?:imal|im)?|max|maks(?:imal)?)\.?\s*)?[*+±~\-\s(=/@]*[<>≥≤]?=?[\d].*?)(?=\n|\b(?:lt|lb|kt|km|harga|budget|bangunan|sertifikat|hadap)\b|(?<=m2)\s|$)")
             # "LT375", "LB200" glued to their value (never "Lt3", the third floor); "L.Tanah", "Luas lahan", "L.Bang".
             for kind, pattern in {"land": r"(?:luas\s+tanah|\blt(?:\b|(?=\d{2}))|luasan\s+tanah|\bl\.?\s*tanah|luas\s+lahan)",
                                   "building": r"(?:luas\s+bangunan|\blb(?:\b|(?=\d{2}))|bangunan|\bl\.?\s*bang(?:unan)?\b)"}.items()}


# "LT/LB : 383/400", "LT/LB: 200/ 300 m2", "LB/LT 90/72": both areas in one statement.
_AREA_PAIR = re.compile(r"\b(lt|lb)[ \t]*/[ \t]*(lt|lb)\b[ \t]*[.:=]*[ \t]*(\d+(?:[.,]\d+)?)[ \t]*(?:m2|m²|m)?(?:[ \t]*\([^()\n]{0,15}\))?"
                        r"[ \t]*/[ \t]*(\d+(?:[.,]\d+)?)(?:[ \t]*(?:m2|m²|m\b))?")
# "Minimal Luas tanah 150 m²": a bound written before the label.
_AREA_BOUND_BEFORE = re.compile(r"\b(?:(?P<min>min(?:imal|im|imum)?|di\s*atas|diatas|lebih\s+dari)|(?P<max>max(?:imal)?|maks(?:imal)?|di\s*bawah|dibawah))"
                                r"\.?[ \t]*[:=]?[ \t]*$")


def _area_pairs(text):
    def pair(m):
        first, second = (m.group(3), m.group(4)) if m.group(1) == "lt" else (m.group(4), m.group(3))
        return f"lt {first} m2 lb {second} m2" if {m.group(1), m.group(2)} == {"lt", "lb"} else m.group()
    return _AREA_PAIR.sub(pair, text) if "/" in text else text


def _areas(text: str, kind: str) -> tuple[float | None, float | None]:
    text = _area_pairs(text.lower())
    # v4.5: "Bangunan 2 Lantai" states the storeys; a "Luas Bangunan 87" after it is still the area.
    # "Panjang bangunan 40 m": a side, not the area.
    match = next((m for m in _AREA_RES[kind].finditer(text) if not re.match(r"[*+±~\-\s]*\d+\s*(?:lantai|tingkat)\b", m.group(1))
                  and not re.search(r"\b(?:panjang|lebar|tinggi)[ \t]*$", text[max(0, m.start() - 10):m.start()])), None)
    if match:
        lo, hi = _range_from_line(match.group(1))
        bound = _AREA_BOUND_BEFORE.search(text[max(0, match.start() - 20):match.start()])
        if bound and lo is not None and lo == hi:
            return (lo, None) if bound.group("min") else (None, hi)
        return lo, hi
    return None, None


# Land sizes written without an LT label (landed listings only): "9 x 15 = 135 m2", "dimensi : 14X31=434M",
# "Ukuran: 72m² (6x12)", "Ls : 15.376 m2 (124 x 124)", "Tanah 550 m2", "Luas 480", "L : 500 m2", "- 14x25",
# "*3871 M2*", "Lucas 128 ( 8x16)".
_DIM = r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:m|meter|mtr)?\s*[x×]\s*(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:m\b|meter|mtr)?"
_DIM_RE = re.compile(r"(?<![\d.,])" + _DIM + r"(?![\d.,]*\s*[x×])")
_AREA_VALUE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(m2|m²|meter2|mtr2|meter|mtr|m\b|ha\b|hektar\w*|hectare)")
_LAND_LABEL = re.compile(r"^[^a-z0-9\n]*(?:\d{1,2}\s*[.)]\s*[^a-z0-9\n]*)?(?P<label>luas(?:an)?(?:\s+(?:tanah|lahan))?|lt|ls|l|tanah|lahan|ukuran|uk|"
                         r"dimensi|size|total(?:\s+luas)?)\b(?P<gap>[^\n\d]{0,15}?)(?=[<>±~+(@]?\s*\d)")
_LABEL_GAP_STOP = re.compile(r"\b(?:semi|gross|semigross|sg|nett?|netto|bangunan|unit|kamar|depan|jalan|row)\b")
_ROOM_LINE = re.compile(r"\b(?:kamar|kmr|kt|km|bedroom|br|ruang\w*|room|toilet|wc|dapur|carport|garasi|garage|teras|balkon|lantai)\b")
_NOT_LAND_LINE = re.compile(r"\b(?:lb|bangunan|bangunannya|gedung|lantai|kamar|kt|km|harga|price|rp|budget|jt|juta|mil+[iy]ar\w*|"
                            r"listrik|pln|watt|kva|row|jalan|jarak|menit|km)\b")


def _area_number(value, unit):
    return _hectares(value) * 10000 if unit and unit.startswith(("ha", "hek", "hec")) else _number(value)


def _fallback_land(text):
    """(lo, hi) land area of a landed listing written in one of the unlabelled forms above, else (None, None)."""
    strong, weak = None, None
    for raw in text.split("\n"):
        line = re.split(r"\b(?:lb|l\.?\s*bang\w*|luas\s+bangunan|bangunan)\b", re.sub(r"[*_~`]", " ", raw.lower()))[0]
        if not _DIGIT.search(line):
            continue
        dims = [(_number(a), _number(b)) for a, b in _DIM_RE.findall(line)]
        dims = [a * b for a, b in dims if 2 <= a <= 600 and 2 <= b <= 2000]
        stated = [(_area_number(v, u), u) for v, u in _AREA_VALUE.findall(line)]
        # A size and the dimensions it comes from ("9 x 15 = 135 m2", "72m² (6x12)", "Lucas 128 ( 8x16)").
        bare = [_number(v) for v in re.findall(r"(?<![\d.,x×])(\d{2,7}(?:[.,]\d+)?)(?![\d.,]|\s*[x×]|\s*(?:jt|juta|m\b|kt|km|lantai|unit|kamar))",
                                               _DIM_RE.sub(" ", line))]
        fits = [v for v in [x for x, _ in stated] + bare for d in dims if d and abs(v - d) <= 0.15 * d and v >= 20]
        if fits and not _ROOM_LINE.search(line):
            strong = strong or fits[0]
            continue
        label = _LAND_LABEL.match(line)
        if label and not _NOT_LAND_LINE.search(label.group("gap")) and not _LABEL_GAP_STOP.search(label.group("gap")):
            # "Dimensi : 11x31m²", "Ukuran 4,5 × 16 meter": the dimensions, never their last side, give the size.
            name, rest = label.group("label"), line[label.end():]
            plain_rest = _DIM_RE.sub(" ", rest)
            unit_value = _AREA_VALUE.match(plain_rest.lstrip(" <>±~+(@:=.-")) or _AREA_VALUE.search(plain_rest[:25])
            if unit_value and not (unit_value.group(2) in ("m", "meter", "mtr") and name in ("tanah", "lahan", "total", "total luas")):
                value = _area_number(*unit_value.groups())
                if value >= (20 if name == "l" else 10):
                    strong = strong or value
                    continue
            if dims and name not in ("l", "total", "total luas"):
                strong = strong or dims[0]
                continue
            bare_value = re.match(r"[\s<>±~+(@:=.-]*(\d+(?:[.,]\d+)?)(?![\d.,])(?!\s*(?:jt|juta|m\b|kt|km|lantai|unit|kamar|x|×|%))", rest)
            if bare_value and ((name in ("luas", "luasan", "lt", "ls") and _number(bare_value.group(1)) >= 20) or (
                    name == "l" and not rest[bare_value.end():].strip(" .,;:!") and _number(bare_value.group(1)) >= 50)):
                weak = weak or _number(bare_value.group(1))
                continue
        if _NOT_LAND_LINE.search(line):
            continue
        only_dim = re.fullmatch(r"[^a-z0-9]*(?:-\s*)?" + _DIM + r"[^a-z0-9]*", line.strip())
        if only_dim and dims:
            weak = weak or dims[0]
            continue
        only_area = re.fullmatch(r"[^a-z0-9]*(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)\s*(m2|m²|ha|hektar\w*)[^a-z0-9]*", line.strip())
        if only_area:
            weak = weak or _area_number(*only_area.groups())
    value = strong or weak
    return (value, value) if value and 10 <= value <= 10**7 else (None, None)


# v4.5: "- Panjang 40 m" and "- Lebar 10.8 m" (separate lines too) give the area they measure (never "lebar jalan").
_SIDE = r"[ \t]*(?:(tanah|lahan|kavling|kav|bangunan|gudang|pabrik|ruang\w*|depan|muka)[ \t]*)?(?:\([^()\n]{0,15}\)[ \t]*)?[:=]?[ \t]*" \
        r"[±+~]?[ \t]*(\d+(?:[.,]\d+)?)[ \t]*(?:m|meter|mtr|mt)?\b(?![ \t]*(?:x|×|m2|m²|km|cm|jt|juta|%))"
_LENGTH = re.compile(r"\bpanjang" + _SIDE)
_WIDTH = re.compile(r"\blebar" + _SIDE)


def _length_width(text):
    """(area, 'land' | 'building' | None) from the length and width a listing states, else None."""
    length, width = _LENGTH.search(text), _WIDTH.search(text)
    if not (length and width):
        return None
    a, b = _number(length.group(2)), _number(width.group(2))
    if not (2 <= b <= 600 and 2 <= a <= 2000):
        return None
    words = {length.group(1), width.group(1)}
    kind = 'land' if words & {'tanah', 'lahan', 'kavling', 'kav'} else \
        'building' if words & {'bangunan', 'gudang', 'pabrik'} or any(w and w.startswith('ruang') for w in words) else None
    return round(a * b, 2), kind


_UNIT_AREA = re.compile(r"\b(?:luas|size|ukuran)\b(?!\s*(?:tanah|lahan|bangunan|parkir))[^\n\d]{0,25}?(?=[<>±~]?\s*(?:min|max|maks)?\.?\s*\d)(?P<value>[^\n]{1,40})")


def _unit_area(text):
    found = []
    for m in _UNIT_AREA.finditer(text):
        label = m.group(0)[:m.start("value") - m.start()]
        net = re.search(r"\bnett?\b\s*[:=]?\s*(\d+(?:[.,]\d+)?)", m.group(0))
        if net and 15 <= _number(net.group(1)) <= 2000:
            found.append((0, _number(net.group(1)), _number(net.group(1))))
            continue
        # The words between "Luas" and the number may say "diatas"/"min" ("Luas wajib diatas 100 m²").
        lo, hi = _range_from_line(re.split(r"\b(?:harga|lantai|lt|kamar|kt|br)\b", re.sub(r"^(?:luas|size|ukuran)", "", m.group(0)))[0])
        if (lo or hi) and 15 <= (lo or hi) <= 2000:
            gross = bool(re.search(r"semi\s*gross|\bsg\b|\bgross\b", label + m.group("value")[:15]))
            nett = bool(re.search(r"\bnett?\b", label + m.group("value")[:15]))
            found.append((0 if nett else 2 if gross else 1, lo, hi))
    return min(found, key=lambda f: f[0])[1:] if found else (None, None)  # first unit among equals


# --------------------------------------------------------------------------- prices
_UNIT_MULTIPLIER = {"m": 10**9, "milyar": 10**9, "milyard": 10**9, "miliar": 10**9, "miliard": 10**9, "b": 10**9, "bn": 10**9,
                    "billion": 10**9, "billions": 10**9,
                    "jt": 10**6, "juta": 10**6, "rb": 10**3, "ribu": 10**3, "k": 10**3}


def _multiplier(unit):
    """Rupiah per unit; every milyar spelling ("milliard", "millyar", "miliard") is 10⁹."""
    unit = (unit or "").lower()
    return 10**9 if unit.startswith("mil") else _UNIT_MULTIPLIER.get(unit, 1)


def _money_value(number: str, unit: str | None) -> int:
    amount = number.strip()
    if amount.count(',') == 1:
        amount = amount.replace('.', '').replace(',', '.')
    try:
        value = Decimal(amount) if ',' not in amount and amount.count('.') <= 1 else Decimal(str(_number(amount)))
    except (InvalidOperation, ValueError):
        value = Decimal(str(_number(number)))
    return int(value * _multiplier(unit))


def _place_value(number: str, unit: str | None) -> float:
    """Value of the last significant digit written ("500" jt → 100 jt, "1,5" M → 0.1 M)."""
    multiplier = _multiplier(unit)
    amount = number.strip()
    decimal_sep = "," if amount.count(",") == 1 and len(amount.rsplit(",", 1)[1]) <= 2 else \
        "." if amount.count(".") == 1 and len(amount.rsplit(".", 1)[1]) <= 2 else None
    if decimal_sep:
        decimals = amount.rsplit(decimal_sep, 1)[1].rstrip("0")
        if decimals:
            return multiplier * 10 ** -len(decimals)
        amount = amount.rsplit(decimal_sep, 1)[0]
    digits = re.sub(r"\D", "", amount).lstrip("0") or "0"
    zeros = len(digits) - len(digits.rstrip("0")) if digits != "0" else 0
    return multiplier * 10 ** zeros


def approximate_range(number, unit):
    """"1M-an" → 0.9–1.49 M: [v − 0.1·p, v + 0.49·p] with p the place value of the last written digit."""
    value, place = _money_value(number, unit), _place_value(number, unit)
    return int(round(value - 0.1 * place)), int(round(value + 0.49 * place))


# "milyar", "miliar", "milyard", "miliard" and the Dutch-style "milliard" / "millyar" spellings.
_PRICE_UNITS = r"mil+[iy]ard?|billions?|juta|jt|ribu|rb|bn|b|m"
# A unit may run into "nego"/"nett" ("13,5Mnego", "2Mnett").
_MONEY_RE = re.compile(r"(?:rp\.?\s*)?(?<![\d,])(?<!\d\.)(?<!\dm)(\d+(?:[.,]\d+)*)\s*(" + _PRICE_UNITS +
                       r")?(?:(?<=[a-z])(?=nego|nett?(?![a-z])|net\b)|(?![a-z0-9²]))(\s*-?\s*an\b)?")
_RANGE_EXPAND = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(mil+[iy]ard?|juta|jt|m)\b")
_PER_M2_AFTER = re.compile(r"\s*(?:/|per|\bp\b)\s*(?:m2|m²|m\b|meter|mtr|meter2|mtr2)")
_YEAR_AFTER = re.compile(r"\s*(?:/|per|\bp\b|utk|untuk)\s*(?:1\s*)?(?:th|thn|thun|tahun|taun|year|yr|yearly)\b")
_MONTH_AFTER = re.compile(r"\s*(?:/|per|\bp\b)\s*(?:1\s*)?(?:bl|bln|bulan|month|mo)\b")
_YEAR_LINE = re.compile(r"(?:/|per\s*|utk\s*|untuk\s*)(?:1\s*)?(?:th|thn|thun|tahun|year)\b|\bpertahun\b|\bsetahun\b|\btahunan\b|\byearly\b")
_MONTH_LINE = re.compile(r"(?:/|per\s*)(?:1\s*)?(?:bl|bln|bulan|month)\b|\bperbulan\b|\bsebulan\b|\bbulanan\b|\bmonthly\b")
_PRICE_LINE = re.compile(r"budge[dt]|budjet|buget|harga|hrg|\bhg\b|price|sewa|rent|jual|sale|\bsell\b|under|di bawah|dibawah|\brp\b|\bstart(?:ing)?\b|"
                         r"(?<=m2)[ \t]*@|(?<=m²)[ \t]*@|"
                         r"open\b|nett?\b|\bnego|hitung\s+tanah|💰|^\W*h\s*[:.]\s*(?=\d|rp)")
_RENT_WORD = re.compile(r"\b(?:sewa|disewakan|rent|rental|kontrak|lease)\b")
_SALE_WORD = re.compile(r"\b(?:jual|dijual|sale|beli|for sale)\b")
_OFFER_KEYWORD = re.compile(r"\b(?:di)?(?:jual|sewa(?:kan)?)\b|\b(?:rent|sale|lease)\b")
_PRICE_WORD = re.compile(r"\b(?:harga(?!\s+sewa)\w*|hrg|price|dijual|jual|budget|disewakan)\b|💰")
_INCOME_LINE = re.compile(r"\b(?:potensi|penghasilan|pendapatan|income|omzet|omset|profit|keuntungan|cash\s*flow|cashflow|roi|"
                          r"yield|return|tarif|okupansi|occupancy|penghuni)\b|\bhasil\s+(?:sewa|kost?|usaha|bersih|per)\b"
                          r"|\bsewa\s+(?:per\s+|/\s*)?(?:kamar|kmr|kost?)\b|\bkost?\s+per\s*bulan\b")
# The label nearest before an amount: an area label ("luas 200m") or a price label ("harga 2m").
_AMOUNT_LABEL = re.compile(r"(?P<area>\b(?:luas\w*|lt|lb|size|ukuran|sg|semi\s*gross|gross|nett?|dimensi|lebar|panjang|row|"
                           r"jarak|tanah|bangunan)\b)|\b(?:harga\w*|hrg|budget|price|jual|dijual|sewa\w*|disewakan|rp|nego\w*)\b|💰")
_REQUEST_LINE = re.compile(r"\b(?:buyer|renter|investor|request|req|dicari|mencari|cari|butuh)\b")
_PURE_FILLER = re.compile(r"\b(?:harga|price|hrg|open|budget|nett?|cash|only|rp|cuma|cuman|hanya|start|mulai|total|all|in|"
                          r"include\w*|exclude\w*|incl|excl|termasuk|nego\w*|bu|keras|tipis|halus|tawar\w*|thn|th|tahun|bln|bulan|"
                          r"per|m2|m|meter|mtr|atau|or|sewa|jual|unit|turun|jadi|dari|ke|ok|deal|harga\w*|an|reposisi|from|"
                          # "No Furnish : 115jt / thn", "Full furnished 150 jt/th": the furnishing a price is for.
                          r"no|non|un|furnish\w*|unfurnish\w*|semi|full|fully|kosongan)\b")


# The period of a price per m² ("30 rb-35 rb /meter/ bln", "Rp 200.000 / m² / tahun").
_M2_MONTH = re.compile(r"(?:/|\bper)\s*(?:1\s*)?(?:bl|bln|bulan|month)\b|\bperbulan\b|\bsebulan\b|\bbulanan\b|\bmonthly\b")
_M2_YEAR = re.compile(r"(?:/|\bper|\butk|\buntuk)\s*(?:1\s*)?(?:th|thn|thun|tahun|taun|year|yr)\b|\bpertahun\b|\bsetahun\b|"
                      r"\btahunan\b|\byearly\b")


def _line_basis(line):
    if re.search(r"/\s*(?:m2|m²|meter|mtr)|per\s*(?:m2|m²|meter|mtr)\b", line):
        return "per_m2"
    if _MONTH_LINE.search(line):
        return "per_month"
    if _YEAR_LINE.search(line):
        return "per_year"
    return "total"


def _price_candidates(text):
    """Every price statement in reading order: dicts with min, max, basis, kind and approx (read-only, cached)."""
    return list(_cached_price_candidates(text))


@lru_cache(maxsize=16384)
def _cached_price_candidates(text):
    return tuple(_find_price_candidates(text))


_DIGIT = re.compile(r"\d")
_TEXT_FIXES = [(re.compile(r'\b(?:bugdet|budged|budjet|buget|bujet|budger|bajet|bujed)\b'), 'budget'),
               (re.compile(r'\bidr\.?\s*(?=\d)'), 'rp '),  # "Sell : IDR 7.75M nego"
               # "-an" marks an approximate amount: 500 jutaan, 1M-an, 1 M an, 1man, 7jtan, 2 milyaran.
               (re.compile(r'\bjuta+a+n\b'), 'juta an'), (re.compile(r'\bjuta+\b'), 'juta'),
               (re.compile(r'(?<=\d)\s*(mil+[iy]ard?|jt|m)\s*-?\s*an\b'), r' \1 an'),
               (re.compile(r'(?<=\d)per(?=\s*(?:m|m2|meter|mtr|th|thn|tahun|bl|bln|bulan)\b)'), ' per'),  # "3.500.000per m"
               (re.compile(r'(?<![\d.,])(\d{2,4})\s*t\s*(?=/\s*(?:th|thn|tahun|bln|bulan)\b)'), r'\1 jt')]  # "150t/tahun"
_STATEMENT_SPLIT = re.compile(r"(?=\bharga\b|\bbudget\b)")


def _find_price_candidates(text):
    text = text.lower()
    for pattern, replacement in _TEXT_FIXES:
        text = pattern.sub(replacement, text)
    out = []
    statements = ((bool(_INCOME_LINE.search(raw)), part) for raw in text.split("\n") if _DIGIT.search(raw)
                  for segment in _STATEMENT_SPLIT.split(raw) for part in _split_offers(segment))
    for income, line in statements:
        line = re.sub(r"[*_~]", "", line)
        if not line.strip() or (income and not _PRICE_WORD.search(line)):
            # "Potensi hasil Rp 20 jt/bulan", "Tarif kost 1,75 jt per bulan", "Okupansi full. Harga sewa bulanan
            # 3 jt/bln": income of the property, not its asking price.
            continue
        line = _RANGE_EXPAND.sub(r"\1 \3 - \2 \3", line)
        mentions = []
        for m in _MONEY_RE.finditer(line):
            number, unit, approx = m.group(1), m.group(2), m.group(3)
            prefix = line[max(0, m.start() - 3):m.start()]
            rp = 'rp' in m.group(0)[:3] or re.search(r'rp\.?\s*$', prefix)
            if not unit and re.fullmatch(r'\d{3,4}', number) and re.match(r'\s*(?:nett?|nego)\b', line[m.end():]) and \
                    re.search(r'(?:\b(?:harga|hrg|price)\b|💰|^\W*h\s*[:.])[^\d]{0,15}$', line[:m.start()]):
                unit = 'jt'  # "Harga 525 nett", "Harga : jadi 400 Nego": juta
            elif not unit and re.fullmatch(r'\d{1,2},\d{1,3}|\d{1,2}\.\d{1,2}', number) and re.match(r'\s*(?:nett?\b|nego\b|$)', line[m.end():]) and \
                    re.search(r'(?:\b(?:harga|hrg|price)\b|💰)[^\d]{0,15}$', line[:m.start()]):
                unit = 'm'  # "Harga Update 6,4 nego", "Harga 2,650 nego": milyar
            elif not unit and re.fullmatch(r'\d{1,3}(?:,\d{1,2})?', number) and _PER_M2_AFTER.match(line[m.end():m.end() + 12]) and \
                    re.search(r'(?:\b(?:harga|hrg|price)\b|💰)[^\d]{0,15}$', line[:m.start()]):
                unit = 'jt'  # "Harga 16,5/m2 Nego": juta per m²
            try:
                if unit:
                    value = _money_value(number, unit)
                elif (rp and len(re.sub(r'\D', '', number)) >= 5) or re.fullmatch(r'\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?', number) or (
                        # "Harga 800.000 / mtr nego", "Harga 9.000/m2": a price per m² written in rupiah
                        re.fullmatch(r'\d{1,3}(?:\.\d{3})+', number) and _PER_M2_AFTER.match(line[m.end():m.end() + 16])
                        and _PRICE_LINE.search(line)):
                    value = int(_number(number))
                    unit = ''
                else:
                    continue
            except (ValueError, ArithmeticError):
                continue  # an unreadable amount is skipped, never fatal for the message
            if unit.lower() == 'm' and (re.match(r'\s*(?:x|×)\s*\d', line[m.end():]) or
                                        re.search(r'\d\s*m?\s*(?:x|×)\s*$', line[:m.start()])):
                continue  # "5 m x 20 m", "23m x 100m" is a dimension
            if unit.lower() == 'm' and not rp and re.fullmatch(r'\d{4,}', number):
                continue  # "Hanggar 3 6000m": metres; nobody writes 6.000 milyar
            if unit.lower() == 'm' and rp and re.fullmatch(r'[1-9]\d{3}', number):
                value = int(number) * 10**6  # "turun jd rp 1795m": 1,795 milyar written without its comma
            if unit.lower() in ('b', 'bn') and not rp and not _PRICE_LINE.search(line):
                continue  # "Unit: 6B" is a unit number
            if unit and re.fullmatch(r'\d{1,3}(?:\.\d{3}){2,}', number):
                value = int(_number(number))  # "Harga : 2.600.000.000 M": the digits already say the amount
                if value < 10**8:
                    continue  # "Rp 1.250.000 m": unreadable
            if unit.lower() == 'm' and not rp:
                label = [k for k in _AMOUNT_LABEL.finditer(line[:m.start()])]
                if label and label[-1].group('area'):
                    continue  # "Luas minim 200m", "Luas Nett 65m": metres of area, not milyar
            tail = line[m.end():m.end() + 16]
            basis = 'per_m2' if _PER_M2_AFTER.match(tail) else 'per_month' if _MONTH_AFTER.match(tail) else \
                'per_year' if _YEAR_AFTER.match(tail) else None
            mentions.append({'value': value, 'number': number, 'unit': unit, 'approx': bool(approx), 'basis': basis,
                             'start': m.start(), 'end': m.end()})
        if not mentions:
            continue
        keyword = _PRICE_LINE.search(line) or (
            # "*Buyer request gudang 4-5 M* :": a request line names its budget without a price word.
            _REQUEST_LINE.search(line) and all(x['unit'] and x['unit'].lower() != 'b' for x in mentions))
        if not keyword:
            rest = _MONEY_RE.sub(" ", line)
            rest = _PURE_FILLER.sub(" ", re.sub(r"[^a-z ]+", " ", rest))
            if rest.split():
                continue
        basis_line = line
        if len(mentions) >= 2 and "(" in line:
            # Amounts in brackets ("(permeter 8.5jt)", "(opsi lift + 380 jt)", "(10,5jt/m2)") are secondary to
            # the amounts outside them.
            spans = [(b.start(), b.end()) for b in _BRACKETS.finditer(line)]
            outside = [x for x in mentions if not any(a <= x['start'] < b for a, b in spans)]
            if outside and len(outside) < len(mentions):
                mentions, basis_line = outside, _BRACKETS.sub(" ", line)
        line_basis = _line_basis(basis_line)
        # "15-20 juta/tahun", "8-9 jt/m²": both ends of a range share the stated basis; an amount that is no end of
        # that range keeps its own ("budget 2,5-3M atau 9jt/m2": a total range OR a price per m²).
        changed = True
        while changed:
            changed = False
            for a, b in zip(mentions, mentions[1:]):
                if bool(a['basis']) != bool(b['basis']) and _RANGE_BETWEEN.fullmatch(line[a['end']:b['start']]):
                    a['basis'] = b['basis'] = a['basis'] or b['basis']
                    changed = True
        per_area = [x for x in mentions if x['basis'] == 'per_m2']
        groups = [(per_area, None)] if per_area else [(mentions, None)]
        totals = [x for x in mentions if not x['basis'] and x['unit']]
        if per_area and totals and len(totals) + len(per_area) == len(mentions) and \
                min(x['value'] for x in totals) >= max(SALE_SIZED_MIN // 2, 20 * max(x['value'] for x in per_area)):
            # A total next to a price per m² ("Budget 2,5-3 M atau 9 jt/m²", "Harga 3 M / 15 jt per m²"): two statements;
            # a total is never read as a price per m².
            for x in totals:
                x['basis'] = 'total'
            groups = sorted([(totals, None), (per_area, None)], key=lambda g: g[0][0]['start'])
        if not per_area and any(x['basis'] in ('per_year', 'per_month') for x in mentions):
            periods = [x for x in mentions if x['basis'] in ('per_year', 'per_month')]
            plain = [x for x in mentions if not x['basis']]
            # "Harga 3 M / 100 jt per tahun": a total far above the yearly rent is the sale price.
            if plain and min(x['value'] for x in plain) >= 5 * max(x['value'] for x in periods):
                for x in plain:
                    x['basis'] = 'total'
                groups = sorted([(plain, 'sale'), (periods, 'rent')], key=lambda g: g[0][0]['start'])
            else:
                groups = [(periods, None)]
        for mentions, kind in groups:
            candidate = _price_candidate(line, mentions, line_basis)
            candidate['kind'] = kind or candidate['kind']
            out.append(candidate)
    return out


def _split_offers(segment):
    """"Jual 1,65M / Sewa 85jt/thn": a second offer keyword after an amount starts its own statement."""
    parts, start = [], 0
    for m in _OFFER_KEYWORD.finditer(segment):
        if re.search(r"\b(?:min(?:imal|im)?|max|maks(?:imal)?)\.?\s*$", segment[:m.start()]):
            continue  # "minimal sewa 2 th" is the lease term of the same offer
        if m.start() > start and re.search(r"\d", segment[start:m.start()]) and re.search(r"\d", segment[m.end():]):
            parts.append(segment[start:m.start()])
            start = m.start()
    return parts + [segment[start:]]


_BRACKETS = re.compile(r"\([^()\n]*\)")
# "1,5 - 1,7 M", "500 jt s/d 1 M", "2 M sampai 2,5 M": the only wording that makes two amounts a range.
_RANGE_BETWEEN = re.compile(r"\s*(?:rp\.?\s*)?(?:[-–~]|s/?d|sd|sampai|sampe|hingga|until|to)\s*(?:rp\.?\s*)?")


def _amount_bounds(line, mentions, numbers):
    """(lo, hi) of several amounts on one line: an explicit range ("1,5 - 1,7 M") within 3×, or a budget, keeps both
    ends; otherwise "Harga 6M 5.75M", "480jt, 380jt", "15jt/m² >> 13,5jt/m²" lower the price to the last amount and
    anything else ("Harga 1,5 M / 1,7 M furnished") states the first."""
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    ranged = all(_RANGE_BETWEEN.fullmatch(line[a['end']:b['start']]) for a, b in zip(mentions, mentions[1:]))
    if re.search(r"\bbudget", line) or (ranged and max(numbers) <= 3 * min(numbers)):
        return min(numbers), max(numbers)
    # "Sewa 40jt/th atau 4,5jt/bln": amounts of different periods are options; the first one counts.
    same_basis = len({x['basis'] for x in mentions}) == 1
    value = numbers[-1] if same_basis and all(v < numbers[0] for v in numbers[1:]) else numbers[0]
    return value, value


def _price_candidate(line, mentions, line_basis):
    per_area = mentions[0]['basis'] == 'per_m2'
    basis = 'per_m2' if per_area else mentions[0]['basis'] or line_basis
    numbers = [x['value'] for x in mentions]
    kind = 'rent' if _RENT_WORD.search(line) or basis in ('per_year', 'per_month') else 'sale' if _SALE_WORD.search(line) else None
    before = line[:mentions[0]['start']]
    # "minimal sewa 2 th", "min 2 thn": a lease term, not a price bound.
    line = re.sub(r"\b(?:min(?:imal|im)?|max|maks(?:imal)?)\.?\s*(?:sewa\s*|kontrak\s*)?\d+\s*(?:th|thn|tahun|thun|bl|bln|bulan|year|yr)s?\b", " ", line)
    period = area = None
    if per_area:
        # v4.5: "Rp58.500/m² BANGUNAN/bulan", "50rb/m2 per tahun": a rent per m² of a period, for the building (or the
        # land) area; matching_rules multiplies it out (per_m2_terms).
        period = 'per_month' if _M2_MONTH.search(line) else 'per_year' if _M2_YEAR.search(line) else None
        area = 'building' if re.search(r"\b(?:bangunan|bang|lb|building)\b", line) else \
            'land' if re.search(r"\b(?:tanah|lahan|lt|land)\b", line) else None
        kind = 'rent' if period else kind
        if re.search(r'\b(?:min(?:imal|im)?|mulai|diatas|di atas)\s*[:=]?\s*$', before):
            lo, hi = min(numbers), None
        elif re.search(r'\b(?:max|maks|under)\b', line):
            lo, hi = None, max(numbers)
        else:
            lo, hi = _amount_bounds(line, mentions, numbers)
    elif re.search(r"\b(?:max|maks(?:imal)?|under|dibawah|di bawah)\b", line):
        lo, hi = None, max(numbers)
    elif re.search(r"\b(?:min(?:imal|im)?|mulai|diatas|di atas|start(?:ing)?)\b", line) and not (
            # "harga start 700jt-1M", "mulai 1,5 - 2 M": a stated range keeps both ends.
            len(mentions) >= 2 and all(_RANGE_BETWEEN.fullmatch(line[a['end']:b['start']]) for a, b in zip(mentions, mentions[1:]))):
        lo, hi = min(numbers), None
    else:
        lo, hi = _amount_bounds(line, mentions, numbers)
    approx = None
    if len(mentions) == 1 and mentions[0]['approx']:
        try:
            a_lo, a_hi = approximate_range(mentions[0]['number'], mentions[0]['unit'])
            approx = (a_lo if lo is not None else None, a_hi if hi is not None else None)
        except (ValueError, ArithmeticError):
            approx = None
    return {'min': lo, 'max': hi, 'basis': basis, 'kind': kind, 'approx': approx, 'period': period, 'area': area}


def _periodic(candidate):
    """A rent price of a period: per year/month in total, or per m² per year/month."""
    return candidate['basis'] in ('per_year', 'per_month') or bool(candidate.get('period'))


def per_m2_terms(text, value=None):
    """(period, area) of the price per m² a text states: ('per_month' | 'per_year' | None, 'building' | 'land' | None);
    "Rp58.500/m² BANGUNAN/bulan" → ('per_month', 'building'). ``value``: the amount the document holds (picks its
    statement among several)."""
    found = [c for c in _price_candidates(text) if c['basis'] == 'per_m2']
    pick = next((c for c in found if value is not None and value in (c['min'], c['max'])), found[0] if found else None)
    return (pick['period'], pick['area']) if pick else (None, None)


def _price(text: str) -> tuple[int | None, int | None, str | None]:
    candidates = _price_candidates(text)
    if not candidates:
        return None, None, None
    first = candidates[0]
    return first['min'], first['max'], first['basis']


# --------------------------------------------------------------------------- categories and places
_LANDED = {"house", "land", "warehouse", "factory", "shophouse", "villa", "commercial_building"}
_INTENDED_USE = re.compile(r"(?i)\b(?:peruntukan(?:nya)?|digunakan\s+(?:untuk|utk|sebagai)|dipakai\s+(?:untuk|utk|sebagai))\b[^\n]*")
# A landed home described only by its specs ("Imperial Beach 2 lantai · LT 160 · KT 4+1") is a house.
# (also "LT/LB : 84/84")
_IMPLIED_LAND = re.compile(r"\b(?:lt(?:[ \t]*/[ \t]*lb)?|lb[ \t]*/[ \t]*lt|luas\s+tanah)\b[ \t]*[.:=]?[ \t]*[±+~-]*[ \t]*\d")
_IMPLIED_BEDROOMS = re.compile(r"\b(?:kt|kamar\s+tidur)\b[ \t]*[.:=]?[ \t]*\d|\b\d\s*(?:\+\s*\d\s*)?(?:kt|kamar\s+tidur)\b|🛌\W{0,3}\d")
# A former business building ("ex resto", "eks showroom"); it names the asset only when nothing else does.
_FORMER_BUSINESS = re.compile(r"\b(?:ex|eks|bekas)[ \t.-]*(?:resto(?:ran)?|restaurant|rumah[ \t]+makan|cafe|kafe|bank|showroom|"
                              r"minimarket|supermarket|klinik|bengkel|spbu)\b")
_NOT_A_HOUSE = re.compile(r"apart|\btower\b|condo|\bsoho\b|\bunit\b|studio|penthouse|\bkos(?:t|an)?\b|homestay|guest\s*house|"
                          r"komersial|commercial|\bmall\b|\bkios\b|\bstand\b|\bbedeng\b|\blapak\b")


def _categories(text: str) -> list[str]:
    # Intended use and interior amenities are not the asset being requested/sold.
    if re.search(r"\btanah\s+(?:spesifikasi|untuk|buat|guna)\b", text):
        return ["land"]
    text = re.split(r"\b(?:cocok|ideal)\s+(?:untuk|utk|buat|usaha|sebagai|dijadikan)\b", text)[0]
    found = []
    for category, patterns in _CATEGORY_RES.items():
        if any(pattern.search(text) for pattern in patterns):
            found.append(category)
    if "factory" in found and "warehouse" in found and "pabrik" in text and "gudang" not in text:
        found.remove("warehouse")
    if found == ["land"] and _IMPLIED_BEDROOMS.search(text) and re.search(r"\b(?:lb|luas\s+bangunan)\b", text) and \
            re.search(r"\bkav(?:ling)?\b", text) and not re.search(r"\btanah\b|\blahan\b", text):
        found = ["house"]  # "Kavling B · LB 232 m² · Kamar tidur 3+1": a house of an estate, not a plot
    if not found and _APARTMENT_RE.search(text) and (re.search(r"\b(?:tower|twr|studio|penthouse|lantai)\b|\d\s*(?:br|bedrooms?)\b", text)):
        found.append("apartment")  # "Waterplace 3BR / Tower ABC": a known apartment building with its unit type
    if not found and _IMPLIED_LAND.search(text) and _IMPLIED_BEDROOMS.search(text) and \
            not _NOT_A_HOUSE.search(re.sub(r"\b\d+\s*unit\b", " ", text)):  # "AC 7 unit" counts, it is no unit type
        found.append("house")
    if set(found) <= {"land"} and _FORMER_BUSINESS.search(text):
        found.append("commercial_building")  # v4.5: "Dijual ex resto", "Tanah & bangunan ex showroom"
    return found


_VIEW_PHRASE = re.compile(r"(?i)\bview\b[^\n,;|]*")
# v4.5: what a property suits or what surrounds it is not the property (R8): "(Cocok segala usaha) : 🏥 Rumah sakit.
# 🏨 Hotel.", "bisa dibangun ruko", "Target pasar ±1500 rumah di sekitarnya", "dekat sekolah, rumah sakit, hotel".
_SUITS = (r"(?:cocok|ideal|sesuai|pas)(?:[ \t]+(?:sekali|banget|bgt|bngt))?[ \t]+(?:untuk|utk|buat|bwt|sebagai|dijadikan|dibuat|"
          r"dibangun|digunakan|dipakai|segala|semua|berbagai|aneka|macam|bermacam|usaha|bisnis|for)|(?:suitable|perfect|great)[ \t]+for")
# "Cocok untuk …" ends what names the asset (as before v4.5); "bisa untuk usaha" only takes the rest of its line.
_SUITABLE_HEAD = re.compile(r"(?i)\b(?:" + _SUITS + r")\b")
_SUITABLE_USE = re.compile(r"(?i)\b(?:" + _SUITS + r"|(?:bisa|dapat|dpt|bs)[ \t]+(?:untuk|utk|dijadikan|dibuat|dibangun|digunakan|dipakai))\b")
# A list item after "Cocok untuk :" ("🏥 Rumah sakit.", "- Kantor"); after a bare "Cocok segala usaha" it needs its mark.
_LIST_ITEM = {True: re.compile(r"^[^\w\n]*(?:\d{1,2}[ \t]*[.)][ \t]*)?[^\d\n:]{1,40}$"),
              False: re.compile(r"^[ \t]*(?:[^\w\s]+|\d{1,2}[ \t]*[.)])[ \t]*[^\d\n:]{1,40}$")}
_SURROUNDINGS = re.compile(
    r"(?i)(?:[±~>]|\+[ \t]*-|\+/-|\b(?:lebih[ \t]+dari|kurang[ \t]+lebih|kurleb|sekitar|hampir)\b)[ \t]*\d[\d.,]*[ \t]*"
    r"(?:unit[ \t]+|kk[ \t]+)?(?:rumah|hunian)\b"
    r"|\b(?:ribuan|ratusan|puluhan)[ \t]+(?:unit[ \t]+)?(?:rumah|hunian)\b"
    r"|\b(?:\d[\d.,]*[ \t]*(?:unit[ \t]+)?)?(?:rumah|hunian)(?=[ \t]+(?:yang[ \t]+|yg[ \t]+)?(?:ada[ \t]+)?(?:di[ \t]+)?"
    r"(?:sekitar|sekeliling)nya\b)"
    r"|\b(?:rumah|hunian|perumahan|pemukiman|permukiman)[ \t]+(?:warga|penduduk)\b"
    r"|\b(?:kawasan|area|lingkungan|daerah|wilayah|zona|tengah|padat|ramai)[ \t]+(?:padat[ \t]+)?(?:hunian|rumah[ \t]+warga)\b"
    r"|\bhunian[ \t]+(?:padat|warga|penduduk)\b")
_NEAR_CUE = re.compile(r"(?i)\b(?:dekat|dkt|deket|near|samping|sebelah|seberang|(?:ber)?hadapan[ \t]+dengan|"
                       r"walking[ \t]+distance(?:[ \t]+(?:to|ke))?|selangkah[ \t]+(?:ke|dari)|\d+[ \t]*(?:menit|mnt)[ \t]+(?:ke|dari)|"
                       r"akses[ \t]+(?:ke|dekat|mudah[ \t]+ke))\b")
_NEAR_ITEM_STOP = re.compile(r"(?i)\d|\b(?:dijual|disewakan|jual|sewa|cari|dicari|boleh|bisa|juga|atau|lokasi|harga)\b")


def _near_items_removed(line):
    """A line without the assets named as landmarks after a nearness cue ("dekat sekolah, rumah sakit, hotel")."""
    out, pos = [], 0
    for cue in _NEAR_CUE.finditer(line):
        if cue.start() < pos:
            continue
        out.append(line[pos:cue.end()])
        pos = cue.end()
        for item in re.finditer(r"[^,;/]+(?:[,;/]|$)", line[cue.end():]):
            text = item.group()
            if len(re.findall(r"[^\W\d_]+", re.sub(r"(?i)\b(?:dan|serta|and|&)\b", " ", text))) > 4 or _NEAR_ITEM_STOP.search(text):
                break
            # (also the words that would deny a house: "Dekat … Tol, Mall, Pasar")
            out.append(re.sub(r"(?i)\b(?:rumah|hotel|apart\w*|kantor|perkantoran|gedung|pabrik|gudang|pergudangan|ruko|villa|vila|"
                              r"hunian|kav(?:ling)?|tanah|mall|mal|plaza|plasa|kios|tower|soho|condo\w*|kos(?:t|an)?|"
                              r"homestay)\b", " ", text))
            pos = cue.end() + item.end()
    return "".join(out) + line[pos:]


def _cleaned_lines(body):
    """Lines without the suitable-use cue and the rest of its line (a list after "Cocok untuk :" too) and without what
    surrounds the property."""
    out, listing = [], None
    for line in body.split("\n"):
        if listing is not None:
            if line.strip() and _LIST_ITEM[listing].match(line) and len(line.split()) <= 6:
                continue
            listing = None
        cue = _SUITABLE_USE.search(line)
        if cue:
            rest = line[cue.end():]
            if rest.rstrip(" *_).").endswith(":"):
                listing = True
            elif not re.search(r"[^\W_]", re.sub(r"(?i)\b(?:usaha|bisnis|apa\s+saja|apapun|dll|dsb|antara\s+lain|seperti)\b",
                                                 " ", rest)):
                listing = False
            line = line[:cue.start()]
        out.append(_SURROUNDINGS.sub(" ", _near_items_removed(line)))
    return "\n".join(out)


@lru_cache(maxsize=8192)
def _without_side_mentions(body):
    """The text the asset is read from: what the property suits or what surrounds it is no asset. As before v4.5,
    everything after the first suitable-use cue is left out ("Cocok untuk kantor, gudang …" may run over several lines),
    unless the message opens with it ("Cocok untuk usaha\nRuko gandeng"); then only the cue's line (and a list after it)
    is."""
    cue = _SUITABLE_HEAD.search(body)
    if cue:
        head = _cleaned_lines(body[:cue.start()])
        if _use_categories(head) or len(re.findall(r"[^\W\d_]{2,}", head)) > 2:
            return head
    return _cleaned_lines(body)


def _use_categories(body):
    body = _VIEW_PHRASE.sub(" ", body)
    without_use = _INTENDED_USE.sub(" ", body)
    if without_use != body:
        found = _categories(normalized(without_use))
        if found:
            return found
    return _categories(normalized(body))


def _body_categories(body: str) -> list[str]:
    """Categories of a message body. "Rumah … Peruntukan: kantor & gudang mesin" asks for a house: the intended
    use names another asset only when nothing else does ("Bangunan 2 lantai, peruntukan kantor"). What a unit
    looks at ("View : parkiran gedung"), what it suits or what surrounds it is never the asset."""
    return _use_categories(_without_side_mentions(body))


_LOCATION_FIELD = re.compile(r"(?:lokasi|area|daerah)\s*[:\-]?\s*(.{3,100})")
# v4.8: "Service area terpisah", "Dry area", "area parkir": a room or facility of the property, never its location.
_FACILITY_AREA = re.compile(r"\b(?:service|servis|dry|wet|living|dining|cooking|kitchen|laundry|jemur\w*|parkir\w*|parking|play|"
                            r"bermain|garden|santai|duduk|makan|cuci|dapur|common|public|lounge|working|bbq|gym|pool|green|open|"
                            r"outdoor|indoor|sitting|family|keluarga|tamu)\s*$")
_FACILITY_AFTER = re.compile(r"^\s*(?:terpisah|tersendiri|sendiri|luas|lega|belakang|depan|samping|atas|bawah|parkir\w*|jemur\w*|cuci|"
                             r"dapur|bermain|santai|makan|tamu|keluarga|hijau|terbuka|outdoor|indoor)\b")
# "Area prefer CITRALAND": the wish word is no part of the place.
_WISH_PREFIX = re.compile(r"^(?:(?:prefer\w*|diutamakan|utamakan|diprioritaskan|prioritas\w*|lebih\s+(?:suka|baik)|kalau\s+bisa|"
                          r"klo\s+bisa|kalo\s+bisa)\s*:?\s+)+")
_REGION_SHORT = re.compile(r"\b(?:daerah|area|wilayah|kawasan|lokasi|sby|surabaya\s+bagian)\s+(barat|timur|utara|selatan|pusat)\b")
_GENERIC_FRAGMENTS = {"sekitarnya", "dan sekitarnya", "bebas", "mana saja", "strategis", "lokasi strategis", "premium",
                      "apik", "atas", "bagus", "oke", "ok", "mantap", "top", "prime", "elit", "elite", "istimewa", "ramai"}
# "Area 9,5 x 18 = 200 jt" is a floor area with its price, not a place.
_NOT_PLACE_FRAGMENT = re.compile(r"\b(?:harga|nego|tahun|thn|bulan|bln|budget|luas|ciamik|murah|strategis|sangat)\b"
                                 # v4.3: property terms on a location line are no places ("Lokasi: hook, row jalan 2 mobil").
                                 r"|\b(?:hook|pojok|row|carport|garasi|kt|km|lt|lb|hadap|semi|full|furnish\w*|kosongan|nett?|nego\w*|"
                                 r"pbg|imb|ipl|shm|shgb|hgb|ppjb|ajb|sertifikat|legalitas|surat|petok|girik|strata|shmrs|kpr|kpa|"
                                 r"cash|studio|bedroom|br|view|lantai|tower)\b")
_MEASURE_FRAGMENT = re.compile(r"\d\s*[x×]\s*\d|=|\d\s*(?:jt|juta|m|miliar|milyar|m2|m²|meter|mtr|ha|hektar|lantai|lt|kt|km|unit)\b")
# Things a property faces ("depan kampus ITS", "depan mall Delta Plaza", "depan Pakuwon Mall"); "cluster depan
# Citraland" is the front part of Citraland, no landmark.
_LANDMARK_NOUNS = (r"mall|mal|plaza|plasa|pasar|kampus|universitas|univ|sekolah|smp|sma|smk|sd|tk|rs|rsud|rumah\s+sakit|puskesmas|"
                   r"klinik|masjid|gereja|klenteng|vihara|stasiun|terminal|bandara|gerbang|gapura|pintu|patung|air\s+mancur|"
                   r"galery|galeri|gallery|kantor|bank|hotel|perumahan|perum|komplek|kompleks|apartemen|apartment|spbu|pom|"
                   r"supermarket|minimarket|indomaret|alfamart|restoran|resto|cafe|kafe|"
                   r"[a-z]+(?:[ \t]+[a-z]+){0,2}[ \t]+(?:mall|plaza|square|town\s+square)")
# A landmark the property is near is not where the property is.
_LANDMARK_SPLIT = re.compile(r"\b(?:(?:ter)?dekat|dkt|deket|dket|tidak jauh|tdk jauh|gak jauh|ga jauh|nggak jauh|menit (?:dari|ke)|mnt (?:dari|ke)|"
                             r"akses (?:ke|dekat|mudah ke|cepat ke|langsung|tol+)|near|sebelah|samping|seberang|walking distance|ke arah|"
                             r"(?:exit|pintu|gerbang)\s+tol|"
                             r"selangkah (?:ke|dari|menuju)|\d+(?:[.,]\d+)?\s*(?:km|kilo(?:meter)?|menit|mnt|jam)\s+(?:ke|dari|menuju)|"
                             # v4.4: facilities ("Fasilitas tersedia: kolam renang, … dan Eastcost Mall"), walking distance
                             # and a landmark the property faces ("depan Pakuwon Mall", "belakang McDonald") are no address.
                             r"fasilitas|jalan\s+kaki\s+(?:ke|dari)|"
                             r"(?:depan|belakang|seberang)\s+(?:(?:persis|langsung|tepat|pas|dgn|dengan)\s+)?(?:" + _LANDMARK_NOUNS + r"))\b"
                             r"|±\s*\d+(?:[.,]\d+)?\s*(?:km|menit|mnt|meter|mtr|m)\b")
# A comma item after a landmark continues its list ("dekat Citraland, Unesa, Lidah Kulon") unless it names a location
# of its own ("…, SELANGKAH KE EXIT TOL ROMOKALISARI, LOKASI PERGUDANGAN ROMOKALISARI - GRESIK").
_OWN_PLACE_CUE = re.compile(r"\b(?:lokasi|alamat|letak|berada|terletak|area|daerah|kawasan|di|jl|jln|jalan(?![ \t]+tol\b)|perumahan|cluster)\b")


def strip_landmarks(line):
    """A line without the landmarks a property is near (they are not where it is)."""
    if not _LANDMARK_SPLIT.search(line):
        return line
    out, near = [], False
    for segment in line.split(","):
        # "… Falkutas WM kedokteran dan Eastcost Mall", "tempat kuliner Raya Darmo Permai I": items joined by "dan"
        # continue the list; only a long statement or an own-place cue ends it.
        items = re.split(r"\s+(?:dan|serta|&|and)\s+", segment)
        if near and (_OWN_PLACE_CUE.search(segment) or max(len(item.split()) for item in items) > 7):
            near = False
        if near:
            continue
        head = _LANDMARK_SPLIT.split(segment)[0]
        near = head != segment
        out.append(head)
    return ",".join(out)


# "kecuali istimewa / bagus / murah": an exception for quality or price, no refused place.
_UNLESS_QUALITY = re.compile(r"(?:istimewa|bagus|menarik|murah|cocok|worth\w*|layak|spesial|special|oke|ok|sesuai|harga\w*|nego\w*)\b")
_NEG_MAU = r"\b(?:tidak|tdk|gak|ga|g|nggak|ngga|gk|ndak)\s*mau\b|\bgamau\b"
# R4: "tidak sampe Margomulyo", "jangan sampai X", "bukan X" refuse a place on a location line.
_NEG_REACH = r"\b(?:tidak|tdk|gak|ga|nggak|ngga|gk|ndak|jangan|jgn|jngn|jgan)\s*(?:sampe|sampai|smpe|smpai|sampek|nyampe|nyampai)\b|\bbukan\b"
_NEG_REACH_RE = re.compile(_NEG_REACH)
# "lokasi bukan gang buntu", "area bukan jalan sempit": what a refused location line describes is no place.
_NOT_A_PLACE = re.compile(r"\b(?:gang|gg|buntu|jalan|jln|jl|sempit|banjir|ts|tusuk|sate|hook|masuk|kecil|besar|rusak|lama|tua|"
                          r"renov\w*|kumuh|rawan|macet|pojok|kuburan|makam|sutet)\b")
# v4.4: a cross after a place refuses it ("Kedung cowek ❎️", "Surabaya Barat❌"); a tick keeps it ("Waru ✅").
_CROSS = "[\u274c\u274e\u2716\u2717\u2718\u26d4\U0001f6ab]\ufe0f?"
_CROSS_MARK = re.compile(_CROSS)
_TICK_MARK = re.compile("[\u2705\u2714\u2611]")
_REQUEST_NEGATIVE_SPLIT = re.compile(_NEG_MAU + r"|" + _NEG_REACH + r"|\bkecuali\b|\bselain\b(?!\s+(?:(?:yang|yg)\s+)?(?:itu|ini|di\s*atas|tsb|tersebut)\b)"
                                     # v4.3: "Rumah Sby Barat - no Northwest" refuses Northwest (never "no 22", "No. 5").
                                     r"|" + _CROSS + r"|\b(?:jangan|jgn|jngn|jgan)\b|\bexcept\b|\bno\b(?![.:]?\s*\d)|\bskip\b")
_LOCATION_LINE = re.compile(r"\b(?:wilayah|area|daerah|lokasi|kawasan|lingkungan|seputar|sekitar|arah|di)\b")
# Words that may follow a negation that comes after the noun ("Northwest jangan ditawarkan ya").
_NEG_TAIL = re.compile(r"^(?:\s|[!.\ufe0f]|\b(?:di)?(?:tawar|kasih|kirim|share|info)\w*\b|\b(?:ya+|yah|dong|deh|lagi|lg|please|pls|plis|ok|"
                       r"sama\s+sekali|ditempatkan|pokoknya)\b)*$")
_SEGMENT_BREAK = re.compile(r"[()\[\],;|]|\s[-–]\s")


def _split_negation(line):
    """(positive text, refused text) of one request line.

    Indonesian negates before the noun ("tidak mau Northwest", "jgn sampai Citraland") or after it
    ("Citraland only (Northwest jangan ditawarkan)"); the refused noun must not count as wanted."""
    strict_else = _STRICT_ELSE.search(line)
    if strict_else:
        return line[:strict_else.start()] + " " + (strict_else.group("else") or ""), ""
    positive, negative, rest = "", "", line
    while True:
        m = _REQUEST_NEGATIVE_SPLIT.search(rest)
        if not m:
            return re.sub(r"\(\s*\)", " ", positive + rest), negative
        boundary = _SEGMENT_BREAK.search(rest, m.end())
        end = boundary.start() if boundary else len(rest)
        if not _NEG_TAIL.match(rest[m.end():end]):
            # Before the noun: the rest of the line is refused ("tidak mau Kenjeran, Rungkut"), or the rest of
            # the bracket it was written in ("Wiyung (jgn sampai Citraland) kalau Waterplace juga bisa").
            close = rest.find(")", m.end()) if rest.count("(", 0, m.start()) > rest.count(")", 0, m.start()) else -1
            if close < 0:
                return re.sub(r"\(\s*\)", " ", positive + rest[:m.start()]), negative + " " + rest[m.end():]
            positive += rest[:m.start()]
            negative += " " + rest[m.end():close]
            rest = rest[close:]
            continue
        start = max((b.end() for b in _SEGMENT_BREAK.finditer(rest, 0, m.start())), default=0)
        if _CROSS_MARK.fullmatch(m.group()) and not rest[end:].strip() and not _TICK_MARK.search(rest):
            # "Area Kenjeran, Kedung cowek ❎️": a cross closing the line refuses all of it (after an earlier mark).
            start = 0
        head = rest[start:m.start()]
        # "Citraland only Northwest jangan": only what follows the strict marker is refused.
        marker = [x.end() for x in re.finditer(r"\b(?:only|hanya|khusus|saja|aja|wajib|harus)\b|!", head)]
        cut = marker[-1] if marker else 0
        positive += rest[:start] + head[:cut]
        negative += " " + head[cut:]
        rest = rest[end:]


# v4.5: street names many towns share ("Jl. Panglima Sudirman, Kota Madiun", "Basuki Rahmat Krian", "Jl. Pahlawan Rempoa"):
# the Surabaya street only when no place of another town is named with it; the newly added ones also need the message to
# name Surabaya or a place in it.
CITY_STREETS = frozenset({"jl pahlawan", "jl pemuda", "raya diponegoro", "panglima sudirman", "gubernur suryo", "basuki rahmat",
                          "ahmad yani", "teuku umar", "jl lombok"})  # v4.6: Teuku Umar (also Denpasar, Jakarta), Jl. Lombok
_SURABAYA_ONLY_STREETS = frozenset({"jl pahlawan", "jl pemuda", "panglima sudirman", "gubernur suryo", "jl lombok"})


def foreign_streets(found, text, places, listing=True):
    """The shared street names among ``found`` (places recognised in ``text``) that are not shown to be in Surabaya: a
    listing that names another town on the street's line (or anywhere, naming no Surabaya place), a request right after
    the street or in the label its line opens with; a street of ``_SURABAYA_ONLY_STREETS`` also without any Surabaya place."""
    streets = CITY_STREETS.intersection(found)
    if not streets:
        return set()

    def town(name):
        region = places.region_of(name)
        return name not in CITY_STREETS and region is not None and region not in SURABAYA_REGIONS

    others = [p for p in found if p not in CITY_STREETS]
    surabaya = any(places.canonical(p) == "surabaya" or places.region_of(p) in SURABAYA_REGIONS for p in others)
    out = set() if surabaya else set(streets & _SURABAYA_ONLY_STREETS)
    anywhere = listing and not surabaya and any(town(p) for p in others)
    for line in text.split("\n"):
        if not CITY_STREETS.intersection(places.recognize(line)):
            continue
        for m in places.pattern.finditer(line):
            street = places.canonical(m.group())
            if street not in CITY_STREETS:
                continue
            # A request: the town right after the street, or the label its line opens with ("Area Gresik : …").
            label = line[:line.find(":")] if 0 <= line.find(":") < m.start() else ""
            scope = line if listing else label + " " + re.split(r"[,;/(&]|\b(?:atau|dan|or)\b", line[m.end():m.end() + 30])[0]
            if anywhere or any(town(p) for p in places.recognize(scope)):
                out.add(street)
    return out


def development_overrides(found, places):
    """v4.6: places among ``found`` (a listing's places) that a development the listing names overrides: "Citra Harmoni /
    Cluster Stamford" is in Citra Harmoni (Taman, Sidoarjo), not in CitraLand's Stamford Place (Surabaya Barat). A cluster
    of a development the listing does not name, in another region than a development it names, is no evidence."""
    named = {places.canonical(p) for p in found}
    developments = [p for p in named if places.development(p) and places.development_of(p) is None]
    if not developments:
        return set()
    out = set()
    for place in named:
        own = places.development_of(place)
        region = places.region_of(place)
        if own is None or own in named or region is None:
            continue
        chain = places.ancestors(place)
        if any(d not in chain and place not in places.ancestors(d) and places.region_of(d) not in (None, region)
               for d in developments):
            out.add(place)
    return out


def _locations(text: str, places=None) -> list[str]:
    places = places or default_places()
    text = _REGION_SHORT.sub(lambda m: "surabaya " + m.group(1), text)
    locations = places.recognize(text)
    for line in text.splitlines():
        match = _LOCATION_FIELD.search(line)
        if match and (_FACILITY_AREA.search(line[:match.start()].lower()) or _FACILITY_AFTER.match(match.group(1).lower())):
            match = None
        if match and not _MEASURE_FRAGMENT.search(match.group(1).lower()):
            # "area - klampis - nirwana - baruk": every name separately, never the joined phrase.
            fragments = [_WISH_PREFIX.sub("", fragment.strip(" .-_()").lower()).strip(" .-_()")
                         for fragment in re.split(r"[,;/]|\s+atau\s+|\s+dan\s+|\s+&\s+|\s+[-–]\s+", match.group(1))]
            locations.extend(
                fragment for fragment in fragments
                if 2 < len(fragment) < 45 and fragment not in _GENERIC_FRAGMENTS
                and not _NOT_PLACE_FRAGMENT.search(fragment)
            )
    return list(dict.fromkeys(location.lower() for location in locations))[:16]


# v4.8: "Area prefer CITRALAND / Graha - Pakuwon boleh ditawarkan": next to places in Surabaya Barat a bare "Graha" is Graha
# Famili and a bare "Pakuwon" Pakuwon Indah; next to places in Surabaya Timur a bare "Pakuwon" is Pakuwon City.
_BARE_DEVELOPMENT = re.compile(r"(?<![\w-])(graha|pakuwon)(?![\w-])")
_BARE_NAMES = {"surabaya barat": {"graha": "graha famili", "pakuwon": "pakuwon indah"}, "surabaya timur": {"pakuwon": "pakuwon city"}}
_BARE_NAMES_ALL = frozenset({"graha", "pakuwon"})


def _bare_developments(location_text, locations, places):
    regions = {places.region_of(p) for p in locations} - {None}
    names = _BARE_NAMES.get(next(iter(regions))) if len(regions) == 1 else None
    if not names or not _BARE_DEVELOPMENT.search(location_text):
        return []
    spans = [(m.start(), m.end()) for m in places.loose_pattern.finditer(location_text)]
    out = []
    for m in _BARE_DEVELOPMENT.finditer(location_text):
        start = location_text.rfind("\n", 0, m.start()) + 1
        end = location_text.find("\n", m.end())
        line = location_text[start:end if end >= 0 else len(location_text)]
        if any(a <= m.start() < b for a, b in spans) or m.group(1) not in names:
            continue
        if len(re.findall(r"[a-z]+", line)) <= 8 or _LOCATION_LINE.search(line):
            out.append(names[m.group(1)])
    return out


# --------------------------------------------------------------------------- transaction intent
_BOTH_PAIR = re.compile(
    r"\b(?:di\s*)?(?:jual|sale|beli|buy)\w*\s*(?:/|\\|&|\+|,|\batau\b|\bor\b|\band\b|\bdan\b|\bmaupun\b|\bn\b)\s*(?:di\s*)?(?:sewa\w*|rent\w*|kontrak\w*|lease)\b"
    r"|\b(?:di\s*)?(?:sewa\w*|rent\w*)\s*(?:/|\\|&|\+|,|\batau\b|\bor\b|\band\b|\bdan\b|\bmaupun\b|\bn\b)\s*(?:di\s*)?(?:jual\w*|sale|beli\w*|buy)\b"
    r"|\b(?:di)?jual\s*(?:di)?sewa(?:kan)?\b|\b(?:di)?sewa(?:kan)?\s*(?:di)?jual\b"  # "JualSewa Rumah", "Jual Sewa"
    r"|\bfs\s*/\s*fr\b|\b(?:lease|rent)\s*/\s*sold\b|\bsold\s*/\s*(?:lease|rent)\b|\bbisa\s+(?:di)?sewa\w*\W{0,4}(?:\w+\W+){0,2}bisa\s+(?:di)?(?:beli|jual)\w*|\bbisa\s+(?:di)?(?:beli|jual)\w*\W{0,4}(?:\w+\W+){0,2}bisa\s+(?:di)?sewa\w*")
_TXN_WORD = r"\b(?:jual|beli|sewa|kpr|sell(?:ing)?|buy(?:ing)?|rent(?:ing)?|lease)\b"
# Agency taglines ("JUAL | BELI | SEWA | KPR PROPERTY", "Sell | Buy | Rent") and "jual beli" as one phrase
# (PPJB/AJB "Perjanjian Pengikatan Jual Beli", "proses jual beli") say what an agent does, not what is offered.
_TXN_SEQUENCE = re.compile(r"(?P<seq>" + _TXN_WORD + r"(?:\s*(?:[|/,&•·+-]|\bi\b)?\s*" + _TXN_WORD + r")+)[^\n]*")
_RENT_NOT_OFFER = re.compile(
    r"\b(?:masih|sudah|sdh|udah|sedang|lagi|kondisi|posisi|dalam\s+kondisi|ada)\s+(?:masih\s+)?(?:di|ter)?(?:sewa|kontrak)\w*"
    r"|\btersewa\w*|\bpenyewa\w*|\b(?:tidak|tdk|belum|blm|gak|ga|nggak|never)\s+pernah\s+(?:di|ter)?(?:sewa|kontrak)\w*"
    r"|\b(?:saat\s+ini|sekarang|skrg|existing|eksisting|pernah)\s+(?:masih\s+|sedang\s+)?(?:di|ter)?sewa\w*"
    r"|\bdisewa\s+(?:oleh|sampai|hingga|s/?d|sd|until|sejak|sampe)\b"
    r"|\b(?:uang|masa|sisa|kontrak|pajak|pph|hasil|pendapatan|income|nilai|yield)\s+sewa\b"
    r"|\bsewa\s*(?:nya)?\s*(?:saat ini|sekarang|existing|eksisting|berjalan|aktif|masih|sampai|hingga|s/?d|habis|berakhir|selesai)\b"
    r"|\b(?:cocok|biasa|ideal|layak|bagus|enak|potensial|berpotensi)\b[^\n]{0,40}?\b(?:di)?(?:sewa|kontrak)(?:kan|kn)\b"
    r"|\b(?:untuk|utk|buat)\s+(?:investasi\s+)?disewakan\b|\bdisewakan\s+(?:lagi|lg|kembali)\b"
    r"|\b(?:investasi|investment|invest|potensi|cashback|roi|penghasilan|passive\s+income|pasif\s+income|income\s+pasif)\b[^\n]{0,30}?\b(?:di)?sewa\w*"
    # v4.6: a sale pitch ("good investment, mudah disewakan") says the property rents out easily, no rent offer.
    r"|\b(?:mudah|gampang|laris)\s+(?:untuk\s+|utk\s+)?(?:di)?(?:sewa|kontrak)\w*"
    r"|\b(?:okupansi|occupancy|penghuni|tarif\s+kos\w*)\b[^\n]{0,40}?\bsewa\w*"
    r"|\bsewa\w*\s+(?:per\s+|/\s*)?(?:kamar|kmr|kost?)\b|\bkost?\s+per\s*bulan\b|\bex\.?\s*sewa\b|\bbekas\s+sewa\b|\bsewa\s+(?:kembali|lagi)\b"
    r"|\b(?:tanah|lahan|status)\s+(?:status\s+)?sewa\b|\bhak\s+sewa\b|\bsewa\s+(?:dari|ke)\s+(?:luar|pemda|pemkot|pemerintah|pelindo|negara)\b"
    r"|\bcalon\s+(?:penyewa|tenant)\w*|\bada\s+calon\b[^\n]{0,30}?\bsewa\w*|\bbisa\s+(?:di)?sewakan\s+(?:untuk|utk|buat|ke)\b"
    r"|\b(?:di)?sewakan\s+(?:ke\s+)?(?:tenant|penyewa)\b"
    # "Harga Jual 385 jt … dikurangi dengan harga oper sewa": the remaining lease, no rent offer.
    r"|\bharga\s+oper\s*(?:sewa|kontrak)\b|\bdikurangi\b[^\n]{0,40}?\b(?:oper\s*)?sewa\w*")
_RENT_OFFER = re.compile(r"\b(?:disewakan|disewa|dikontrakkan|dikontrakan|for\s+rent|for\s+lease|to\s+let|rent|sewa)\b")
_SALE_OFFER = re.compile(r"\b(?:dijual|jual|for\s+sale|sale|lelang|take\s+over|over\s+kredit)\b")
_REQ_RENT = re.compile(r"\b(?:renter|penyewa|menyewa|sewa|nyewa|disewa|disewakan|rent|rental|ngontrak|kontrak|lease|leasing)\b")
_REQ_RENT_NOT = re.compile(r"\b(?:tidak|tdk|gak|ga|g|nggak|ngga)\s+mau\s+(?:yang\s+|yg\s+)?(?:masih\s+)?(?:di|ter)?sewa\w*"
                           r"|\b(?:untuk|utk|buat|bisa)\s+(?:investasi\s+)?(?:di)?sewakan\w*|\bdisewakan\s+(?:lagi|lg|kembali)\b"
                           r"|\b(?:ada|sudah ada|sdh ada)\s+penyewa\w*|\b(?:sudah\s+|sdh\s+)?ada\s+(?:yg\s+|yang\s+)?(?:sewa|nyewa)\w*"
                           r"|\b(?:investasi|investment|invest|roi|potensi|yield|penghasilan|income|passive\s+income|cash\s*flow)\b[^\n]{0,30}?\b(?:di)?sewa\w*"
                           r"|\b(?:mudah|gampang|laris)\s+(?:untuk\s+|utk\s+)?(?:di)?(?:sewa|kontrak)\w*"
                           r"|\b(?:cocok|ideal|layak|potensial|berpotensi)\b[^\n]{0,40}?\b(?:di)?(?:sewa|kontrak)(?:kan|kn)\b"
                           r"|\bpenyewa\s*(?:nya)?\b|\bsewa\s*(?:nya)?\s*(?:berjalan|aktif)\b|\bsudah\s+tersewa\b")
_REQ_SALE = re.compile(r"\b(?:beli|membeli|pembeli|dibeli|buy|purchase|dijual|for\s+sale|take\s*over|over\s*kredit|lelang)\b")
# R6: an investor buys, even when the text mentions rental income.
_INVEST = re.compile(r"\b(?:invest\w*|roi|potensi\s+hasil|passive\s+income|pasif\s+income|income\s+pasif)\b")
_REQ_WEAK_SALE = re.compile(r"\b(?:buyer|investor|end\s+user)\b|" + _INVEST.pattern)
# Separate buyer and renter sections in one request post ("1. Buyer cari …  2. Renter cari …", "*BUYER* … *RENTER* …").
_REQ_BUYER_SECTION = re.compile(r"(?m)^\W*(?:\d+\s*[.)]\s*)?(?:buyer|investor)\b|\b(?:buyer|investor)\s+(?:req\w*|cari|need|butuh)\b|\breq(?:uest)?\s+(?:buyer|investor)\b")
_REQ_RENTER_SECTION = re.compile(r"(?m)^\W*(?:\d+\s*[.)]\s*)?renter\b|\brenter\s+(?:req\w*|cari|need|butuh)\b|\breq(?:uest)?\s+renter\b")


def _strip_taglines(text):
    def tagline(m):
        words = set(re.findall(r"[a-z]+", m.group("seq")))
        return " " if words & {"jual", "sell", "selling"} and words & {"beli", "buy", "buying"} else m.group()
    return _TXN_SEQUENCE.sub(tagline, text)


_SEEK = re.compile(r"\b(?:cari|dicari|mencari|nyari|carikan|butuh|dibutuhkan|membutuhkan)\b")
# R2 rule 3: a total "belasan jt … < 200 jt" for a building is a (likely yearly) rent; land stays a sale.
RENT_SIZED_MIN, SALE_SIZED_MIN = 10 * 10**6, 200 * 10**6
_RENT_SIZED_CATEGORIES = {"house", "apartment", "shophouse", "office", "warehouse", "commercial_building"}
# Ownership/financing words: a 185 jt "Strata Title" apartment or an "SHM, bisa KPR" house is for sale.
_OWNERSHIP = re.compile(r"\b(?:shm|shgb|hgb|ppjb|sppjb|ajb|strata|sertifikat|sertipikat|hak\s+milik|kpr|cicil\w*|angsur\w*|"
                        r"belum\s+pernah\s+(?:di)?(?:tempati|huni|pakai)|never\s+occupied)\b")


def _price_size(candidates, categories, text=""):
    """'rent' | 'sale' | None from the size and period of the first price statement (R2 rule 3)."""
    if not candidates:
        return None
    if any(_periodic(c) for c in candidates):
        return "rent"
    first = candidates[0]
    if first['basis'] == 'per_m2':
        return "sale"
    value = first['max'] if first['max'] is not None else first['min']
    if value is None:
        return None
    if value >= SALE_SIZED_MIN or "land" in categories:
        return "sale"
    if value >= RENT_SIZED_MIN and set(categories) & _RENT_SIZED_CATEGORIES:
        return "sale" if _OWNERSHIP.search(text) else "rent"
    return None


def _sale_and_rent_prices(candidates):
    """A total sale-sized price next to a period (rent) price: "Harga 8 M / 250 jt per tahun" offers both."""
    def value(c):
        return c['max'] if c['max'] is not None else c['min']
    # "Harga: Rp 125 Milyar" next to "Harga Sewa: Rp 5 M": a rent price without a period counts as yearly.
    totals = [value(c) for c in candidates if c['basis'] == 'total' and c['kind'] != 'rent' and value(c)]
    periods = [value(c) * (12 if c['basis'] == 'per_month' else 1) for c in candidates
               if (c['basis'] in ('per_year', 'per_month') or (c['kind'] == 'rent' and c['basis'] == 'total')) and value(c)]
    return bool(periods) and any(v >= SALE_SIZED_MIN and v >= 5 * max(periods) for v in totals)


def _explicit_transaction(text, classification, candidates=()):
    """R2 rule 1 (and R6 vocabulary): 'sale' | 'rent' | 'both' from the words (and period prices) a text states, else None."""
    if classification == "buyer_request":
        rent_text = _REQ_RENT_NOT.sub(" ", text)
        if _INVEST.search(text) and not _BOTH_PAIR.search(text):
            # R6: an investor buys, also when the request mentions the rental income ("bisa disewakan lagi, potensi
            # sewa 100 jt/th"); those income words and amounts are no rent wish.
            kept = "\n".join(line for line in text.split("\n") if not (_INCOME_LINE.search(line) or _RENT_NOT_OFFER.search(line)))
            rent_text, candidates = _REQ_RENT_NOT.sub(" ", kept), _price_candidates(kept)
        rent = bool(_REQ_RENT.search(rent_text)) or any(_periodic(c) for c in candidates)
        sale = bool(_REQ_SALE.search(rent_text))
        sections = _REQ_RENTER_SECTION.search(text) and _REQ_BUYER_SECTION.search(text)
        if _BOTH_PAIR.search(text) or (rent and sale) or sections:
            return "both"
        if rent:
            # "Renter request … budget max 10 M … boleh sewa 200 jt/tahun": a sale budget and a rent budget.
            return "both" if _sale_and_rent_prices(candidates) else "rent"
        return "sale" if sale or _REQ_WEAK_SALE.search(text) else None
    offer_text = _RENT_NOT_OFFER.sub(" ", text)
    rent = bool(_RENT_OFFER.search(offer_text))
    sale = bool(_SALE_OFFER.search(offer_text))
    if _BOTH_PAIR.search(text) or (rent and sale):
        return "both"
    both_prices = _sale_and_rent_prices(candidates)
    if rent:
        return "both" if both_prices else "rent"
    if sale:
        return "sale"
    if both_prices:
        # R6: "Ruko untuk investasi … Sudah tersewa 100 jt/th … Harga 3 M" sells a tenanted property.
        return "sale" if _INVEST.search(text) or _RENT_NOT_OFFER.search(text) else "both"
    if re.search(r"\b(?:buyer|beli|jual cepat)\b", text) or _INVEST.search(text):
        return "sale"
    return None


def _transaction(signal_text, classification, candidates, categories=(), own_text=None):
    """R2: explicit words, then "cari + property" = buy, then the price size/period, else unknown.
    ``own_text``: a split part's own text; its words win over the shared header of the bubble, whose
    words only count when the part states none ("Disewakan gudang: 1. … 2. …")."""
    text = _strip_taglines(_unmark(signal_text))
    if own_text is not None:
        own_plain = _strip_taglines(_unmark(own_text))
        explicit = _explicit_transaction(own_plain, classification, candidates)
        if not explicit:
            explicit = _explicit_transaction(text, classification)
            if explicit == "both" and classification == "property_listing" and candidates and not _sale_and_rent_prices(candidates):
                # "*LIST JUAL & SEWA APARTEMEN*" over parts that each state one price: a sale-sized total sells, a
                # period price rents; only an amount that could be either keeps both.
                first = candidates[0]
                value = first['max'] if first['max'] is not None else first['min']
                if any(_periodic(c) for c in candidates):
                    explicit = "rent"
                elif first['basis'] == 'total' and value and value >= SALE_SIZED_MIN:
                    explicit = "sale"
    else:
        explicit = _explicit_transaction(text, classification, candidates)
    if explicit:
        return explicit
    if classification == "buyer_request":
        size = _price_size(candidates, categories, text)
        if categories and _SEEK.search(text):
            # "Cari rumah … Budget 2 M" buys; a rent-sized budget ("cari rumah 80 jt") rents.
            return size or "sale"
        return size or "unknown"
    if classification == "property_listing" and candidates:
        return _price_size(candidates, categories, text) or "sale"
    return "unknown"


# --------------------------------------------------------------------------- strict locations
_STRICT_NEG = r"(?:tidak|tdk|gak|ga|g|nggak|ngga|gk|ndak)\s*mau\b|gamau\b"
_STRICT_ELSE = re.compile(
    r"\b(?:selain\s+(?:(?:yang|yg)\s+)?(?:itu|ini|di\s*atas|tsb|tersebut|(?:lokasi|area|daerah)\s+(?:itu|ini|tsb|tersebut|di\s*atas))\s*,?\s*(?:" + _STRICT_NEG + r")"
    r"|selain\s+(?:(?:di|area|daerah|lokasi|kawasan)\s+)?(?P<else>[^\n,;()!?]{3,40}?)\s*,?\s*(?:" + _STRICT_NEG + r")"
    r"|(?:lokasi|area|daerah)\s+lain\s+(?:" + _STRICT_NEG + r")"
    r"|(?:" + _STRICT_NEG + r")\s*(?:lokasi|area|daerah|tempat)\s+lain"
    r"|di\s*luar\s+(?:itu|ini|(?:lokasi|area|daerah)\s+(?:itu|ini|tsb|tersebut))\s*,?\s*(?:" + _STRICT_NEG + r"))")
_STRICT_SUFFIX = re.compile(r"\bonly\b|(?<=\w)only\b")
_STRICT_PREFIX = re.compile(
    r"\b(?P<marker>only|hanya|khusus|wajib|harus)\b(?P<mau>(?:\s+(?:mau|mencari|cari|dicari|yang|yg|area|daerah|lokasi|kawasan|wilayah|di|in|at|seputar|cluster|perumahan|komplek|kompleks)\b|\s*:)*)\s*(?P<phrase>[^\n]{2,90})")
_STRICT_SAJA = re.compile(r"\b(?:area|daerah|lokasi|wilayah|kawasan|di)\s+(?P<phrase>[^\n]{2,60}?)\s+saja\b")
_LOCATION_PREP = re.compile(r"\b(?:di|in|at|area|daerah|lokasi|kawasan|wilayah|seputar|cluster|perumahan|komplek|kompleks)\b")
# An unlisted name before "only" is a place only after a property or location word ("Rumah Palm Spring only").
_PLACE_CUE = re.compile(r"\b(?:rumah|ruko|gudang|tanah|kavling|kav|kaveling|apartemen|apartement|apartment|apart|apt|villa|vila|kantor|"
                        r"office|pabrik|gedung|lahan|kost|kos|di|in|at|area|daerah|lokasi|kawasan|wilayah|seputar|cluster|perumahan|"
                        r"perum|komplek|kompleks)\b")
_PHRASE_DROP = re.compile(r"\b(?:rumah|ruko|gudang|tanah|kavling|kav|kaveling|apartemen|apartement|apartment|apart|apt|unit|villa|vila|"
                          r"kantor|office|pabrik|hotel|lahan|kost|kos|cari|dicari|beli|sewa|jual|dijual|disewakan|mau|di|area|daerah|"
                          r"lokasi|kawasan|wilayah|cluster|perumahan|komplek|kompleks|yang|yg|in|at|the|raya|jl|jln|jalan|req|request|"
                          r"buyer|renter|hot|valid|urgent|only|hanya|khusus|wajib|harus|saja|aja|bebas|ya|nya|seputar)\b")
_PHRASE_STOP = re.compile(r"\b(?:harga|bu|murah|miring|cash|kpr|shm|hgb|ppjb|serius|principal|owner|pemilik|langsung|hadap|hook|pojok|"
                          r"lantai|lt|lb|furnish\w*|semi|kosongan|ready|siap|huni|baru|gress|nego|nett?|wa|chat|call|telp|foto|spec|spek|"
                          r"private|lift|depan|belakang|pinggir|danau|view|blok|block|tower|type|tipe|studio|br|bedroom|kamar|kt|km|row|"
                          r"main|road|nol|storage|jualan|deal|survey|viewing|sertifikat|zona|peruntukan|usaha|untuk|utk|buat|sudah|ada|"
                          r"tidak|gak|ga|minimal|maksimal|max|min|budget|luas|lebar|panjang|dimensi|end|user|investor|agent|agen|klien|"
                          r"client|segera|cepat|hari|besok|minggu|bulan|tahun|garasi|carport|parkir|parkiran|akses|kontainer|truk|jejer|"
                          r"gandeng|lebih|sekitar|sekitarnya|dsk|dskt|dsktnya|dekat|dkt|near|prefer|diutamakan|utama|bagus|mewah|bisa|boleh|"
                          r"terima|dan|atau|or|dengan|dgn|tanpa|non|no|bukan|full|kondisi|modern|minimalis|kost|putri|putra|mobil|"
                          r"meter|mtr|m2|harus|wajib|khusus|keluarga|jual|sewa|beli|cicil\w*|angsur\w*|dp|mana|manapun|mana2|"
                          r"semua|apa|apapun|aja|saja|sesuai|terserah|kotanya|daerahnya|lokasinya|bawah|atas|tawarkan|"
                          r"ditawarkan|itu|ini|gpp|aman|ok|oke|serious|seriously|japri|co-?broke|co-?brok|cobroke|weekend|weekday|"
                          r"marketing|sales|komisi|fee|direct|inbox|pm|dm|real|asli|legal|resmi|nomor|no\.?\s*hp|agency|"
                          r"properti|property|listing|stock|stok|cek|check|tanya|respon|fast|slow)\b|\d")


def _clean_phrase(phrase, digits=True):
    phrase = re.split(r"[()\[\]!?;:.,\n" + (r"\d" if digits else "") + r"]|\s[-–]\s|\b(?:budget|harga|lt|lb|luas|kt|km|kamar|hadap|"
                      r"prefer|diutamakan|minimal|min|max|tidak|tdk|gak|ga|jika|kalau|kalo|klo|yg|yang|untuk|utk|buat|dengan|dgn)\b", phrase)[0]
    phrase = re.sub(r"[*_~\"'“”‼️✅▶️•]+", " ", phrase)
    phrase = re.sub(r"^\s*\d+\s*[.)]\s*", " ", phrase)
    return re.sub(r"\s+", " ", phrase).strip()


def _free_places(phrase):
    """Unlisted place names from a strict phrase; any non-place word voids the phrase."""
    parts = [p for p in re.split(r"\s*/\s*|\s*,\s*|\s+atau\s+|\s+or\s+|\s+&\s+|\s+dan\s+|\s+n\s+", phrase) if p.strip()]
    if any(_PHRASE_STOP.search(part) for part in parts):
        return []
    out = []
    for part in parts:
        part = re.sub(r"\s+", " ", _PHRASE_DROP.sub(" ", part)).strip(" -")
        # A generic word alone ("hill", "central") names no place.
        if 3 <= len(part) <= 40 and len(part.split()) <= 4 and re.search(r"[a-z]{3}", part) \
                and not set(part.split()) <= GENERIC_PLACE_WORDS:
            out.append(part)
    return out


def _strict_locations(lines, places, wanted):
    """Places a buyer accepts exclusively, read from the strict line(s)."""
    found = []
    previous = ""
    near = re.compile(r"\b(?:dekat|dkt|near|sekitar|seputar|deket)\b")
    for raw in lines:
        line = re.sub(r"[*_~]", "", raw.lower()).strip()
        if not line:
            continue
        line = _REGION_SHORT.sub(lambda m: "surabaya " + m.group(1), line)
        strict_else = _STRICT_ELSE.search(line)
        if strict_else:
            head = line[:strict_else.start()]
            named = strict_else.group("else")
            if named is None:
                here = places.recognize(head, strict=True) or places.recognize(previous, strict=True) or [p for p in wanted if p != "surabaya"]
                found += here or (_free_places(_clean_phrase(head)) if len(head) <= 80 and _PLACE_CUE.search(head) else [])
                previous = line
                continue
            # "Selain Citraland tidak mau": the place named after "selain" is the only one accepted.
            here = places.recognize(named, strict=True) or _free_places(_clean_phrase(named, digits=False))
            if here:
                found += here
                previous = line
                continue
        positive = _split_negation(line)[0]
        # Unlisted names are only trusted on short requirement lines, never in prose.
        short = len(positive) <= 80
        hits = []
        suffix = _STRICT_SUFFIX.search(positive)
        if suffix:
            whole = positive[:suffix.start()]
            segment = re.split(r"[|(\[;:!?]|\s[-–]\s|✅|•|\d+\s*[.)]\s", whole)[-1]
            before = segment.split(",")[-1]
            if not near.search(before):
                known = places.recognize(before, strict=True)
                leftover = re.sub(r"\s+", " ", _PHRASE_DROP.sub(" ", re.sub(r"[^a-z ]+", " ", before))).strip()
                # "Serious buyer only", "Co-broke only": "only" qualifies a word that is not a place.
                if known or not leftover:
                    known = places.recognize(segment if known else whole, strict=True)
                    # "Sewa ruko Surabaya barat Loop only": an unlisted name between the region and "only" is the
                    # place the buyer insists on; the region only says where it lies.
                    ends = [m.end() for m in places.loose_pattern.finditer(before)]
                    tail = _free_places(_clean_phrase(before[ends[-1]:])) if ends and short else []
                    if tail and all(places.region_of(p) == p or p in ROOT_PLACES for p in known):
                        hits += tail
                    else:
                        hits += known
                    hits += places.recognize(positive[suffix.end():], strict=True)
                if not known and short and _PLACE_CUE.search(segment):
                    hits += _free_places(_clean_phrase(before))
        for m in _STRICT_PREFIX.finditer(positive):
            phrase = _clean_phrase(m.group("phrase"))
            # "hanya 1 jam dari Surabaya", "hanya dekat tol": distance or proximity, not an area.
            if re.match(r"\s*(?:\d|dekat|dkt|near|sekitar|deket)", m.group("phrase")) or \
                    re.search(r"\b(?:menit|mnt|jam|km|meter|mtr|dari|ke)\b", _clean_phrase(m.group("phrase"), digits=False)):
                continue
            prep = bool(m.group("mau") and _LOCATION_PREP.search(m.group("mau")))
            phrase_known = places.recognize(_clean_phrase(m.group("phrase"), digits=False), strict=True)
            if phrase_known:
                hits += phrase_known
            elif short and prep:
                hits += _free_places(phrase)
        for m in _STRICT_SAJA.finditer(positive):
            raw_phrase = m.group("phrase")
            phrase = _clean_phrase(raw_phrase, digits=False)
            named = places.recognize(phrase, strict=True)
            if re.search(r"\d", raw_phrase) or (_PHRASE_STOP.search(phrase) and not named == [phrase]):
                if not named or _PHRASE_STOP.search(re.sub("|".join(map(re.escape, named)) or "^$", " ", phrase)):
                    continue
            hits += named or (_free_places(phrase) if short else [])
        tail_saja = re.search(r"\b(?:saja|aja)\b", positive)
        if tail_saja and short and not hits:
            # "Araya 1 Surabaya saja", "Tanah Citraland Utama saja": a known place right before "saja".
            head = positive[:tail_saja.start()].rstrip(" !.,")
            ends = [m.end() for m in places.loose_pattern.finditer(head)]
            # At most two non-stop words may follow the place ("Manyar Kertoadi saja").
            gap = re.findall(r"[a-z]+", head[ends[-1]:]) if ends else None
            if not near.search(head) and gap is not None and len(gap) <= 2 and not any(_PHRASE_STOP.search(w) for w in gap):
                hits += places.recognize(head, strict=True)
        found += [p for p in hits if not (p == "surabaya" and len(hits) > 1)]
        previous = line
    out = []
    for place in dict.fromkeys(found):
        if not place or any(place != other and (re.search(r"(?<!\w)" + re.escape(place) + r"(?!\w)", other)
                                                or place in places.ancestors(other)) for other in found):
            continue  # the more specific place wins ("surabaya timur pakuwon city only")
        out.append(place)
    return out[:8]


# --------------------------------------------------------------------------- classification
_WHO = r"(?:buyer|renter|investor|pembeli|penyewa)"
_SECTION_HEADING = re.compile(r"(?m)^[^a-z0-9\n]*(?:\d{1,2}\s*[.)]\s*)?(?:#\s*" + _WHO + r"\b|(?:hot\s+|urgent\s+)?" + _WHO +
                              r"(?:\s*\d{1,2}\b|\s*\(\s*[a-z0-9]{1,2}\s*\)|\s*[:：]|[^a-z0-9\n]*$))")


# Request phrases; "Mau beli rumah …", "Ingin sewa …", "Looking for a warehouse …", "WTB", "*Renter Rumah*" ask for a
# property too ("yang mau sewa genset", "yang cari tanah … ada pilihan" offer one).
_REQUEST_PHRASES = re.compile(
    r"\b(?:masih\s+)?(?:dicari|mencari)\b|\b(?:buyer|renter|investor)\s+(?:need|request|req|cari|butuh|survey)\b"
    r"|\bkebutuhan\s+(?:buyer|renter)\b|(?<!yang )(?<!yg )\b(?:butuh|cari)\s+(?:segera\s+)?(?:beli|sewa|rumah|ruko|tanah|kavling|gudang|"
    r"pabrik|villa|vila|apartemen|apartement|apartment|apart|apt|kantor|office|hotel|gedung|kios|toko|kost?)\b|(?<!yang )(?<!yg )\b(?:mau|ingin|pengen|pingin|rencana)\s+(?:beli|sewa|cari|nyari)\b"
    r"|\blooking\s+for\b|\bwant(?:s|ed)?\s+to\s+(?:buy|rent)\b|\bwt[br]\b|^\s*(?:buyer|renter)\b")


def _classification(text: str) -> tuple[str, float]:
    lower = normalized(text)
    if not lower or lower in NOISE_EXACT or len(lower) < 18:
        return "ignored", 0.99
    # WhatsApp emphasis often splits phrases (for example ``dicari * sewa``).
    # Use a punctuation-free signal only for intent detection while preserving
    # the original normalised text for indexing and display.
    signal = re.sub(r"[^a-z0-9]+", " ", lower)
    request_hits = sum(marker in signal for marker in REQUEST_MARKERS)
    request_hits += len(_REQUEST_PHRASES.findall(signal))
    # Group posts commonly use only "HOT/URGENT REQUEST" as the title.
    if re.search(r"^.{0,80}\brequest\b", signal):
        request_hits += 1
    # Section headings of a request part: "#Renter 1", "*BUYER*…", "Renter (B)", "Buyer:".
    if _SECTION_HEADING.search(text.lower()):
        request_hits += 1
    listing_hits = sum(marker in signal for marker in LISTING_MARKERS)
    property_hits = sum(any(pattern.search(lower) for pattern in patterns) for patterns in _CATEGORY_RES.values())
    if not property_hits and _body_categories(text) == ["house"]:
        property_hits = 1  # specs of a landed home without the word "rumah" (landmarks "dekat … Mall" aside)
    if not property_hits and re.search(r"\blahan\b", lower):
        property_hits = 1  # "Lahan parkir dalam": no plot of land, but a property message all the same
    explicit_request = re.search(r"\b(?:dicari|buyer request|buyer need|request buyer|renter request|cari beli|cari sewa)\b", signal)
    sales_pitch = listing_hits or re.search(r"\b(?:ready|siap huni|bonus|furnished|pilihan yang tepat)\b", signal) or re.search(r"cari[^\n?]{0,100}\?", lower)
    concrete_stock = re.search(r"\bharga\b", signal) and re.search(r"\b(?:lt|luas tanah|lb)\b", signal)
    if concrete_stock and sales_pitch and not explicit_request:
        return "property_listing", 0.95
    if request_hits:
        return "buyer_request", min(0.99, 0.78 + request_hits * 0.06 + property_hits * 0.02)
    if listing_hits and property_hits:
        return "property_listing", min(0.98, 0.72 + listing_hits * 0.06 + property_hits * 0.02)
    if property_hits >= 1 and re.search(r"harga|budget|luas|\blt\b|\blb\b", lower):
        return "property_listing", 0.60
    return "ignored", 0.90


_SPACED_LETTERS = re.compile(r"(?<!\w)(?:[a-z] ){3,}[a-z](?!\w)")
_PREFERENCE = re.compile(r"\b(?:prefer\w*|diutamakan|utamakan|diprioritaskan|lebih\s+(?:suka|baik|bagus|disukai|disuka)|"
                         r"(?:kalau|kalo|klo|klu)\s+bisa|sebaiknya|nice\s+to\s+have)\b")
_PROPERTY_TERMS = re.compile(r'kamar|sertifikat|furnish|nego|carport|for sale|for rent|luas tanah|luas bangunan')
_EXTRA_PLACES = {'regency', 'national hospital', 'garden mansion', 'kawasan industri gresik',
                 'sidoarjo rangkah industrial estate', 'padepokan cahaya putra'}


_DIRECTIONS = ("utara", "timur", "selatan", "barat")
_DIRECTION_LETTERS = {"u": "utara", "t": "timur", "s": "selatan", "b": "barat"}
_FACING_PHRASE = re.compile(r"(?:hadap|hdp|facing)\s*[:=-]?\s*([^\n,.]{1,30})")
_FACING_LETTERS = re.compile(r"^[ \t]*([utsb])(?:[ \t]*[/&-][ \t]*([utsb]))?(?![\w])")
# A listing line that starts with the direction ("Utara, ppjb", "Selatan / Timur").
_FACING_BARE = re.compile(r"(?m)^[^a-z0-9\n]*(utara|timur|selatan|barat)(?:[ \t]*(?:/|-|&|dan)[ \t]*(utara|timur|selatan|barat))?[ \t]*(?:[,;]|$)")
_WISH_CLAUSE = re.compile(r"[,;()\[\]]|\s[-–]\s")


def _without_wishes(line):
    """A line without its preference clauses: "HARUS HADAP BARAT / UTARA (Hadap Barat lebih baik)" keeps the
    requirement, "Prefer hadap selatan" is a wish only (it never rules other directions out)."""
    while True:
        m = _PREFERENCE.search(line)
        if not m:
            return line
        start = max((b.end() for b in _WISH_CLAUSE.finditer(line, 0, m.start())), default=0)
        end = _WISH_CLAUSE.search(line, m.end())
        line = line[:start] + " " + line[end.start() if end else len(line):]


def facing_directions(lower, listing=False):
    """Facing directions a text states ("Hadap: Utara", "HARUS HADAP BARAT / UTARA", "Hadap: U"; a listing line
    "Utara, ppjb")."""
    found = set()
    for line in lower.splitlines():
        line = _without_wishes(line)
        for m in _FACING_PHRASE.finditer(line):
            phrase = m.group(1)
            found.update(d for d in _DIRECTIONS if re.search(rf"\b{d}\b", phrase))
            letters = _FACING_LETTERS.match(phrase)
            if letters:
                found.update(_DIRECTION_LETTERS[x] for x in letters.groups() if x)
    if listing and not found:
        for m in _FACING_BARE.finditer(lower):
            found.update(x for x in m.groups() if x)
    return [d for d in _DIRECTIONS if d in found]


_REQUEST_SIZE_LINE = re.compile(r"^\W*(?:gudang|tanah|lahan|kavling|kav|kaveling|pabrik|warehouse)(?:\s+kosong)?\s*[:=]?\s*"
                                r"((?:min(?:imal|im)?\.?\s*|[±~]\s*)?\d+(?:[.,]\d+)*(?:\s*(?:-|–|s/d|sd|sampai|hingga)\s*\d+(?:[.,]\d+)*)?)"
                                r"\s*(m2|m²|meter|mtr|m\b|ha\b|hektar\w*)?\W*$")


# "dekat pusat kota Surabaya" stays a wish to be near the centre (the bare check in matching_rules).
_CITY_CENTRE = re.compile(r"(?<!dekat )(?<!dkt )(?<!near )(?<!akses )(?<!ke )\b(?:(?:pusat|tengah)\s+kota\s+(?:surabaya|sby)"
                          r"|(?:surabaya|sby)\s+(?:pusat|tengah)\s+kota)\b")


def city_centre(lower):
    """A request's "pusat kota Surabaya" / "tengah kota Sby" as the region it names: Surabaya Pusat."""
    return _CITY_CENTRE.sub(" surabaya pusat ", lower) if "kota" in lower else lower


def parse_message(text: str, author: str | None = None, glossary=None) -> ParsedDocument:
    """Parse one WhatsApp bubble as a whole (legacy; ``parse_bubble`` splits multi-context bubbles).
    ``glossary``: legacy {alias: canonical} dict or a list of entry dicts ``{term|alias, canonical, kind, parent}``
    (kinds: synonym, location, category)."""
    return _parse_text(clean_text(text), author, glossary)


def _parse_text(original, author=None, glossary=None, split=None, own=None, kind=None):
    """``split``: precomputed (body, signature); ``own``: the part's own text without the shared header of
    its bubble. Prices, areas, the asset and the transaction come from the own text first, so a header never
    overrides them (the header still gives places). ``kind``: the classification of a split part whose own
    text does not classify on its own (taken from its bubble)."""
    model = _glossary_model(glossary)
    places = model.places
    body, signature = split if split is not None else split_contact(strip_top_contact(original))
    plain_body = _unmark(body)
    # v4.8: "Manyar2an" / "manyar-an" is "Manyar dsk" (the place and the area around it).
    text = expand_place_suffixes(apply_glossary(plain_body, glossary), places)
    lower = text.lower()
    flat = normalized(text)
    own_plain = _unmark(own) if own else None
    own_text = expand_place_suffixes(apply_glossary(own_plain, glossary), places) if own else None
    classification, confidence = _classification(text)
    if classification == 'ignored' and kind:
        classification, confidence = kind, 0.6
    # "HOT LISTING APARTMENT & OFFICE SPACE" over several condos: a part that names its own asset keeps it.
    own_categories = bool(own_plain) and bool(_body_categories(own_plain) or any(
        pattern.search(normalized(own_text)) for pattern, _ in model.categories))
    category_body = own_plain if own_categories else plain_body
    category_flat = normalized(own_text) if own_categories else flat
    category_text = normalized(_INTENDED_USE.sub(" ", _without_side_mentions(category_body)))
    categories = _body_categories(category_body)
    if classification == 'buyer_request' and 'land' in categories and len(categories) > 1 and re.search(r"hitung\s+tanah", category_text):
        # "Rumah harga BU … kalau ada hitung tanah boleh": a house valued by its land, not a plot.
        without = _body_categories(re.sub(r"(?i)hitung\s+tanah", " ", category_body))
        if 'land' not in without and without:
            categories = [c for c in categories if c != 'land']
    if classification == 'buyer_request' and len(categories) > 1:
        # "Note: jangan tawarkan ex hotel …", "Cari rumah, bukan ruko": a refused asset is not requested.
        wanted = _body_categories("\n".join(_split_negation(line)[0] for line in category_body.lower().split("\n")))
        if wanted:
            categories = [c for c in categories if c in wanted] or wanted
    title = None
    if classification == 'property_listing':
        title = re.split(r'\b(?:luas|lt|lb|kamar|harga|fasilitas)\b', category_text, maxsplit=1)[0]
        title_categories = _categories(title)
        # "Kavling B (tipe pojok)" titles a house unit of an estate whose specs say it is a house.
        if title_categories and not (title_categories == ['land'] and categories == ['house'] and _UNIT_MARK.match(title)):
            categories = title_categories
    if model.categories:
        scope = title if title is not None and _categories(title) else re.split(r"\b(?:cocok|ideal)\s+(?:untuk|utk|buat)\b", category_flat)[0]
        for pattern, key in model.categories:
            if key not in categories and pattern.search(scope):
                categories.append(key)
    if 'shophouse' in categories and re.search(r'rumah\s+usaha', category_flat) and not re.search(r'\brumah\b(?!\s+usaha)', category_flat):
        categories = [c for c in categories if c != 'house']
    land_min, land_max = _areas(own_text, "land") if own_text else (None, None)
    if land_min is None and land_max is None:
        land_min, land_max = _areas(text, "land")
    if land_min is None and land_max is None and "land" in categories:
        for line in lower.splitlines():
            if re.search(r"\b(?:uk|ukuran|luas|luasan)\b", line):
                land_min, land_max = _range_from_line(line)
                if land_min is not None or land_max is not None:
                    break
    if land_min is None and land_max is None and classification == 'buyer_request' and 'apartment' not in categories:
        for line in lower.splitlines():
            # "Luas 300-500", "minimal 500 m²": the size a buyer asks for.
            if (re.search(r'\b(?:luas|luasan)\b.*\d', line) or re.match(r'\W*(?:min(?:imal|im)?\.?|minim)\s*\d+(?:[.,]\d+)?\s*(?:m2|m²|meter|mtr)\b', line)) \
                    and not re.search(r'\b(?:bangunan|lb|parkir)\b', line):
                land_min, land_max = _range_from_line(line)
                if re.search(r'ke atas|keatas', line): land_max = None
                break
            # v4.4: "Gudang 1500-2000", "Tanah 300-500 m2": the size written right after the asset on its own line.
            sized = _REQUEST_SIZE_LINE.match(line)
            if sized:
                lo, hi = _range_from_line(sized.group(1) + (" " + sized.group(2) if sized.group(2) else ""))
                stated = [v for v in (lo, hi) if v]
                if stated and (min(stated) >= 50 or (sized.group(2) or "").startswith(("ha", "hek"))):
                    land_min, land_max = lo, hi
                    break
    if categories and set(categories) <= {'apartment', 'office'} and land_max is not None and land_max <= 80 and re.search(
            r"\blt(?:\b|(?=\d))\.?\s*[:=]?\s*" + f"{land_max:g}" + r"(?![\d.,]|\s*(?:m2|m²|m\b|meter|mtr|x|×))", lower):
        land_min = land_max = None  # "Apartemen … Lt 36": the floor, not a land area
    building_min, building_max = _areas(own_text, "building") if own_text else (None, None)
    if building_min is None and building_max is None:
        building_min, building_max = _areas(text, "building")
    if land_min is None and land_max is None and classification == 'property_listing' and set(categories) & _LANDED \
            and not set(categories) & {'apartment', 'office'}:
        for line in lower.splitlines():
            # "Luas 135m2 (9x15)", "Luas 700 m2 @ 200 jt/th.": a landed property's size is its land area.
            m = re.search(r"\bluas(?:an)?\b(?![ \t]*(?:bangunan|bang\b|lb|parkir|kamar|unit|gross|semi|sg|nett?))[^\n\d]{0,12}(\d[^\n]*)", line)
            if m and re.match(r"[\d.,]+[ \t]*(?:m2|m²|meter|mtr|m\b)|[\d.,]+[ \t]*[x×][ \t]*\d", m.group(1)):
                land_min, land_max = _range_from_line(m.group(1))
                if building_min is None and building_max is None and set(categories) & {'warehouse', 'factory', 'commercial_building'}:
                    building_min, building_max = land_min, land_max  # a warehouse's "Luas 700 m2" is also its floor area
                break
    sides = _length_width((own_text or text).lower()) if classification == 'property_listing' and categories and \
        (None is land_min is land_max or None is building_min is building_max) else None
    if sides:
        # The building's floor plan for a warehouse/factory/commercial building (its land too when no size is stated),
        # an apartment or office; the plot for a house, shophouse, villa or land.
        area, kind = sides
        built = set(categories) & {'warehouse', 'factory', 'commercial_building', 'apartment', 'office'}
        bare = None is land_min is land_max is building_min is building_max
        if (kind == 'building' or (kind is None and built)) and building_min is None and building_max is None:
            building_min = building_max = area
            if kind is None and bare and set(categories) & _LANDED:
                land_min = land_max = area
        elif kind != 'building' and land_min is None and land_max is None and set(categories) & _LANDED:
            land_min = land_max = area
    if land_min is None and land_max is None and classification == 'property_listing' and set(categories) & _LANDED \
            and not set(categories) & {'apartment', 'office'}:
        land_min, land_max = _fallback_land(own_plain or plain_body)
    if 'apartment' in categories and None is land_min is land_max is building_min is building_max:
        # Apartments state the unit size as "Luas 93 m2" (nett preferred over semi gross).
        building_min, building_max = _unit_area(lower)
    price_text = text
    if own_text and _price_candidates(own_text):
        price_text = own_text
    # Struck-through old prices ("~Rp 7,5 M~ 6,9 M"), discounts ("TURUN 50JT"), reference prices and running costs
    # are not the asking price; they only count when nothing else states a price.
    asking_source = own if price_text is own_text else body
    asked, asking = _asking_text(asking_source), None
    if asked != asking_source:
        asking = apply_glossary(_unmark(asked), glossary)
        if _price_candidates(asking):
            price_text = asking
    # "Harga 2 M, turun harga menjadi 1,6 M", "Harga 1,8M turun Harga jadi 1,5M": the reduced price counts.
    reduced_last = price_text in (text, own_text, asking)
    if classification == 'property_listing' and re.search(r'turun harga|harga turun', price_text.lower()):
        current = [line for line in price_text.splitlines() if re.match(
            r'\s*harga\s*(?:(?:turun|baru|sekarang|now)\s*(?:(?:jadi|jd|menjadi|ke)\s*)?)?[:=]?\s*(?:rp\.?\s*)?\d', line, re.I)]
        if current:
            # The reduced sale price is the last "Harga" line; a rent offer stays a separate statement.
            rent_lines = [line for line in price_text.splitlines() if line not in current and re.search(r'\d', line)
                          and (_RENT_WORD.search(line.lower()) or _line_basis(line.lower()) in ('per_year', 'per_month'))]
            price_text = '\n'.join([current[-1], *rent_lines])
    candidates = _price_candidates(price_text)
    transaction = _transaction(lower, classification, candidates, categories, own_text.lower() if own_text else None)
    periods = ('per_year', 'per_month')
    sale_prices = [c for c in candidates if c['kind'] != 'rent' and c['basis'] not in periods]
    if reduced_last and classification == 'property_listing' and \
            re.search(r'turun\s+(?:harga|jadi|ke|menjadi)|harga\s+turun', price_text.lower()):
        sale_prices = sale_prices[::-1]  # "Harga 2 M, turun harga menjadi 1,6 M": the reduced price counts
    rent_prices = [c for c in candidates if c['kind'] == 'rent' or c['basis'] in periods]
    primary = rent = None
    if transaction == 'rent':
        primary = (rent_prices or [c for c in candidates if c['kind'] != 'sale'] or candidates or [None])[0]
    elif transaction == 'both':
        primary = sale_prices[0] if sale_prices else None
        rent = rent_prices[0] if rent_prices else None
        if rent is None and primary and primary['basis'] == 'total' and primary['max']:
            # "JualSewa Rumah … Rp 2.500.000.000 / Rp 70.000.000": the far smaller second amount is the rent.
            rent = next((c for c in sale_prices[1:] if c['basis'] == 'total' and c['max'] and
                         RENT_SIZED_MIN <= c['max'] <= primary['max'] / 5), None)
    elif transaction == 'sale':
        primary = (sale_prices or candidates or [None])[0]
    else:
        primary = candidates[0] if candidates else None

    def bounds(candidate):
        if not candidate:
            return None, None, None
        if classification == 'buyer_request' and candidate['approx']:
            lo, hi = candidate['approx']
        else:
            lo, hi = candidate['min'], candidate['max']
            if classification == 'buyer_request' and lo == hi and hi is not None:
                lo = None
        return lo, hi, candidate['basis']

    price_min, price_max, price_basis = bounds(primary)
    rent_min, rent_max, rent_basis = bounds(rent)
    # A refused direction ("Tidak mau hadap barat", "❌️Hadap Barat❌️") is no wanted facing (matching_rules rejects it).
    facing = facing_directions(lower, True) if classification == 'property_listing' else \
        facing_directions("\n".join(_split_negation(line)[0] for line in lower.splitlines()))
    exclusions = []
    for line in lower.splitlines():
        if _STRICT_ELSE.search(line):
            continue  # "selain Citraland tidak mau" names the only area wanted, not an exclusion
        if classification == 'buyer_request':
            # Places refused before or after the noun ("jgn sampai Citraland", "Northwest jangan ditawarkan").
            refused_text = _split_negation(line)[1]
            for m in places.loose_pattern.finditer(refused_text):
                # "Mulyosari yang row jalan sempit", "Jemursari Regency": only part of that place is refused.
                if re.match(r"\s+(?:yang|yg|regency|residence|indah|permai|park|garden|village|town|city|mansion|estate|"
                            r"cluster|hills?|asri|baru|raya)\b", refused_text[m.end():]):
                    continue
                exclusions += [m.group(), places.canonical(m.group())]
            if refused_text.strip() and _NEG_REACH_RE.search(line) and (
                    _LOCATION_LINE.search(line) or places.recognize(_split_negation(line)[0])):
                # "wilayah Surabaya ke arah Waru, tidak sampe Margomulyo atau DKP": every refused name on a
                # location line is excluded, also one the place list does not know.
                # v4.8: "tapi gak sampai osowilangun gak sampai buduran kecuali istimewa": every refused name on its own.
                for expression in re.split(r"[,;/()\[\]!?]|\s+(?:atau|dan|and|or|n|&|maupun)\s+|" + _NEG_REACH + r"|\bkecuali\b.*$",
                                           refused_text):
                    expression = re.sub(r"^(?:(?:area|daerah|lokasi|wilayah|kawasan|ke|di|arah)\s+)+", "",
                                        expression.strip(" .-_*❌:"))
                    if 2 <= len(expression) <= 30 and len(expression.split()) <= 3 and not re.search(r"\d", expression) \
                            and not _PHRASE_STOP.search(expression) and not _NOT_A_PLACE.search(expression):
                        exclusions += [expression, places.canonical(expression)]
        negative = re.search(r"(?:❌|\bno\b|\bnon\b|" + _NEG_MAU + r"|\bkecuali\b|\bselain\b(?!\s+(?:(?:yang|yg)\s+)?(?:itu|ini|di\s*atas)\b))\s*(.+)", line)
        if negative:
            soft = re.search(r"\b(?:kecuali|selain)\b", negative.group(0)[:12])
            for expression in re.split(r'[,;/]|\s+atau\s+|\s+dan\s+' if soft else r'[,;/]|\s+atau\s+', negative[1]):
                expression = re.sub(r'^(?:(?:tidak|tdk|gak|ga|g|nggak|ngga|gk)\s*mau|area|daerah|lokasi)\s+', '', expression.strip(' .-_*❌'))
                if soft:
                    expression = re.split(r'[()\[\]!]', expression)[0].strip(' .-_*')
                    if len(expression.split()) > 4 or _UNLESS_QUALITY.match(expression):
                        continue  # "kecuali istimewa": unless it is exceptional, nothing is refused
                    group = re.match(r'(?:grup|group|grp)\s+(.+)', expression)
                    if group:
                        exclusions.append(group.group(1).strip())
                if re.search(r"[a-z]{2,}", expression): exclusions.append(expression)  # never "️)" from "❌️)"
    if classification == 'buyer_request':
        # A place the buyer also asks for ("Jemursari dan sekitarnya") is never excluded as a whole.
        asked = set(places.recognize("\n".join(_split_negation(line)[0] for line in lower.splitlines()), strict=True))
        exclusions = [e for e in exclusions if places.canonical(e) not in asked]
    requirements = []
    # v4.3: SHM/HGB come from the legal-title reading ("ga mau SHM/HGB" requires neither; "SHMRS" is no SHM).
    legal = legal_requirement(lower)[0] if classification == 'buyer_request' else legal_statuses(lower)
    for keyword in ("siap huni", "siap pakai", "akses kontainer", "jalan raya", "hook", "shm", "hgb", "non lsd", "dekat tol", "parkiran luas", "full furnish", "minimalis modern"):
        if keyword == "hook":
            found = corner_lot(lower, classification == 'buyer_request')  # v4.6: "Rumah Pojok", "posisi sudut", "corner"
        else:
            found = keyword in legal if keyword in ("shm", "hgb") else keyword in lower
        if found:
            requirements.append(keyword)
    contact_name, contact_phones = extract_contact(original)
    place_lower = lower
    if classification == 'buyer_request':
        # v4.4: "rumah di pusat kota Surabaya" asks for the Surabaya Pusat region (a listing's "tengah kota" is a pitch).
        place_lower = city_centre(lower)
        location_lines = [_split_negation(line)[0] for line in place_lower.splitlines()]
    else:
        location_lines = [strip_landmarks(line) for line in lower.splitlines()]
    # Letter-spaced emphasis ("C I T R A L A N D") still names a place.
    location_text = _SPACED_LETTERS.sub(lambda m: m.group().replace(' ', ''), '\n'.join(location_lines))
    locations = _locations(location_text, places)
    foreign = foreign_streets(locations, location_text, places, classification == 'property_listing')
    if classification == 'property_listing':
        foreign |= development_overrides(locations, places)
    locations = [p for p in locations if p not in foreign]
    locations += model.user_places(location_text)
    if classification == 'buyer_request':
        locations += _bare_developments(location_text, locations, places)
    strict_locations = _strict_locations(place_lower.splitlines(), places, locations) if classification == 'buyer_request' else []
    if strict_locations:
        # v4.8: "Kavling Graha Only OR Pakuwon Indah": the bare development is Graha Famili (see _bare_developments).
        regions = {places.region_of(p) for p in locations if p not in _BARE_NAMES_ALL} - {None}
        names = _BARE_NAMES.get(next(iter(regions))) if len(regions) == 1 else None
        strict_locations = list(dict.fromkeys((names or {}).get(p, p) for p in strict_locations))
    locations += [p for p in strict_locations if p not in locations]
    return ParsedDocument(
        classification=classification,
        confidence=confidence,
        transaction_type=transaction,
        categories=categories,
        locations=list(dict.fromkeys(locations))[:20],
        land_area_min=land_min,
        land_area_max=land_max,
        building_area_min=building_min,
        building_area_max=building_max,
        price_min=price_min,
        price_max=price_max,
        price_basis=price_basis,
        rent_price_min=rent_min,
        rent_price_max=rent_max,
        rent_price_basis=rent_basis,
        negotiable=bool(re.search(r"nego|negotiable|harga bu|butuh uang", lower)),
        facing=facing,
        exclusions=list(dict.fromkeys(exclusions))[:12],
        requirements=requirements,
        location_strict=bool(strict_locations),
        strict_locations=strict_locations,
        contact_name=(contact_name or '')[:100] or author_name(author),
        contact_phone=contact_phones[0] if contact_phones else None,
        contact_phones=contact_phones,
        normalized_text=flat,
    )


# --------------------------------------------------------------------------- corner lot (v4.6)
# A corner lot by any name: "hook", "hoek", "Rumah Pojok", "posisi sudut", "corner (unit)" ("sudut pandang" is a viewpoint).
HOOK_WORDS = r"(?:hook|hoek|pojok(?:an)?|corner|sudut(?![ \t]+(?:pandang|ruang\w*|kota|mata)\b))"
HOOK = re.compile(r"(?<!\w)" + HOOK_WORDS + r"(?!\w)")
_NOT_HOOK = re.compile(r"\b(?:bukan|tidak|tdk|non|tanpa|no|gak|ga|nggak|ngga)[ \t]*[-:]?[ \t]*(?:(?:posisi|unit|rumah)[ \t]+)?$")
# "Unit hook boleh", "boleh hook", "hook ga masalah": the buyer allows a corner lot, never requires one.
_HOOK_ALLOWED = re.compile(r"\b(?:boleh|bisa|ok|oke|gpp)[ \t]+(?:juga[ \t]+)?" + HOOK_WORDS + r"\b|\b" + HOOK_WORDS +
                           r"[ \t]+(?:juga[ \t]+)?(?:boleh|bisa|ok|oke|gpp|ga[ \t]*papa|gak[ \t]*papa|"
                           r"(?:ga|gak|tidak|tdk|nggak|ngga|no)[ \t]+(?:masalah|problem))\b")


def corner_lot(lower, request=False):
    """A listing that is a corner lot, or a buyer who requires one (refused or merely allowed corners do not count)."""
    if request:
        lower = _HOOK_ALLOWED.sub(" ", "\n".join(_split_negation(line)[0] for line in lower.splitlines()))
    return any(not _NOT_HOOK.search(lower[max(0, m.start() - 12):m.start()]) for m in HOOK.finditer(lower))


# --------------------------------------------------------------------------- property terms (v4.3)
# Read from lower-case text without WhatsApp marks; matching_rules compares the results (R8).
LEGAL_LABELS = {"shm": "SHM", "hgb": "HGB", "surat_ijo": "Surat Ijo", "strata": "Strata title (SHMRS)", "ppjb": "PPJB",
                "ajb": "AJB", "petok": "Petok D / Girik", "hpl": "HPL"}
# In priority order: "SHM strata" / "SHM-RS" is an apartment title, never a landed SHM.
_LEGAL_RES = [(key, re.compile(pattern)) for key, pattern in (
    ("strata", r"\bsh\s*m?\s*-?\s*s?rs\b|\bshm\s*-?\s*(?:sarusun|strata)\b|\bstrata(?:\s*-?\s*title)?\b|\bsrs\b"
               r"|hak\s+milik\s+(?:atas\s+)?satuan\s+rumah\s+susun"),
    # "SHM", "S H M", "S.H.M" (spaced or dotted letters).
    ("shm", r"\bs[ \t]?\.?[ \t]?h[ \t]?\.?[ \t]?m\b|serti[fp]ikat\s+hak\s+milik|\bhak\s+milik\b"),
    ("hgb", r"\bs?hgb\b|\b(?:s[ \t]?\.?[ \t]?)?h[ \t]?\.?[ \t]?g[ \t]?\.?[ \t]?b\b|hak\s+guna\s+bangunan"),
    # "Surat Ijo", "Surat : Hijau", "S.Ijo", "IPT".
    ("surat_ijo", r"\b(?:surat|srt|sertifikat|serti)[ \t]*[:.=]?[ \t]*(?:ijo|hijau)\b|\bs\.[ \t]?(?:ijo|hijau)\b|\bipt\b"
                  r"|\bi[zj]in\s+pemakaian\s+tanah\b"),
    ("ppjb", r"\bs?ppjb\b"),
    ("ajb", r"\bajb\b"),
    ("petok", r"\bpetok\s*-?\s*d?\b|\bgirik\b|\blett?er\s*-?\s*c\b"),
    ("hpl", r"\bhpl\b"))]
_LEGAL_ANY = re.compile("|".join(p.pattern for _, p in _LEGAL_RES))
_LEGAL_NEGATION = re.compile(r"\b(?:bukan|non|tidak|tdk|tanpa|belum|blm|ex|eks|bekas)\b\s*[-:]?")
# Clause boundaries: a negation, a preference or a condition only reaches the titles of its own clause.
_LEGAL_CLAUSE = re.compile(r"[,;.!?()\[\]\n]|\s[-–]\s|\b(?:tapi|tetapi|namun|sedangkan)\b")
# Words that may stand between a negation and the title it negates ("bukan hak pakai / HPL").
_LEGAL_GAP = re.compile(r"\s|/|&|\+|\batau\b|\bdan\b|\bor\b|\bhak\s+pakai\b|\bserti[fp]ikat\b|\bsurat\b|\bstatus\b|\blegalitas\b")
# "Pembayaran KPR pastikan sudah perpanjang HGB-nya", "paham trx AJB": a remark about the process, no requirement.
_LEGAL_CONDITION = re.compile(r"\b(?:pastikan|pastiin|perpanjang\w*|diperpanjang|paham|trx|transaksi|proses|balik\s*nama|pecah|"
                              r"urus\w*|biaya|pajak|ngerti|mengerti)\b")
# Interior finishes ("kitchen set HPL", "meja HPL") are no legal title: HPL counts next to a legal word only.
_HPL_LEGAL = re.compile(r"serti|hgb|ppjb|ajb|legal|status|\bhak\b|surat|\bdi\s*atas\b")
_HPL_FINISH = re.compile(r"kitchen|meja|lemari|kabinet|cabinet|laminat|finishing|\btop\b|table|pintu|furni|\bset\b|lantai|dinding")
# "SHM, surat ijo juga boleh": an accepted alternative, no requirement of its own.
_LEGAL_ACCEPT_AFTER = re.compile(r"^\W{0,3}(?:juga\s+|jg\s+)?(?:boleh|bisa|gpp|gapapa|ga\s*papa|gak\s*papa|nggak\s*papa|tidak\s+apa|oke|ok|"
                                 r"masuk|diterima|dipertimbangkan|welcome)\b")
_LEGAL_ACCEPT_BEFORE = re.compile(r"\b(?:boleh|terima|menerima|termasuk)\s+(?:juga\s+)?$")
# Mortgage-bankable titles; Surat Ijo, Petok D / Girik and a bare PPJB are not (R8 financing).
_BANKABLE = frozenset({"shm", "hgb", "strata", "ajb"})


def _legal_spans(text):
    out, masked = [], text
    for key, pattern in _LEGAL_RES:
        found = list(pattern.finditer(masked))
        if key == "hpl":
            found = [m for m in found if _HPL_LEGAL.search(text[max(0, m.start() - 30):m.end() + 30])
                     and not _HPL_FINISH.search(text[max(0, m.start() - 25):m.end() + 15])]
        out += [(key, m.start(), m.end()) for m in found]
        if found:
            masked = pattern.sub(lambda m: " " * len(m.group()), masked)
    return out


def _clause_start(text, start):
    return max((m.end() for m in _LEGAL_CLAUSE.finditer(text, 0, start)), default=0)


def _clause(text, start, end):
    after = _LEGAL_CLAUSE.search(text, end)
    return text[_clause_start(text, start):after.start() if after else len(text)]


def _legal_negated(text, start):
    """"bukan surat ijo", "non SHM", "bukan hak pakai / HPL": a negation in the same clause with only legal words
    between it and the title."""
    head = text[max(_clause_start(text, start), start - 40):start]
    found = list(_LEGAL_NEGATION.finditer(head))
    return bool(found) and not _LEGAL_GAP.sub("", _LEGAL_ANY.sub("", head[found[-1].end():])).strip()


@lru_cache(maxsize=16384)
def legal_statuses(text):
    """Legal titles a listing states ("SHGB, bisa SHM" → {hgb, shm}); negated ones ("bukan surat ijo") do not count."""
    if not _LEGAL_ANY.search(text):
        return frozenset()
    return frozenset(key for key, start, _ in _legal_spans(text) if not _legal_negated(text, start))


@lru_cache(maxsize=16384)
def legal_requirement(text):
    """(required, refused) legal titles of a buyer request: "Cari yang Surat Ijo" requires Surat Ijo, "ga mau
    SHM/HGB" refuses both, "Petok juga boleh" or "prefer SHM" requires nothing."""
    required, accepted, refused = set(), set(), set()
    if not _LEGAL_ANY.search(text):
        return frozenset(), frozenset()
    for line in text.split("\n"):
        if not _LEGAL_ANY.search(line):
            continue
        positive, negative = _split_negation(line)
        refused.update(key for key, start, end in _legal_spans(negative)
                       if not _LEGAL_CONDITION.search(negative[_clause_start(negative, start):start]))
        for key, start, end in _legal_spans(positive):
            clause = _clause(positive, start, end)
            if _LEGAL_CONDITION.search(positive[_clause_start(positive, start):start]):
                continue  # "Pembayaran KPR pastikan sudah perpanjang HGB": a remark about the process
            strong = re.match(r"\s*(?:saja|aja|only|doang|wajib|harus|mutlak)\b", positive[end:end + 12]) or \
                re.search(r"\b(?:harus|wajib|hanya|only|khusus|mutlak)\s*(?:yang\s+|yg\s+)?$", positive[max(0, start - 16):start])
            if _legal_negated(positive, start):
                refused.add(key)
            elif not strong and (_PREFERENCE.search(clause) or _LEGAL_ACCEPT_AFTER.match(positive[end:end + 30]) or
                                 _LEGAL_ACCEPT_BEFORE.search(positive[max(0, start - 20):start])):
                accepted.add(key)
            else:
                required.add(key)
    if required:
        required |= accepted
    return frozenset(required - refused), frozenset(refused - required)


# Apartment unit types: studio, 1BR … 5BR, penthouse ("Type 2Br", "2bedroom", "2 KT", "KT 1", "3/4 Bedroom", "min 2BR").
# "bed" is a bedroom only when no furniture word follows ("1 bed single", "ranjang susun 2 bed").
_BR = r"(?:br|bedrooms?|bed[ \t]*rooms?|bed(?![ \t]*(?:single|double|queen|king|sofa|susun|tingkat|size|set))|" \
      r"kamar(?![ \t]*mandi)(?:[ \t]*tidur)?|kmr(?![ \t]*mandi)|kt)"
# A count never starts inside a size ("21 m2, 1 kt"), a word or after a "+" (the maid room of "3BR + 1BR").
_UNIT_LEFT = r"(?<![\d.,])(?<!\dm)(?<![a-z²])(?<!\+)(?<!\+[ \t])(?<!\+[ \t]{2})(?<!lantai )(?<!lt )(?<!tower )(?<!lt\. )"
# "1 kamar pembantu", "1 KT + 1 KM pembantu", "(1 kamar kosong)": a maid's or spare room is no unit type.
# "2 BR Kosongan" is an unfurnished 2BR ("kosongan", "furnished", "semi" describe the unit, never a spare room).
_UNIT_RIGHT = r"\b(?![ \t]*(?:\+[ \t]*\d?[ \t]*(?:km|kamar[ \t]*mandi)[ \t]*)?\(?[ \t]*(?:pembantu|art\b|asisten|maid|kosong(?!an)|gudang|ekstra|extra))"
_UNIT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "satu": 1, "dua": 2, "tiga": 3, "empat": 4}
_UNIT_RANGE = re.compile(_UNIT_LEFT + r"([1-6])[ \t]*(?:/|-|–|atau|or|&|,)[ \t]*([1-6])[ \t]*(?:\+[ \t]*\d[ \t]*)?" + _BR + _UNIT_RIGHT)
_UNIT_COUNT = re.compile(_UNIT_LEFT + r"([1-6]|one|two|three|four|satu|dua|tiga|empat)[ \t]*(?:\+[ \t]*\d[ \t]*)?" + _BR + _UNIT_RIGHT)
_UNIT_AFTER = re.compile(r"\b(?:kt|kamar[ \t]+tidur|bedrooms?|br)(?:[ \t]*/[ \t]*km)?[ \t]*[:=.]?[ \t]*([1-6])(?![\d.,])"
                         r"(?![ \t]*(?:pembantu|art\b|asisten|maid|kosong(?!an)))")
_UNIT_MIN = re.compile(r"\b(?:min(?:imal|im|imum)?|di[ \t]*atas|diatas|lebih[ \t]+dari)\.?[ \t]*([1-5])[ \t]*(?:\+[ \t]*\d[ \t]*)?" + _BR + r"\b")
# Bed furniture before the count ("ranjang susun 2 bed", "spring bed 2"), and "Tipe 3BR dijadikan 2BR" (the unit is now a 2BR).
_BED_FURNITURE = re.compile(r"(?:ranjang|kasur|spring[ \t]*bed|tempat[ \t]+tidur|sofa[ \t]*bed)[^\n,;]{0,12}$")
# "Tipe 3BR dijadikan 2BR", "4br dijebol jadi 3br", "3 br jadi 2 br", "2 br digabung": the unit is what it became.
_CONVERTED = re.compile(r"\b([1-6])[ \t]*(?:br|bedrooms?|kt|kamar)\b(?:[ \t]*\+[ \t]*\d)?[ \t,]*(?:(?:di|ter)?(?:jadikan|ubah|rubah|jebol|gabung|buat|modif|"
                        r"convert)\w*[ \t]*)*(?:jadi|jd|menjadi|ke|to)\b|\b([1-6])[ \t]*(?:br|bedrooms?|kt|kamar)\b[ \t]*(?:di|ter)?(?:jadikan|jebol|gabung)\w*")
# "(bisa disekat jadi 2 kamar)": what a unit could become is not what it is.
_COULD_BECOME = re.compile(r"\bbisa\b[^\n]{0,20}\b(?:jadi|jd|menjadi)[ \t]*$")
_STUDIO = re.compile(r"\bstudio\b(?![ \t]*(?:foto|photo|musik|music|rekaman|alam|lukis|gym|senam|yoga|tari|band))")
_PENTHOUSE = re.compile(r"\bpent[ \t]*house\b")
_BR_EMPTY = re.compile(r"\b(br|bedrooms?|bed[ \t]*rooms?)([ \t]+)kosong\b")
UNIT_TYPE_LABELS = {"studio": "studio", "penthouse": "penthouse", **{f"{n}br": f"{n}BR" for n in range(1, 7)}}


@lru_cache(maxsize=16384)
def unit_types(text):
    """Apartment unit types a text names, e.g. {'2br'} for "Type 2Br", {'studio'} for "Tipe studio, KT 1" (an
    explicit studio wins over its one bedroom). Maid rooms, bed furniture, sizes and links are no unit types."""
    text = _URL.sub(" ", text)
    # "2 BR Kosong" is an empty (unfurnished) 2BR; only "1 kamar kosong" / "KT 3 + 1 kosong" is a spare room.
    text = _BR_EMPTY.sub(r"\1\2kosongan", text)
    # "3 BR … 3 KT MENJADI 2 KT": the unit is no longer the type it was converted from, wherever that is written.
    converted = {f"{m.group(1) or m.group(2)}br" for m in _CONVERTED.finditer(text)}
    text = _CONVERTED.sub(" ", text)
    found, counted = set(), set()
    if _STUDIO.search(text):
        found.add("studio")
    if _PENTHOUSE.search(text):
        found.add("penthouse")
    for m in _UNIT_MIN.finditer(text):
        found.update(f"{n}br" for n in range(int(m.group(1)), 6))
    for m in _UNIT_RANGE.finditer(text):
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        found.update(f"{n}br" for n in range(lo, hi + 1))
    for m in _UNIT_COUNT.finditer(text):
        if _BED_FURNITURE.search(text[max(0, m.start() - 30):m.start()]) or _COULD_BECOME.search(text[max(0, m.start() - 40):m.start()]):
            continue
        value = f"{_UNIT_WORDS.get(m.group(1)) or int(m.group(1))}br"
        (found if re.search(r"br|bed", m.group()) else counted).add(value)
    for m in _UNIT_AFTER.finditer(text):
        (found if re.match(r"b", m.group()) else counted).add(f"{m.group(1)}br")
    if "studio" in found:
        counted.discard("1br")  # "Tipe studio … KT 1", "Studio luas 25m2 / KT/KM 1/1": the studio's one bedroom
    types = found | counted
    return frozenset(types - converted if types - converted else types)


# Surabaya apartment buildings agents name (canonical: spellings). A tower named after its complex counts as that
# complex ("Tanglin" is in Pakuwon Mall); two towers of one complex are different buildings.
_APARTMENTS = {
    'waterplace': ('waterplace', 'water place', 'wp tower'), 'educity': ('educity', 'edu city'),
    'uc apartment': ('uc apartment', 'uc tower', 'uc apart'), 'east coast': ('east coast', 'eastcoast', 'eastcost', 'east cost'),
    'la riz': ('la riz', 'lariz', 'la riz mansion', 'lariz mansion', 'laritz', 'la ritz', 'laris mansion'),
    'la viz': ('la viz', 'laviz', 'la viz mansion'),
    'pakuwon mall': ('pakuwon mall',), 'anderson': ('anderson',), 'benson': ('benson',), 'orchard': ('orchard', 'orchad', 'orcad'),
    'tanglin': ('tanglin',), 'amor': ('amor',), 'bella': ('bella',), 'grand sungkono lagoon': ('grand sungkono', 'gsl'),
    'puncak bukit golf': ('puncak bukit golf',), 'puncak permai': ('puncak permai',), 'puncak cbd': ('puncak cbd',),
    'puncak kertajaya': ('puncak kertajaya',), 'puncak dharmahusada': ('puncak dharmahusada', 'puncak darmahusada'),
    'puncak marina': ('puncak marina',), 'trillium': ('trillium', 'trilium'), 'one icon': ('one icon',),
    'gunawangsa merr': ('gunawangsa merr', 'merr gunawangsa'), 'gunawangsa tidar': ('gunawangsa tidar', 'gws tidar', 'gw tidar'),
    'gunawangsa manyar': ('gunawangsa manyar',), 'the peak': ('the peak',), 'ciputra world': ('ciputra world',),
    'vieloft': ('vieloft',), 'voila': ('voila', 'the voila'), 'the via': ('the via', 'via vue', 'via ciputra'), 'the vue': ('the vue', 'tower vue'),
    'bale hinggil': ('bale hinggil',), 'metropolis': ('metropolis',), 'graha golf': ('graha golf',),
    'pavilion permata': ('pavilion permata', 'paviliun permata'), 'rosebay': ('rosebay', 'the rosebay'),
    'klaska': ('klaska',), 'vittorio': ('vittorio',), 'amega crown': ('amega crown', 'amega'), 'high point': ('high point',),
    'linden': ('linden',), 'marvell city': ('marvell city',), 'adhiwangsa': ('adhiwangsa', 'adiwangsa'),
    'dian regency': ('dian regency',), 'cityloft': ('cityloft', 'city loft', 'royal cityloft'),
    'grand dharmahusada lagoon': ('grand dharmahusada lagoon', 'grand dharmahusada', 'gdl'), 'westown': ('westown', 'westtown'),
    'one galaxy': ('one galaxy',), 'belleview': ('belleview', 'bellevue'), 'praxis': ('praxis',),
    'tamansari papilio': ('tamansari papilio', 'de papilio'), 'celadon': ('celadon',), 'cosmopolis': ('cosmopolis',),
    'skyloft': ('skyloft', 'soho skyloft'), 'the colmar': ('the colmar',), 'puri matahari': ('puri matahari',),
    'aryaduta': ('aryaduta',), 'bess mansion': ('bess mansion',), 'one east': ('one east',), 'taman beverly': ('taman beverly',),
}
_APARTMENT_COMPLEX = {'anderson': 'pakuwon mall', 'benson': 'pakuwon mall', 'orchard': 'pakuwon mall', 'tanglin': 'pakuwon mall',
                      'vieloft': 'ciputra world', 'voila': 'ciputra world', 'the via': 'ciputra world', 'the vue': 'ciputra world',
                      'skyloft': 'ciputra world'}
_APARTMENT_SPELLINGS = {alias: name for name, aliases in _APARTMENTS.items() for alias in aliases}
_APARTMENT_RE = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(a).replace(r'\ ', r'\s*') for a in
                                                    sorted(_APARTMENT_SPELLINGS, key=len, reverse=True)) + r')(?!\w)')


@lru_cache(maxsize=16384)
def known_buildings(text):
    """Apartment buildings a text names, most specific only ("Tanglin Pakuwon Mall" → {'tanglin'})."""
    found = {_APARTMENT_SPELLINGS.get(re.sub(r'\s+', ' ', m.group()), re.sub(r'\s+', ' ', m.group()))
             for m in _APARTMENT_RE.finditer(text)}
    return frozenset(f for f in found if not any(_APARTMENT_COMPLEX.get(o) == f for o in found))


# Towers ("Tower A atau C saja", "tower A/C", "Tw B", "Tower Anderson", "Yale Tower") and estate blocks ("Blok GC").
# A letter range ("Tw A-C", "Tower A s/d C") is one item; "a - lantai 5" is no range (the end is one letter).
_TOWER_RANGE = r"[a-j][ \t]*(?:-|–|s/?d|sampai|hingga)[ \t]*[a-j]\b"
_TOWER_ITEM = r"(?:" + _TOWER_RANGE + r"|[a-z]{1,14}|\d{1,2})"
_TOWER_SEP = r"[ \t]*(?:/|,|&|\+|\batau\b|\bdan\b|\bor\b|\band\b)[ \t]*"
_TOWER_LIST = re.compile(r"\b(?:tower|twr|tw)\b\.?[ \t]*[:\-]?[ \t]*(" + _TOWER_ITEM + r"\b(?:" + _TOWER_SEP + r"(?:(?:tower|twr|tw)\b\.?[ \t]*)?"
                         + _TOWER_ITEM + r"\b)*)")
_TOWER_NAMED = re.compile(r"\b([a-z]{3,14})[ \t]+tower\b")
_BLOCK_LIST = re.compile(r"\b(?:blok|block|blk)\b\.?[ \t]*([a-z]{1,3}(?:\d+[a-z]?)?\b(?:" + _TOWER_SEP + r"[a-z]{1,3}(?:\d+[a-z]?)?\b)*)")
_TOWER_STOP = frozenset((
    "saja aja only lantai lt lantainya view di yang yg dan atau or and dengan dgn unit units type tipe no nomor nmr mana apa apapun "
    "manapun bebas lain lainnya high low floor baru juga jg ini itu the of ke sama tinggi rendah pilihan depan belakang samping utara "
    "selatan timur barat apartemen apartment apartement apart apt residence residences jual dijual sewa disewakan new satu dua tiga "
    "beda other another condo office kantor twin north south east west semua all connecting with pada ada tersedia available full "
    "furnish furnished semi kosongan studio br bedroom harga hrg nego ready siap luas hadap hdp cluster perumahan blok block kav "
    "kavling rumah ruko").split())


def _letters(item):
    """Single tower letters an item stands for: "a-c" → a, b, c; "abc" (written as one word, "Tower ABC ONLY") →
    a, b, c. Only 2–4 distinct letters A–J in ascending order count as letters; other words are tower names."""
    ranged = re.fullmatch(r"([a-j])[ \t]*(?:-|–|s/?d|sampai|hingga)[ \t]*([a-j])", item)
    if ranged:
        lo, hi = sorted((ord(ranged.group(1)), ord(ranged.group(2))))
        return {chr(c) for c in range(lo, hi + 1)}
    if re.fullmatch(r"[a-j]{2,4}", item) and list(item) == sorted(set(item)):
        return set(item)
    return {item}


_TOWER_RANGE_RE = re.compile(r"\b" + _TOWER_RANGE)
_BUILDING_LETTERS = re.compile(_APARTMENT_RE.pattern + r"[ \t]+(?P<letters>[a-j]{2,4}|" + _TOWER_RANGE + r"|[a-j](?:" + _TOWER_SEP +
                               r"[a-j])+|[a-j])(?!\w)(?=[ \t]*(?:only|saja|aja|doang)\b|[ \t]*(?:[,.;!)]|$))", re.M)


def _tower_items(group, letters=True):
    """Items of a tower list; ``letters``: letter groups and ranges stand for single towers (never for estate
    blocks, where "AA" or "AB" names one block)."""
    found = set()
    if letters:
        for m in _TOWER_RANGE_RE.finditer(group):
            found |= _letters(m.group())
        group = _TOWER_RANGE_RE.sub(" ", group)
    items = re.split(_TOWER_SEP + r"(?:(?:tower|twr|tw)\b\.?[ \t]*)?", group)
    return found | {letter for item in (i.strip() for i in items) if item and item not in _TOWER_STOP
                    for letter in (_letters(item) if letters else (item,))}


@lru_cache(maxsize=16384)
def tower_names(text):
    """(towers, blocks) a text names: ({'a', 'c'}, set()) for "Tower A atau C saja"."""
    towers, blocks = set(), set()
    if "tower" in text or "tw" in text:
        listed = set()
        for m in _TOWER_LIST.finditer(text):
            items = _tower_items(m.group(1))
            if items:
                towers |= items
                listed.add(m.start())
        # "Yale Tower", never "Pakuwon Mall Tower Anderson" (the tower is named after the word).
        towers |= {m.group(1) for m in _TOWER_NAMED.finditer(text) if m.group(1) not in _TOWER_STOP and m.end() - 5 not in listed}
    for m in _BUILDING_LETTERS.finditer(text):
        # v4.4: "Apartemen Waterplace ABC only", "Educity A/C saja": tower letters right after a known building.
        towers |= {letter for letter in _tower_items(m.group("letters")) if len(letter) == 1}
    if "bl" in text:
        for m in _BLOCK_LIST.finditer(text):
            blocks |= {re.match(r"[a-z]+", item).group() for item in _tower_items(m.group(1), letters=False)}
    return frozenset(towers), frozenset(blocks)


# Floor level of an apartment/office unit (never the storey count of a house, never LT = luas tanah). ``lt``
# (apartments): "Lt 20" is the floor; for offices only "lantai"/"floor" count ("Kantor LT 50-80" is a land area).
def _floor_patterns(lt):
    word = r"(?:lantai|\blt\b|\bfloor\b|\blvl\b|\blevel\b)\.?" if lt else r"(?:lantai|\bfloor\b|\blvl\b|\blevel\b)\.?"
    # "Lantai 20.", "lantai 37, hanya ada 4 unit", "Lantai 20 unit 05" state the floor; "Lt 1,5" or "lt 20m" do not.
    listing = re.compile(word + r"[ \t]*(?:ke[ \t]*-?[ \t]*)?[:=]?[ \t]*(\d{1,2})(?!\d|[.,]\d)"
                         r"(?![ \t]*(?:m2|m²|meter|mtr|m\b|x|×|lantai|lt\b|kt|km|br|tingkat|bed|kamar))")
    after = r"[ \t]*(?:lantai[ \t]*|lt\.?[ \t]*)?(\d{1,2})\b"
    needs = [(re.compile(word + r"[ \t]*(?:di[ \t]*bawah|dibawah|bawah|under|below|<|kurang[ \t]+dari)" + after), "below"),
             (re.compile(r"(?:di[ \t]*bawah|dibawah|under|below|<|kurang[ \t]+dari)[ \t]*" + word + r"[ \t]*(\d{1,2})\b"), "below"),
             (re.compile(word + r"[ \t]*(?:max|maks(?:imal|imum)?|maximal|maximum)\.?" + after), "max"),
             (re.compile(r"\b(?:max|maks(?:imal|imum)?|maximal|maximum)\.?[ \t]*(?:di[ \t]*)?" + word + r"[ \t]*(\d{1,2})\b"), "max"),
             (re.compile(word + r"[ \t]*(?:di[ \t]*atas|diatas|above|>|lebih[ \t]+dari)" + after), "above"),
             (re.compile(r"(?:di[ \t]*atas|diatas|above|>|lebih[ \t]+dari)[ \t]*" + word + r"[ \t]*(\d{1,2})\b"), "above"),
             (re.compile(word + r"[ \t]*(?:min(?:imal|imum)?)\.?" + after), "min"),
             (re.compile(r"\bmin(?:imal|imum)?\.?[ \t]*(?:di[ \t]*)?" + word + r"[ \t]*(\d{1,2})\b"), "min"),
             (re.compile(word + r"[ \t]*(\d{1,2})[ \t]*(?:ke[ \t]*atas|keatas|up\b|\+)"), "min"),
             (re.compile(word + r"[ \t]*(\d{1,2})[ \t]*(?:ke[ \t]*bawah|kebawah)"), "max")]
    ranged = re.compile(word + r"[ \t]*(\d{1,2})[ \t]*(?:-|–|s/?d|sampai|hingga|to)[ \t]*(\d{1,2})\b(?![.,]?\d|[ \t]*(?:m2|m²|jt|juta|m\b))")
    return listing, needs, ranged, re.compile(r"lantai|floor|\blvl\b|\blevel\b" + (r"|\blt\b" if lt else ""))


_FLOOR_PATTERNS = {True: _floor_patterns(True), False: _floor_patterns(False)}
_FLOOR_WORDS = [(re.compile(r"\b(?:low[ \t]*floor|lower[ \t]*floor|(?:lantai|lt\.?)[ \t]*(?:bawah|rendah))\b"), (1, 10)),
                (re.compile(r"\b(?:high[ \t]*floor|higher[ \t]*floor|(?:lantai|lt\.?)[ \t]*(?:atas|tinggi))\b"), (15, 99)),
                (re.compile(r"\b(?:mid(?:dle)?[ \t]*floor|(?:lantai|lt\.?)[ \t]*tengah)\b"), (8, 20))]


# Unit numbers carry the floor: "Unit 3105" is floor 31, "unit A706" / "#1909" floor 7 / 19.
_UNIT_CODE = re.compile(r"(?:\bunit\b[ \t]*(?:no\.?|nomor|nmr)?[ \t]*[:#]?[ \t]*[a-z]?[ \t]*-?[ \t]*|#[ \t]*)(\d{3,4})(?![\d.,]|[ \t]*(?:m2|m²|jt|juta|m\b|unit))")


@lru_cache(maxsize=16384)
def floor_level(text, lt=True):
    """(lo, hi) floor of a listed unit ("Lantai 20" → (20, 20), "low floor" → (1, 10), "Unit 3105" → (31, 31)),
    else None. "LT 35 LB 35" is a land area, never the floor."""
    listing = _FLOOR_PATTERNS[lt][0]
    for m in listing.finditer(text):
        value = int(m.group(1))
        if m.group().startswith("lt"):
            line = text[text.rfind("\n", 0, m.start()) + 1:(text.find("\n", m.end()) + 1 or len(text) + 1) - 1]
            if re.search(r"\blb\b|\bluas\b|m2|m²", line):
                continue
        if 1 <= value <= 80:
            return value, value
    for pattern, span in _FLOOR_WORDS:
        if pattern.search(text):
            return span
    for m in _UNIT_CODE.finditer(text):
        value = int(m.group(1)) // 100
        if 1 <= value <= 80:
            return value, value
    return None


@lru_cache(maxsize=16384)
def floor_requirement(text, lt=True):
    """(lo, hi, preferred) floor a buyer asks for ("Lantai di bawah 10" → (None, 9, False), "Prefer maks lt 10" →
    (None, 10, True), "high floor" → (15, None, False)), else None."""
    _, needs, ranged, cue = _FLOOR_PATTERNS[lt]
    for line in text.split("\n"):
        if not cue.search(line):
            continue
        lo = hi = None
        m = ranged.search(line)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        else:
            for pattern, kind in needs:
                m = pattern.search(line)
                if m:
                    n = int(m.group(1))
                    lo, hi = {"below": (None, n - 1), "max": (None, n), "above": (n + 1, None), "min": (n, None)}[kind]
                    break
            else:
                for pattern, (a, b) in _FLOOR_WORDS:
                    if pattern.search(line):
                        lo, hi = (None, b) if a == 1 else (a, None) if b == 99 else (a, b)
                        break
        if lo is not None or hi is not None:
            return lo, hi, bool(_PREFERENCE.search(line))
    return None


# Financing: KPR (house mortgage), KPA (apartment mortgage), cash.
_MORTGAGE = re.compile(r"\b(?:kpr|kpa)\b")
_CASH = re.compile(r"\b(?:cash|tunai)\b")
_CASH_ONLY = re.compile(r"\bcash\s*(?:keras\s*)?(?:only|saja|aja|doang)\b|\b(?:hanya|only|cuma|khusus|harus|wajib)\s+(?:cash|tunai)\b"
                        r"|\b(?:tidak|tdk|ga|gak|nggak|no|non|bukan)\s*(?:bisa\s*|terima\s*|menerima\s*|melayani\s*)?(?:kpr|kpa)\b"
                        r"|\btanpa\s+(?:kpr|kpa)\b")
_CASH_HARD = re.compile(r"\bcash\s*keras\b")
_MORTGAGE_OK = re.compile(r"\b(?:bisa|dapat|boleh|terima|menerima|melayani|support|via|proses|siap|ready)\s*(?:di\s*)?(?:kpr|kpa)\b"
                          r"|\b(?:kpr|kpa)\s*(?:bisa|ok|oke|available|ready|welcome|siap)\b")


@lru_cache(maxsize=16384)
def financing_need(text):
    """'kpa' | 'kpr' when a buyer pays with a mortgage ("Pembelian KPA"); None for cash or both ("cash/KPR")."""
    wanted = "\n".join(_split_negation(line)[0] for line in text.split("\n"))
    found = _MORTGAGE.findall(wanted)
    if not found or _CASH.search(wanted):
        return None
    return "kpa" if "kpa" in found else "kpr"


@lru_cache(maxsize=16384)
def financing_offer(text):
    """'cash_only' | 'cash_hard' | 'mortgage' | None from what a listing says about payment."""
    if _CASH_ONLY.search(text):
        return "cash_only"
    if _MORTGAGE_OK.search(text):
        return "mortgage"
    if _CASH_HARD.search(text):
        return "cash_hard"
    return None


# Lease term of a rent (v4.4): a renter's planned lease ("SEWA 3 BULAN", "Rencana sewa 2 tahun", "sewa 1-2 thn") and a
# listing's minimum ("Minim 2 thn", "min. 1 tahun", "Minimal Sewa 2 Tahun", "sewa min 1 th"), in months.
_LEASE_PERIOD = r"[ \t]*(?:(th|thn|thun|tahun|taun|tn|years?|yrs?)|(bl|bln|bulan|months?|mo))\b"
_LEASE_NEED = re.compile(r"\b(?:sewa|kontrak|lease|rent)(?:[ \t]*nya)?[ \t]*[:=]?[ \t]*(?:(?:selama|rencana|untuk|utk|kurang[ \t]+lebih|kurleb|sekitar|"
                         r"min(?:imal|im|imum)?\.?|max|maks(?:imal)?\.?)[ \t]*)*(\d{1,2})(?:[ \t]*(?:-|–|s/?d|sampai|atau|/)[ \t]*(\d{1,2}))?"
                         + _LEASE_PERIOD)
_LEASE_MIN = re.compile(r"\bmin(?:im(?:al|um)?)?\.?[ \t]*(?:(?:sewa|kontrak|lease)[ \t]*)?[:=]?[ \t]*(\d{1,2})" + _LEASE_PERIOD +
                        r"|\b(?:sewa|kontrak|lease)[ \t]*min(?:im(?:al|um)?)?\.?[ \t]*[:=]?[ \t]*(\d{1,2})" + _LEASE_PERIOD)
_LEASE_MONTHLY = re.compile(r"\b(?:bisa|boleh|sewa|harga)[ \t]+(?:per[ \t]*)?bulanan\b|\bbulanan[ \t]+(?:bisa|boleh|ok)\b|\bmonthly\b")


def _months(number, year_unit):
    return int(number) * (12 if year_unit else 1)


@lru_cache(maxsize=16384)
def lease_need(text):
    """Months a renter plans to rent for ("sewa 3 bulan" → 3, "Sewa 1-2 tahun" → 24), else None."""
    found = [_months(m.group(2) or m.group(1), m.group(3)) for m in _LEASE_NEED.finditer(text) if int(m.group(2) or m.group(1)) > 0]
    return max(found) if found else None


@lru_cache(maxsize=16384)
def lease_offer(text):
    """(minimum lease in months or None, monthly rent offered) of a listing ("Minim 2 thn" → (24, False))."""
    found = []
    for m in _LEASE_MIN.finditer(text):
        number, year = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(5))
        if int(number) > 0:
            found.append(_months(number, year))
    return (min(found) if found else None), bool(_LEASE_MONTHLY.search(text))


# "View bebas", "view apa saja", "bebas view": any view, no requirement.
_FREE_VIEW = re.compile(r"^(?:bebas|apa\s*(?:saja|aja|pun)|apapun|any|semua|terserah|mana\s*(?:saja|aja)|tidak\s+masalah|gpp|free)\b")

# --------------------------------------------------------------------------- multi-context bubbles (v4.2)
MAX_SEGMENTS = 12


class _Whole(Exception):
    """Splitting looks unreliable (too many parts, a price for the whole lot): keep the whole bubble (R1)."""


# "1.", "2)" and keycap digits ("1️⃣") number sections and items.
_KEYCAP = "\ufe0f?\u20e3"
_MARK_LEAD = r"^[^a-z0-9#\n]*(?:\d{1,2}(?:\s*[.)]|" + _KEYCAP + r")\s*)?[^a-z0-9#\n]*"
# Section headings that start a context: "#Buyer 1", "#Renter 1", "Buyer request (A)", "*RENTER*", "Buyer:",
# "Renter cari …", "Req 1", "Kebutuhan 2".
_SECTION_MARK = re.compile(
    r"(?im)" + _MARK_LEAD + r"(?:#\s*" + _WHO + r"\b"
    r"|(?:(?:hot|urgent|new)\s+)?" + _WHO + r"(?:\s*\d{1,2}\b|\s*\(\s*[a-z0-9]{1,2}\s*\)|\s+(?:[*_~]*[a-z][*_~]*\s+)?req(?:uest)?\b|\s*[:：]"
    r"|\s+[a-z][^a-z0-9\n]*$"
    r"|\s+(?:cari|mencari|nyari|need|butuh)\b|[^a-z0-9\n]*$)"
    r"|req(?:uest)?\s+(?:buyer|renter|investor)\b|(?:req(?:uest)?|kebutuhan|permintaan)\s*(?:ke\s*)?\d{1,2}\b)"
    # v4.3 R9: an italic/bold numbered heading "_1) Buyer corporate_" / "*2) Buyer surat ijo*".
    r"|^[ \t]*[_*~]+[ \t]*\d{1,2}[ \t]*[.)][ \t]*" + _WHO + r"\b[^\n_*~]{0,40}[_*~]+[ \t]*$"
    # A numbered section may name its asset first: "1️⃣GUDANG!! request buyer".
    r"|^[^a-z0-9#\n]*\d{1,2}(?:\s*[.)]|" + _KEYCAP + r")[^a-z0-9#\n]*(?:[a-z]+[^a-z0-9\n]+){1,2}"
    r"(?:req(?:uest)?\s+" + _WHO + r"|" + _WHO + r"\s+req(?:uest)?)\b")
_WHO_WORD = re.compile(r"(?i)\b" + _WHO + r"\b")
# Numbered items "1. IMPERIAL BEACH …", "*2) Rumah …", "*2*DIJUAL …", "1 *DUKUH KUPANG*", "2 🏢 *OFFICE BUILDING*",
# "1⛳ *Tanah …*", "1." alone on its line above the title (never "1.5 M" or a fraction "6. 1/2 Lantai").
_ITEM_NUMBER = (r"(\d{1,2})(?:\s*[.)](?!\d)(?![ \t]*\d+[ \t]*/[ \t]*\d)|\*|[ \t]+(?=\*)|[ \t]*(?=[^\w\s\x00-\x7f]+[ \t]*\*)|"
                + _KEYCAP + r")")
# "1 GEDUNG KANTOR:" restarting a numbered list under its own heading ("*`JAKARTA SELATAN`*").
_RESTART_LOOSE = re.compile(r"(?m)^[^a-z0-9\n]*(1)[ \t]+(?=[A-Z]{3,})")
# "*6 RUMAH 1 LANTAI …" in a numbered list whose other items have a period: accepted only as the next number.
_NUMBERED_LOOSE = re.compile(r"(?m)^[ \t]*\*[ \t]*(\d{1,2})[ \t]+(?=[A-Z]{3,})")
_NUMBERED = re.compile(r"(?im)^[^a-z0-9\n]*" + _ITEM_NUMBER + r"[ \t]*(?=\S|$)")
_ITEM_PREFIX = re.compile(r"(?i)^[^a-z0-9\n]*" + _ITEM_NUMBER)
_OFFER_START = re.compile(r"(?i)^[^a-z0-9\n]*(?:\d{1,2}\s*(?:[.)]|\*)[^a-z0-9\n]*)?(?:open\s+)?(?:(?:di\s*jual|di\s*sewa(?:kan)?|"
                          r"dikontrakk?an|jual|sewa|for\s+(?:sale|rent|lease)|"
                          r"(?:house|home|land|apartment|office|warehouse|shophouse)\s+for\s+(?:sale|rent|lease)|dicari|cari|mencari)"
                          r"(?![^\W_])|f[sr](?:\s*/\s*f[sr])?\s*:)")  # "_For Sale_": italics end the word too
_SEPARATOR_LINES = re.compile(r"^(?:[ \t]*[^\w\s]{3,}[ \t]*(?:\n|$))+|(?:(?:^|\n)[ \t]*[^\w\s]{3,}[ \t]*)+$")
_AREA_SPEC = re.compile(r"(?i)\b(?:lt|lb|luas(?:\s+(?:tanah|bangunan|sg|semi\s*gross|gross|nett?))?|ukuran|dimensi)(?:\b|(?=\d))[ \t]*[.:=]?[ \t]*"
                        r"(?:(?:min(?:imal|im)?|max|maks(?:imal)?)\.?[ \t]*|[±+~<>-][ \t]*)*\d|\d\s*[x×]\s*\d"
                        r"|(?<![\d.,/])\d+(?:[.,]\d+)?\s*(?:m2|m²|sq\.?\s*m\b|sqm\b)(?!\w)")
# Words that do not name a property on an item's first line.
_TITLE_STOP = re.compile(r"\b(?:harga\w*|hrg|budget|budged|bujet|price|nego\w*|nett?|cash|keras|only|rp|jt|juta|milyar|miliar|"
                         r"milyard|m|rb|ribu|lt|lb|kt|km|luas|tanah|bangunan|shm|hgb|ppjb|ajb|sertifikat|hadap|dp|cicil\w*|kpr|"
                         r"max|maks\w*|min\w*|mulai|start|total|per|tahun|thn|th|bulan|bln|sewa|jual|dijual|disewakan|untuk|utk|"
                         r"bisa|tipis|ok|oke|semi|furnish\w*|kosongan|dan|atau|di|ke|dari|yang|yg|ada|sudah|masih|lantai|"
                         r"kamar|kondisi|listrik|watt|pln|pdam|air|carport|garasi|sesuai|bu|turun|jadi|net|all|in|include\w*)\b")
# "EDISI PAKUWON CITY 4M > UP", "Dijual kavling di Citraland mulai 2 M": a price that only summarises the offers below.
_SUMMARY_PRICE = re.compile(r"\b(?:mulai|start(?:ing)?|edisi)\b|[>+]\s*up\b|\bup\b|\bke\s*atas\b|\bkeatas\b"
                            r"|\d\s*(?:m|jt|juta|milyar|miliar)\s*-?\s*an\b|\d\s*(?:man|jtan|jutaan)\b")
# First lines that continue the property above them rather than starting a new one.
_CONTINUATION = re.compile(r"(?i)^[^a-z0-9\n]*(?:harga\w*|hrg|budget|price|lokasi|alamat|fasilitas|note|nb|catatan|info\w*|hub\w*|"
                           r"minat|contact|kontak|cp|wa|call|sertifikat|surat|legal\w*|lt|lb|luas|kt|km|kamar|hadap|dimensi|ukuran|"
                           r"listrik|pln|pdam|daya|bonus|free|gratis|nego\w*|dp|cicil\w*|kpr|row|akses|lebar|panjang|kondisi|"
                           r"spesifikasi|spek|fyi|untuk|utk|cocok|dekat|tersedia|view|lantai|type|tipe|semi|full|furnish\w*|shm|hgb|"
                           r"shgb|ppjb|ajb|keunggulan|kelebihan|detail|keterangan|deskripsi|benefit|plus|selling\s+point)\b")
_HEADER_WORDS = re.compile(r"\b(?:listings?|edisi|katalog|catalog|update|ready\s+stock|stock|stok|pilihan|promo|hot|info)\b")
# ">>>Ruko Kalirungkut" is a title marker, not a bullet.
_BULLET = re.compile(r"^\s*(?:[-•·◦▪]|>(?!>))")
# Spec/price words that continue the property above even on a bold line ("*Harga 2 M*", "*SHM*").
_PRICE_CONTINUATION = re.compile(r"(?i)^[^a-z0-9\n]*(?:harga\w*|hrg|budget|price|nego\w*|dp|cicil\w*|kpr|lt|lb|luas|kt|km|kamar)\b")
_CAPTION = re.compile(r"(?i)\W*mengirim gambar dengan caption")
_HAS_WORD = re.compile(r"[^\W_]")


def _trim_part(text):
    return _SEPARATOR_LINES.sub("", text.strip()).strip()


@lru_cache(maxsize=8192)
def _part_category_tuple(text):
    return tuple(_body_categories(re.sub(r"[*_~]", "", text)))


def _part_categories(text):
    return list(_part_category_tuple(text))


# Amounts that are not the asking price of the block they stand in: a struck-through old price ("~60 jt~"), a
# discount or the old price ("turun harga 7M", "TURUN 50JT", "semula Rp 60 jt", "diskon 100 jt"), a reference
# price ("harga developer … 1,6M", "NJOP", "appraisal") and running costs or income ("PBB Rp 3.912.750",
# "Expense 5 juta / bulan", "2 kamar @2 juta"); a block headed "HITUNGAN BISNIS KOST" holds income figures only.
_STRUCK = re.compile(r"~[^~\n]*\d[^~\n]*~")
_NOT_ASKING_AMOUNT = re.compile(
    r"(?:\bturun\s+harga\s+(?:dari\s+|sebesar\s+|sampai\s+)?|^[^\w\n]*turun\s+(?:dari\s+|sebesar\s+)?|"
    r"\bharga\s+(?:terjun|anjlok|jatuh)(?:\s+bebas)?\s+(?:sebesar\s+)?|\b(?:terjun|anjlok)(?:\s+bebas)?\s+(?:harga\s+)?(?:sebesar\s+)?|"
    r"\b(?:diskon|discount|disc|hemat|potongan|cash\s*back|cashback|deposit|uang\s+jaminan|jaminan|"
    r"semula|sebelumnya|awalnya|harga\s+(?:awal|lama|normal|developer|dev|pasar|beli|perolehan|pembelian|appraisal|njop|"
    r"pricelist|price\s*list)|normal\s+price|price\s*list|pricelist|njop|appraisal|waktu\s+beli|beli\s+dulu|dulu\s+beli)\b"
    r"[^\n\d]{0,40}?)(?:rp\.?\s*)?\d[\d.,]*\s*(?:" + _PRICE_UNITS + r")?\b", re.I)
# "Harga: Rp 3,5 miliar Turun Jadi 2,9M", "~950jt~ >> 800jt": the amount before "jadi"/">>" is the old price.
_OLD_AMOUNT = re.compile(r"(?:rp\.?\s*)?\d[\d.,]*\s*(?:" + _PRICE_UNITS + r")?\b(?=[\s,.]*(?:nego\w*\s*)?"
                         r"(?:turun\w*\s*(?:(?:only|now|tinggal|cuma|hanya|jadi|jd|menjadi|ke)\s*)?|jadi|jd|menjadi|ke|>>|→|->|=>|➡️?|👉)\s*(?:rp\.?\s*)?\d)", re.I)
_CUE_END = re.compile(r"(?:rp\.?\s*)?\d[\d.,]*\s*(?:" + _PRICE_UNITS + r")?$", re.I)
_NOT_ASKING_LINE = re.compile(r"(?i)\b(?:expense|pengeluaran|biaya|net\s+income|income|omzet|omset|pbb|ipl|iuran|service\s+charge|"
                              r"maintenance|bphtb|notaris|pajak|bep|break\s*even)\b|^\W*sc\b|@\s*(?:rp\.?\s*)?\d")
_ASKING_WORD = re.compile(r"\b(?:harga\w*|hrg|price|dijual|jual|sewa\w*|disewakan|budget)\b|💰")
_INCOME_HEADING = re.compile(r"(?i)^\W*(?:(?:hitungan|perhitungan|simulasi|estimasi|proyeksi|kalkulasi|rincian)\s+"
                             r"(?:bisnis|usaha|kost?|sewa|income|investasi|pendapatan|penghasilan|hasil|roi|cash\s*flow)|"
                             r"(?:potensi\s+)?(?:income|pendapatan|penghasilan|omzet|omset)\b)")
# A lenient reading for telling blocks apart (never used for values): "Harga 525 nett" (no unit) and
# "Harga 9.000/m2 NEGO" still state the block's own price.
_LOOSE_PRICE = re.compile(r"(?i)(?:\b(?:harga|hrg|hg|price|sell|idr|rp)\b|💰)[^\n\d]{0,12}(?:\d{3,4}\s*(?:nett?|nego)\b|"
                          r"\d{1,3}(?:[.,]\d{3})+(?![\d.,]))|(?:^|\s)h\s*[:.]\s*\d{3,4}(?![\d.,])"
                          # "*Harga Sewa 150t/tahun*": a typo for juta, still the block's own rent.
                          r"|\b\d{2,4}\s*t\s*/\s*(?:th|thn|tahun|bln|bulan)\b|\bharga\s+sewa\s*:?\s*\d{2,4}\b(?![.,]?\d)")


_PARAGRAPHS = re.compile(r"\n[ \t]*\n")


_INCLUDED_COST = re.compile(r"\b(?:include\w*|incl|termasuk|exclude\w*|excl|belum\s+termasuk|tidak\s+termasuk|tdk\s+termasuk)\b")


# "Luas 700 m2 @ 200 jt/th": the price of the stated size, not a cost per room.
_SIZE_PRICE = re.compile(r"(?i)^\W*(?:luas\w*|lt|lb|size|ukuran)\b[^\n@]*\d[^\n@]*(?:m2|m²|meter|mtr)[^\n@]*@")


def _asking_line(line):
    if not _DIGIT.search(line):
        return line
    lowered = line.lower()
    if _SIZE_PRICE.match(line):
        return line
    # "Rp 5,5 juta/bulan (include Service Charge, listrik)" still states the asking rent.
    if _NOT_ASKING_LINE.search(line) and not _ASKING_WORD.search(lowered) and not _INCLUDED_COST.search(lowered):
        return ""
    return _OLD_AMOUNT.sub(" ", _NOT_ASKING_AMOUNT.sub(lambda m: _CUE_END.sub(" ", m.group()), line))


# "HARGA TERJUN 700 JUTA RUMAH WIYUNG", "TURUN 500 JT!! Rumah …": a price drop announced in the headline is the
# amount of the reduction, not the price stated below it ("Harga : 2,3 M --> 1,6M").
_HEADLINE_DROP = re.compile(r"(?i)\b(?:turun|terjun|anjlok|drop(?:ped)?|jatuh)\b(?:\s+(?:bebas|harga|hrg|sebesar|lagi|banget|"
                            r"sampai|sampe|hingga|by))*[\s:!-]*(?:rp\.?\s*)?\d[\d.,]*\s*(?:" + _PRICE_UNITS + r")\b")


def _headline_drop(text):
    """The text without a price drop announced in its first line ("TURUN 300 JT! Rumah …"), when the rest still states
    a price. "Harga ~3 M~ Turun 2 M" (an amount before the word) states the reduced price itself."""
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    drop = _HEADLINE_DROP.search(lines[first]) if first is not None else None
    if not drop or _DIGIT.search(lines[first][:drop.start()]):
        return text
    rest = "\n".join(lines[:first] + [_HEADLINE_DROP.sub(lambda m: _CUE_END.sub(" ", m.group()), lines[first])] + lines[first + 1:])
    return rest if _price_candidates(_STRUCK.sub(" ", rest)) else text


@lru_cache(maxsize=8192)
def _asking_text(text):
    """The text with the amounts that are not its own asking price removed (see _NOT_ASKING_AMOUNT)."""
    text = _headline_drop(text)
    out = []
    for paragraph in _PARAGRAPHS.split(_STRUCK.sub(" ", text) if "~" in text else text):
        if not _DIGIT.search(paragraph):
            out.append(paragraph)
            continue
        if _INCOME_HEADING.match(re.sub(r"[*_~]", "", _first_line(paragraph))):
            continue
        out.append("\n".join(_asking_line(line) for line in paragraph.split("\n")))
    return "\n\n".join(out)


# Text that can hold a price at all (an amount with a unit, a digit-grouped amount or a price word near a digit).
_MAYBE_PRICE = re.compile(r"(?i)\d\s*(?:m|jt|juta|milyar|miliar|milyard|rb|ribu|b|bn)|rp|idr|harga|hrg|\bhg\b|price|budge|bajet|bujet|"
                          r"nett?\b|nego|sewa|jual|sell|💰|\d[.,]\d{3}|\bh\s*[:.]")


@lru_cache(maxsize=8192)
def _has_price(text):
    if not _DIGIT.search(text) or not _MAYBE_PRICE.search(text):
        return False
    asking = _asking_text(text)
    return bool(_price_candidates(asking)) or any(
        _LOOSE_PRICE.search(line) and not (_INCOME_LINE.search(line.lower()) and not _PRICE_WORD.search(line.lower()))
        for line in asking.split("\n"))


def _is_context(text):
    """A text with its own price/budget and its own property (category or stated size)."""
    return _has_price(text) and bool(_part_categories(text) or _AREA_SPEC.search(text))


def _has_substance(text):
    return _has_price(text) or bool(_part_categories(text))


def _own_price(text):
    """A price/budget of its own; a summary price ("4M > UP", "mulai 2 M", "500 juta an") without a size is not."""
    return _has_price(text) and not (_SUMMARY_PRICE.search(text.lower()) and not _AREA_SPEC.search(text))


# Spec lines of a property besides its size: rooms, floors, certificate, facing, power, dimensions.
_SPEC_LINE = re.compile(r"(?im)^[\W_]*(?:kt|km|kamar|lantai|sertifikat|surat|shm|shgb|hgb|ppjb|ajb|hadap|listrik|pln|daya|dimensi|"
                        r"ukuran|row|carport|garasi|luas\w*|bangunan|tanah)\b|\b\d\s*(?:\+\s*\d\s*)?(?:kt|km|br|bed\w*|kamar|lantai|lt)\b"
                        r"|\b(?:studio|\d\s*br)\b|\b(?:kt|km)\s*[:.]?\s*\d")


# A request states what it wants, often without any spec line ("*RENTER RUKO* … 💰 Budget 250-350jt").
_REQUEST_WORDS = re.compile(r"(?i)\b(?:budget|budged|bugdet|budjet|buget|bujet|bajet|request|req|buyer|renter|investor|dicari|mencari|cari|butuh)\b")


def _specs(text):
    return bool(_AREA_SPEC.search(text) or _SPEC_LINE.search(text) or _REQUEST_WORDS.search(text))


def _group_closed(blocks):
    """A property is complete once it has its own price and a size (or a few lines with a spec); a heading with
    a price ("UNIT 2 – RUMAH 2 LANTAI · HARGA 785 JT", "*MURAH RUMAH HITUNG TANAH RP 4.8 JUTA /M²*") still waits
    for its specs, and so does a teaser ("*TURUN HARGA*" / "DIJUAL" / "RUMAH X")."""
    text = "\n\n".join(blocks)
    return any(_own_price(b) for b in blocks) and (bool(_AREA_SPEC.search(text)) or (
        sum(bool(_HAS_WORD.search(line)) for line in text.split("\n")) >= 3 and _specs(text)))


def _closes(text):
    """A text with an asking price/budget of its own and the property it prices."""
    return _group_closed([text])


def _first_line(text):
    return next((line.strip() for line in text.split("\n") if _HAS_WORD.search(line)), "")


def _item_title(text):
    """The first words of a numbered item after its number ("1.\n*PBG*" → "*PBG*")."""
    return _first_line(_ITEM_PREFIX.sub(" ", text.strip(), count=1))


def _offer_item(text):
    """A numbered item that is an offer/request of its own: its own price AND its own property
    (category, size or a title such as "IMPERIAL BEACH 2 LANTAI"), not a feature/requirement line."""
    if not _has_price(text):
        return False
    if _part_categories(text) or _AREA_SPEC.search(text):
        return True
    first = _TITLE_STOP.sub(" ", _MONEY_RE.sub(" ", re.sub(r"[*_~]", " ", _item_title(text).lower())))
    return bool(re.search(r"[a-z]{3,}", first))


def _part_key(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _context_header(text):
    return _is_context(text) and _closes(text)


def _named_property(chunk):
    return bool(_part_categories(chunk) or _AREA_SPEC.search(chunk))


def _assemble_parts(header, chunks, valid, own_header=_context_header, leading=None):
    """(shared header, part texts): invalid chunks join the previous part (or the header). A header that is a
    context itself (``own_header``: by default its own offer, price and property) is a part, never shared.
    ``leading``: a first chunk that passes it is a part too, never the header of the others (a numbered item
    without a readable price that names a property; a block with its own price). Identical parts count once."""
    head, parts = _trim_part(header), []
    if own_header and head and own_header(head):
        parts.append(head)
        head = ""
    for chunk in chunks:
        chunk = _trim_part(chunk)
        if not chunk:
            continue
        if valid(chunk) or (leading and not parts and leading(chunk)):
            parts.append(chunk)
        elif parts:
            parts[-1] = parts[-1] + "\n" + chunk
        else:
            head = (head + "\n" + chunk).strip()
    unique = {}
    for part in parts:
        unique.setdefault(_part_key(part), part)
    return head, list(unique.values())


def _compose(head, parts):
    """[(part text incl. the shared header, own text or None)]. A greeting is no shared context."""
    if head and _greeting(head):
        head = ""
    return [((head + "\n\n" + part) if head else part, part if head else None) for part in parts]


def _purpose_mark(text):
    """'buyer' | 'renter' | None from the who-word on a section's first line."""
    words = set(re.findall(_WHO, _first_line(text).lower()))
    return None if len(words) != 1 else "renter" if words & {"renter", "penyewa"} else "buyer"


# What a request states besides a price or an asset: rooms ("2BR"), a lease term ("Sewa 1th") or a budget statement.
_REQUEST_DETAIL = re.compile(r"(?i)\b\d\s*(?:br|bed\w*|kt)\b|\b(?:sewa|beli|kontrak)\s*(?:min\w*\s*)?\d+\s*(?:th|thn|tahun|bln|bulan)\b"
                             r"|\bstudio\b")


def _request_details(text):
    body = "\n".join(text.split("\n")[1:]).lower()
    return bool(_REQUEST_DETAIL.search(body) or _BUDGET_STATEMENT.search(body) or default_places().recognize(body))


def _section_parts(body):
    starts = [m.start() for m in _SECTION_MARK.finditer(body)]
    if len(starts) < 2:
        return None
    chunks = [body[s:e] for s, e in zip(starts, starts[1:] + [len(body)])]
    last = [None]

    def valid(chunk):
        # A renter section never joins a buyer section, even without a budget or asset of its own; a section
        # under its own heading that states what it wants ("*Renter Request* / Klaska 2BR Full Furniture / Sewa 1th
        # bugdet mengikuti") is a request of its own.
        mark = _purpose_mark(chunk)
        lines = sum(bool(_HAS_WORD.search(line)) for line in chunk.split("\n"))
        ok = _has_substance(chunk) or (bool(mark) and last[0] is not None and mark != last[0] and lines >= 2) or (
            bool(mark) and lines >= 3 and _request_details(chunk))
        if ok:
            last[0] = mark or last[0]
        return ok

    # "*3 orang Renter Request budget 100jt …*" above two "*Buyer Request*" sections is a request of its own.
    head, parts = _assemble_parts(body[:starts[0]], chunks, valid, own_header=_own_price)
    if len(parts) < 2:
        return None
    # A stray "Renter request" line above parts that each name their own buyer/renter is not shared.
    if head and not _has_substance(head) and _WHO_WORD.search(head) and not default_places().recognize(head.lower()) and \
            all(_WHO_WORD.search(" ".join(line for line in part.split("\n")[:3])) for part in parts):
        head = ""
    return _compose(head, parts)


_PLACE_HEADING = re.compile(r"(?i)^[^a-z0-9\n]*(?:lokasi|area|daerah|wilayah)\b")


def _heading_block(block, places=False):
    """A short heading of a list without price, size, spec or contact ("*`JAKARTA SELATAN`*", "🤗 JUAL"); with
    ``places`` also a place line ("Lokasi *PEKANBARU*")."""
    lines = [line for line in block.split("\n") if _HAS_WORD.search(line)]
    return 0 < len(lines) <= 2 and len(block) <= 160 and not _has_price(block) and not _AREA_SPEC.search(block) \
        and not re.match(r"[\W_]*#", lines[0]) and not _ITEM_PREFIX.match(lines[0]) and not _PHONE_RE.search(block) \
        and not any(_continues(line) and not (places and _PLACE_HEADING.match(line)) for line in lines)


def _sublist_heading(text, nearest=False):
    """Offset in ``text`` where the heading blocks at its end begin ("*`JAKARTA SELATAN`*", "*🤗 Jual Tanah di LUAR
    PULAU*" / "Lokasi *PEKANBARU*"): the nearest block, and the blocks above it only when every block back to a
    ruled line is such a heading (never with ``nearest``). None when the text does not end with a heading."""
    lines = text.split("\n")
    found, i, ruled = [], len(lines), False
    while i > 0 and len(found) < 4:
        while i > 0 and not _HAS_WORD.search(lines[i - 1]) and not _RULE_LINE.match(lines[i - 1]):
            i -= 1
        if i == 0 or _RULE_LINE.match(lines[i - 1]):
            ruled = i > 0
            break
        j = i
        while j > 0 and _HAS_WORD.search(lines[j - 1]):
            j -= 1
        if not _heading_block("\n".join(lines[j:i]), places=True):
            break
        found.append(j)
        i = j
    if ruled and not nearest and found:
        line = found[-1]
    elif found and _heading_block("\n".join(lines[found[0]:]).strip()):
        line = found[0]
    else:
        return None
    return sum(len(x) + 1 for x in lines[:line])


def _marker_key(line):
    return re.sub(r"[*_~`\s]", "", _lead_marker(line))


def _numbered_starts(body, restarts):
    """(item starts, {start: heading start}): items follow their numbers 1, 2, 3 …; with ``restarts`` a "1." under
    a heading of its own ("*`JAKARTA SELATAN`*" / "1 GEDUNG KANTOR:") starts the next sub-list."""
    starts, heads, expected = [], {}, 1
    marks = sorted([(m.start(), int(m.group(1)), "item") for m in _NUMBERED.finditer(body)] +
                   [(m.start(), int(m.group(1)), "loose") for m in _NUMBERED_LOOSE.finditer(body)] +
                   ([(m.start(), 1, "restart") for m in _RESTART_LOOSE.finditer(body)] if restarts else []))
    for start, number, kind in marks:
        if starts and start <= starts[-1]:
            continue
        if number == expected and kind != "restart" and (kind == "item" or expected > 1):
            starts.append(start)
            expected += 1
        elif restarts and number == 1 and starts:
            cut = _sublist_heading(body[starts[-1]:start])
            if cut is not None:
                heads[start] = starts[-1] + cut
                starts.append(start)
                expected = 2
    return starts, heads


# A struck-through item ("*3. ~Rosebay Tower G: 2BR - 79m2 - Nego~*") is sold: no part of its own, no part's text.
_STRUCK_ANY = re.compile(r"~[^~]*~")


def _struck_out(chunk):
    rest = _ITEM_PREFIX.sub(" ", chunk.strip(), count=1)
    return "~" in rest and not _HAS_WORD.search(_STRUCK_ANY.sub(" ", rest))


def _numbered_parts(body, own_header=False):
    starts, heads = _numbered_starts(body, True)
    parts = _numbered_lists(body, starts, heads, own_header)
    if parts is None and heads:
        parts = _numbered_lists(body, *_numbered_starts(body, False), own_header)
    return parts


def _numbered_lists(body, starts, heads, own_header):
    if len(starts) < 2:
        return None
    header = body[:starts[0]]
    if not own_header and _closes(header):
        return None  # the text above the list offers something itself: blank-line blocks decide (_block_parts)
    lists = [["", []]]
    for start, end in zip(starts, [heads.get(s, s) for s in starts[1:]] + [len(body)]):
        if start in heads:
            lists.append([body[heads[start]:start], []])
        if not _struck_out(body[start:end]):
            lists[-1][1].append(body[start:end])
    if len(lists) > 1:
        # The region heading right above the first sub-list ("*`JAKARTA PUSAT`*", or the "🟩 *DIJUAL TANAH –
        # SURAMADU*" block when a later sub-list opens with "🟩 *DIJUAL TANAH – TUBAN*") heads that sub-list only.
        keys = {_marker_key(_first_line(heading)) for heading, _ in lists[1:]} - {""}
        cut = next((m.start() for m in reversed(list(re.finditer(r"(?:^|(?<=\n\n))[ \t]*\S", header)))
                    if _marker_key(header[m.start():].lstrip().split("\n")[0]) in keys), None)
        if cut is None:
            cut = _sublist_heading(header, nearest=True)
            cut = cut if cut is not None and _HAS_WORD.search(header[:cut]) else None
        if cut is not None:
            lists[0][0], header = header[cut:], header[:cut]
    chunks = [c for _, items in lists for c in items]
    valid = [_offer_item(c) for c in chunks]
    # Mostly feature/requirement lines (one budget for the whole request) are not separate offers.
    if sum(valid) < 2 or sum(valid) * 2 < len(chunks):
        return None
    # A price list of something else ("1. 10 kva harga sewa 500 ribu/8 jam") names no property and no size.
    if not _named_property(header) and sum(_named_property(c) for c in chunks) * 2 < len(chunks):
        return None
    if len(chunks) > MAX_SEGMENTS:
        raise _Whole()  # a catalogue of more offers than parts allowed stays whole (R1)
    flags = {_trim_part(c): v for c, v in zip(chunks, valid)}

    def is_valid(c):
        return flags[c] if c in flags else _offer_item(c)

    head, parts = _assemble_parts(header, lists[0][1], is_valid, own_header=_context_header if own_header else None,
                                  leading=_named_property)
    if len(lists) == 1:
        return _compose(head, parts) if len(parts) >= 2 else None
    if not parts:
        return None
    out, keys = [(_trim_part(lists[0][0]), part) for part in parts], {_part_key(part) for part in parts}
    for heading, items in lists[1:]:
        sub_head, sub_parts = _assemble_parts(heading, items, is_valid, own_header=None, leading=_named_property)
        sub_parts = [part for part in sub_parts if _part_key(part) not in keys]
        keys.update(_part_key(part) for part in sub_parts)
        if sub_parts:
            out += [(sub_head, part) for part in sub_parts]
        else:
            # A sub-list without an offer of its own continues the part before it.
            out[-1] = (out[-1][0], out[-1][1] + "\n\n" + sub_head)
    if len(out) < 2:
        return None
    if head and _greeting(head):
        head = ""
    result = []
    for sub, part in out:
        prefix = "\n\n".join(x for x in (head, sub) if x)
        result.append(((prefix + "\n\n" + part) if prefix else part, part if prefix else None))
    return result


def _sizes(text):
    lowered = text.lower()
    return {v for v in (*_areas(lowered, "land"), *_areas(lowered, "building")) if v} | \
        {_number(v) for v in re.findall(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:m2|m²)(?!\w)", lowered)}


def _teaser_sizes(line):
    """Sizes a one-line teaser names, also without a unit ("LUAS 240 HARGA 7 M")."""
    return _sizes(line) | {_number(v) for v in re.findall(r"\b(?:luas\w*|lt|lb)\s*[.:=]?\s*(\d+(?:[.,]\d+)?)(?![\d.,]*\s*[x×])",
                                                          line.lower())}


def _offer_facts(text):
    lowered = text.lower()
    values = frozenset(c['max'] if c['max'] is not None else c['min'] for c in _price_candidates(_asking_text(text)))
    return values, _sizes(text), _part_category_tuple(text), frozenset(default_places().recognize(lowered)), \
        frozenset(re.findall(r"[^\W_]{2,}", lowered))


def _same_offer(a, b):
    """The same property written twice in one bubble: the same amounts, asset and places and a shared size. When
    a side names no place the texts must be nearly the same ("Apartemen Orchard 35 jt" is no repeat of "Apartemen
    Amor 35 jt")."""
    if not (bool(a[0]) and a[0] == b[0] and (set(a[2]) & set(b[2]) or not a[2] or not b[2])
            and (bool(a[1] & b[1]) or len(a[0]) >= 2 or not a[1] or not b[1])):
        return False
    if a[3] and b[3]:
        return a[3] <= b[3] or b[3] <= a[3]
    if (not a[3] and not a[1]) or (not b[3] and not b[1]):
        return True  # a teaser without place or size ("*TURUN HARGA* … 2M") restates the detailed offer
    return len(a[4] & b[4]) >= 0.6 * max(1, min(len(a[4]), len(b[4])))


_RULE_LINE = re.compile(r"^\s*(?:[-‐‑–—=═▬_~#*•.·]\s*){3,}$")


def _blocks(body, rules=None):
    """Blank-line blocks; lines without letters or digits ("----", "═══", ".", "🔵🔶") separate blocks too.
    ``rules``: a list that receives the blocks written right after a ruled line ("------", "=====", "####")."""
    marked = [line if _HAS_WORD.search(line) else "\x00" if _RULE_LINE.match(line) else "" for line in body.split("\n")]
    out, ruled = [], False
    for piece in re.split(r"(\x00)|\n[ \t]*\n", "\n".join(marked)):
        if piece == "\x00":
            ruled = True
            continue
        if piece and piece.strip("\n \t"):
            out.append(piece.strip("\n \t"))
            if rules is not None and ruled:
                rules.append(out[-1])
            ruled = False
    return out


def _continues(line):
    """A spec, price or contact line that continues the property above it. A fully bold heading of a few words
    ("*AJB NORMAL*", "*Type Milenial II*") is a title, unless it states a price or a spec value."""
    if not _CONTINUATION.match(line):
        return False
    stripped = line.strip()
    heading = re.fullmatch(r"[^\w\s]*[*_]+([^*_\n]+)[*_]+[^\w\s]*", stripped)
    words = heading.group(1).split() if heading else []
    return not (heading and len(words) >= 2 and not re.search(r"[:=]|\d", heading.group(1)) and not _PRICE_CONTINUATION.match(line))


def _title_line(line):
    if not line:
        return False
    if _OFFER_START.match(line) or _ITEM_PREFIX.match(line):
        return True
    if _continues(line) or (_BULLET.match(line) and not re.match(r"\s*[-•·>◦▪]\s*\*", line)):
        return False
    letters = re.sub(r"[^A-Za-z]", "", line)
    return bool(re.match(r"\s*[^\w\s(]", line)) or (len(letters) >= 4 and letters.isupper()) or bool(_categories(normalized(line)))


def _second_line(block):
    lines = [line.strip() for line in block.split("\n") if _HAS_WORD.search(line)]
    return lines[1] if len(lines) > 1 else ""


def _teaser_line(line):
    """A short remark above a title ("Langka! Jarang ada", "Ready 15 Oktober 2026") that is no spec, price or
    contact line itself."""
    return len(line.split()) <= 6 and not _continues(line) and not _has_price(line) and not _AREA_SPEC.search(line) \
        and not _PHONE_RE.search(line) and not _MARKER_PREFIX.match(_strip_symbols(line))


def _title_like(block):
    """A block whose first line can start a property: an offer verb, an item number, a bold/emoji title, an
    all-caps title or a property word; spec and contact lines ("LT 120", "Harga 2 M", "Hub …") continue. A short
    remark may stand above the title ("Langka! Jarang ada" / "*Kavling Margomulyo Permai*")."""
    line = _first_line(block)
    if _title_line(line):
        return True
    second = _second_line(block)
    return bool(line) and _teaser_line(line) and bool(second) and bool(
        _OFFER_START.match(second) or (re.match(r"\s*[^\w\s(]", second) and not _BULLET.match(second) and not _continues(second)))


def _subtitle(block):
    """A block that opens with its own marked title: an offer verb, an item number or a bold/emoji line (never a
    bracketed remark such as "(Hanya 1M an, …)")."""
    line = _first_line(block)
    return bool(line) and bool(_OFFER_START.match(line) or _ITEM_PREFIX.match(line) or
                               (re.match(r"\s*[^\w\s(]", line) and not _BULLET.match(line) and not _continues(line)))


def _header_like(text):
    """A catalogue heading ("HOT LISTING AREA SURABAYA PUSAT"): no size, no own price, no asset of its own
    unless it says it is a listing/edition."""
    return not _AREA_SPEC.search(text) and not _closes(text) and \
        (not _part_categories(text) or bool(_HEADER_WORDS.search(text.lower())))


# A unit of the development named above it: "#Unit A", "• *Unit B03-P*", "*Type Alpha*", "Tower A", "#1".
_UNIT_MARK = re.compile(r"(?i)^[^a-z0-9\n]*(?:\d{1,2}\s*[.)]\s*[^a-z0-9\n]*)?(?:(?:unit|type|tipe|tower|blok|block)\b|"
                        r"kav(?:ling)?\.?\s*(?:no\.?\s*)?(?:[a-z]{0,2}\s*\d|[a-z]{1,2}\b)|no\.?\s*\d|(?<=#)\s*\d{1,2}\b|\d{1,2}\s+units?\s*:?\s*$)")


def _lead_marker(line):
    """The symbols a title opens with (">>>", "**", "📍"); plain bold or italics marks every title alike."""
    m = re.match(r"\s*([^\w\s]+)", line)
    marker = m.group(1) if m else ""
    return "" if re.fullmatch(r"[*_~]?[*_~]?", marker) and marker != "**" else marker


def _title_categories(text):
    lines = [line for line in text.split("\n") if _HAS_WORD.search(line)][:2]
    return set(_body_categories(re.sub(r"[*_~]", "", "\n".join(lines))))


def _subheader_scope(sub, parts):
    """Titles above the first property ("*Dijual Rumah Wiyung*" above "*UNIT A*", "*UNIT B*") head the
    properties after them, up to a property that is a peer of the title (">>>Ruko …" after ">>>Ruko …") or names
    another kind of asset; if none follows, the title belongs to the first property alone."""
    marker, kinds = _lead_marker(_first_line(sub)), _title_categories(sub)
    markers = {_lead_marker(_first_line(part)) for part in parts}
    if not marker and len(markers) == 1 and "" not in markers and len(parts) > 1:
        # "DIJUAL TANAH / SIDOARJO" above twelve "➡️SARIROGO", "➡️GUDANG BY PASS KRIAN" items heads them all.
        return len(parts)
    places = set(default_places().recognize(sub.lower()))
    scope = 0
    for part in parts:
        own_places = set(default_places().recognize("\n".join(part.split("\n")[:3]).lower())) - places
        cats = _title_categories(part)
        peer = marker and any(_lead_marker(line) == marker and not _UNIT_MARK.match(line)
                              for line in (_first_line(part), _second_line(part)))
        if peer or (own_places and kinds and cats and not cats & kinds):
            break
        scope += 1
    return max(scope, 1)


def _unit_headings(groups):
    """A title without price or size directly above a unit ("*Apartemen Rainbow Spring …*" above "• *Unit B03*",
    "Tersedia beberapa area …" above "🔹 Lokasi I") heads the units after it until a property with a title of its
    own: [(heading text or "", group blocks)]. Units start with a unit word or with the lead symbol of the
    groups after them."""
    markers = {_lead_marker(_first_line(group[0])) for group in groups[1:]} - {""}

    def unit_line(line):
        return bool(_UNIT_MARK.match(line.strip()) or (markers and _lead_marker(line) in markers))

    out, current = [], ""
    for i, group in enumerate(groups):
        siblings = i + 1 < len(groups) and unit_line(_first_line(groups[i + 1][0]))
        lines = group[0].split("\n")
        j = next((n for n, line in enumerate(lines) if _HAS_WORD.search(line) and unit_line(line)), 0)
        if siblings and j and not _has_price("\n".join(lines[:j])) and not _AREA_SPEC.search("\n".join(lines[:j])):
            # "Baru Gress Darmo Baru barat" on the line above "*Type A*" heads every type.
            group = ["\n".join(lines[:j]), "\n".join(lines[j:])] + group[1:]
        k = 0
        while k < len(group) - 1 and not _has_price(group[k]) and not _AREA_SPEC.search(group[k]) \
                and not unit_line(_first_line(group[k])):
            k += 1
        if siblings and k and not unit_line(_first_line(group[k])):
            # "Ada 5 tipe : / Kavling A / LT …" under the estate's intro: the lines above the first unit head it too.
            inner = group[k].split("\n")
            j = next((n for n, line in enumerate(inner) if _HAS_WORD.search(line) and unit_line(line)), 0)
            if j and not _has_price("\n".join(inner[:j])) and not _AREA_SPEC.search("\n".join(inner[:j])):
                group = group[:k] + ["\n".join(inner[:j]), "\n".join(inner[j:])] + group[k + 1:]
                k += 1
        if siblings and k and unit_line(_first_line(group[k])):
            current = "\n\n".join(group[:k])
            out.append((current, group[k:]))
        elif current and unit_line(_first_line(group[0])):
            out.append((current, group))
        else:
            current = ""
            out.append(("", group))
    return out


def _offer_kind(text):
    kinds = {c['kind'] or ('rent' if c['basis'] in ('per_year', 'per_month') else None) for c in _price_candidates(_asking_text(text))}
    return kinds - {None}


def _labels(text):
    """The first word of every line ("luas", "kt", "harga"); words glued to digits ("Li69") are codes."""
    out = []
    for line in text.lower().split("\n"):
        m = re.search(r"(?<!\w)[a-z]{2,}(?!\w)", line)
        if m:
            out.append(m.group())
    return out


def _sibling_unit(group, block):
    """A block that is a complete unit of its own next to the one before it, without a title: "* Tipe HD 3.5 …
    Harga 3,1 M" after "* Tipe DS 3.0 … Harga 2,6 M", or mostly the same spec lines with other values
    ("Luas Nett … Luas Gross … Jual 900 JT" after "Luas Nett … Luas Gross … Jual 1,1M")."""
    if not (_closes(block) and _AREA_SPEC.search(block)):
        return False
    if not _UNIT_MARK.match(_first_line(block)):
        anchor = next((i for i in range(len(group) - 1, -1, -1) if _AREA_SPEC.search(group[i])), None)
        if anchor is None:
            return False
        before, labels = _labels("\n".join(group[anchor:])), _labels(block)
        shared = set(before) & set(labels)
        if not labels or not before or labels[0] != before[0] or len(shared) < 0.6 * min(len(set(before)), len(set(labels))):
            return False
    return not _same_offer(_offer_facts("\n\n".join(group)), _offer_facts(block))


# "*#Dekat Stasiun MRT Cipete Raya!*", "*#5 Menit Pintu Tol!*", "*10 menit ke Stasiun Kebayoran Lama*" under a
# property: its taglines, never the next title.
_TAGLINE_START = re.compile(r"^[*_~`\s]*#[^\W_]*\s*\S+(?:\s+\S+){2,}")
_TAGLINE_CUE = re.compile(r"(?i)!|\b(?:dekat|dkt|menit|mnt|akses|selangkah|samping|depan|sebelah|view)\b")
_LANDMARK_REMARK = re.compile(r"(?i)^\*[^*\n]*\b(?:dekat|dkt|menit|mnt|selangkah|samping|sebelah)\b[^*\n]*\*\W*$")


def _tagline_line(line):
    line = line.strip()
    return bool(_TAGLINE_START.match(line) and _TAGLINE_CUE.search(line) or _LANDMARK_REMARK.match(line)) and \
        len(line.split()) >= 3 and not _has_price(line) and not _AREA_SPEC.search(line)


def _tagline_block(block):
    lines = [line for line in block.split("\n") if _HAS_WORD.search(line)]
    return 0 < len(lines) <= 4 and all(_tagline_line(line) for line in lines)


_SHARED_TAIL = re.compile(r"(?i)^[^a-z0-9\n]*(?:semua\s+unit|all\s+units?|untuk\s+semua(?:\s+unit)?|ketentuan|"
                          r"syarat\s+(?:dan|&)\s+ketentuan)\b")


def _block_parts(body):
    """Several properties written one after another, each in its own blank-line block(s) with its own price:
    a property ends with its price; the next title-like block starts the next one."""
    ruled = []
    blocks = _blocks(body, ruled)
    if len(blocks) < 2 or sum(_has_price(b) for b in blocks) < 2:
        return None
    ruled = set(ruled)
    # "*HOT LISTING AREA MAYJEND SUNGKONO*" on the first line of the first property is the shared heading.
    lines = blocks[0].split("\n")
    lead = 0
    while lead < len(lines) and _HEADER_WORDS.search(lines[lead].lower()) and not _has_price(lines[lead]) \
            and not _AREA_SPEC.search(lines[lead]) and not _CAPTION.match(lines[lead]):
        lead += 1
    header = "\n".join(lines[:lead])
    blocks = (["\n".join(lines[lead:])] if lead < len(lines) else []) + blocks[1:]
    # Titles without a price or size above a block that starts a property themselves ("*Dijual Rumah … Wiyung*"
    # above "*UNIT A*", "*UNIT B*") head the properties; a catalogue heading heads every one.
    sub = []
    while len(blocks) > 2 and not _has_price(blocks[0]) and not _AREA_SPEC.search(blocks[0]) and _subtitle(blocks[1]):
        block = blocks.pop(0)
        marker = _lead_marker(_first_line(block))
        # "🏡 *GAHARU CILANDAK*" above its specs, with "🏡 *JAYAMANDALA TEBET*" further down, titles one property.
        peers = marker and any(marker in (_lead_marker(_first_line(b)), _lead_marker(_second_line(b))) for b in blocks[1:])
        if not sub and _header_like(block) and not peers:
            header = (header + "\n" + block).strip()
        else:
            sub.append(block)
    # "🏡 *PEJATEN BARAT*" when other titles open with "🏡" too: a property title, so the offer below continues it.
    peers = {m for m, n in collections.Counter(_lead_marker(_first_line(b)) for b in blocks).items() if n >= 2} - {""}
    groups = [[blocks[0]]]
    for block in blocks[1:]:
        closed = _group_closed(groups[-1])
        # "3⛳ *RAYA CIRENDEU JAKSEL*" above "⭐️Di Jual Tanah Kosong …": a numbered title, not a catalogue heading.
        peer_title = len(groups[-1]) == 1 and (_lead_marker(_first_line(groups[-1][0])) in peers or bool(
            _ITEM_PREFIX.match(_first_line(groups[-1][0])))) and not _PHONE_RE.search(groups[-1][0]) and \
            not _URL.search(groups[-1][0]) and not _has_price(groups[-1][0])
        if not groups[-1][-1].rstrip(" *_").endswith(":") and not _tagline_block(block) and (  # "… terdiri dari:"
                (closed and (block in ruled or _title_like(block) or _sibling_unit(groups[-1], block))) or
                (not closed and not peer_title and _OFFER_START.match(_first_line(block))
                 and _header_like("\n\n".join(groups[-1])))):
            groups.append([block])
        else:
            groups[-1].append(block)
    if len(groups) > 1 and _CAPTION.match(groups[0][0]):
        groups[:2] = [groups[0] + groups[1]]  # an image caption summarises the description posted under it
    for i in range(len(groups) - 2, -1, -1):
        # "*LUAS 240 HARGA 7 M AREA PADANG GOLF*" above "*RIZWOOD PAKUWON INDAH* LT 240 …": a one-line teaser of it.
        teaser = groups[i][0]
        if len(groups[i]) == 1 and "\n" not in teaser.strip() and _teaser_sizes(teaser) & _sizes("\n\n".join(groups[i + 1])):
            groups[i:i + 2] = [groups[i] + groups[i + 1]]

    def valid(chunk):
        # A block titled with its own offer verb names its property even without a category ("For Rent / La Riz");
        # a price line with an offer verb ("*Sewa 165jt perthn*") is no title.
        first = _first_line(chunk)
        return _is_context(chunk) or (bool(_OFFER_START.match(first)) and not _has_price(first) and _own_price(chunk)
                                      and _specs(chunk))

    # "*Dijual Rp 9.5 Milyar (nego)*" under the last of several rent plots prices the whole lot, not that plot.
    tail, rest = (groups[-1][-1], "\n\n".join(groups[-1][:-1])) if len(groups[-1]) > 1 else \
        (groups[-1][0], "\n\n".join(groups[-2]) if len(groups) > 1 and not valid(groups[-1][0]) else "")
    if rest and _has_price(tail) and not _specs(tail) and _closes(rest) and _offer_kind(tail) and _offer_kind(rest) and \
            not _offer_kind(tail) & _offer_kind(rest):
        raise _Whole()
    # Parts as [heading, own text]: a heading (titles above the units) heads the part like the shared header.
    head, parts = _trim_part(header), []
    for heading, group in _unit_headings(groups):
        chunk = _trim_part("\n\n".join(group))
        if not chunk:
            continue
        titled = (heading + "\n\n" + chunk) if heading else chunk
        if valid(titled) or (not parts and _closes(titled)):
            parts.append([_trim_part(heading), chunk])
        elif parts:
            parts[-1][1] = parts[-1][1] + "\n" + chunk
        else:
            head = (head + "\n" + chunk).strip()
    # A part without a single spec line of its own ("📍 3 menit ke Mall … 💰 Rp450 JUTA NEGO") describes the
    # property next to it.
    for i in range(len(parts) - 1, -1, -1):
        if len(parts) > 1 and not _specs(parts[i][0] + "\n" + parts[i][1]):
            if i:
                parts[i - 1][1] += "\n\n" + parts.pop(i)[1]
            else:
                heading, own = parts.pop(0)
                parts[0] = [parts[0][0] or heading, own + "\n\n" + parts[0][1]]
    if len(parts) > 1:
        # "Semua Unit : di Lantai 9 … PPJB" after the last unit describes every unit.
        blocks = _PARAGRAPHS.split(parts[-1][1])
        shared = next((i for i in range(1, len(blocks)) if _SHARED_TAIL.match(_first_line(blocks[i]))), None)
        if shared and _closes("\n\n".join(blocks[:shared])) and not _has_price("\n\n".join(blocks[shared:])):
            tail = "\n\n".join(blocks[shared:])
            parts[-1][1] = "\n\n".join(blocks[:shared])
            for part in parts:
                part[1] += "\n\n" + tail
    if sub and parts:
        sub_text = _trim_part("\n\n".join(sub))
        for part in parts[:_subheader_scope(sub_text, [own for _, own in parts])]:
            part[0] = (sub_text + "\n\n" + part[0]).strip()
    distinct, keys = [], set()
    for heading, own in parts:
        full = (heading + "\n\n" + own) if heading else own
        facts = _offer_facts(full)
        if _part_key(full) not in keys and not any(_same_offer(facts, seen) for seen, _, _ in distinct):
            distinct.append((facts, heading, own))
            keys.add(_part_key(full))
    if len(distinct) < 2:
        return None
    if head and _greeting(head):
        head = ""
    return [("\n\n".join(x for x in (head, heading, own) if x), own if head or heading else None)
            for _, heading, own in distinct]


def _split_tail(parts):
    """A numbered item that holds more properties splits again: the last item runs to the end of the bubble
    (properties written after the list are their own parts, without the list's header), and an item whose
    numbering slipped ("1. Tanah …", "1. Tanah …", "3. Tanah …") holds its numbered siblings (they keep the header)."""
    out = []
    for index, (text, own) in enumerate(parts):
        try:
            tail = _block_parts(own if own is not None else text)
        except _Whole:
            if index == len(parts) - 1:
                raise  # a price for the whole lot under the last item ("*Dijual Rp 9.5 Milyar*") prices every item
            tail = None  # "Harga sewa … / Harga Jual …" of one item
        if not tail:
            out.append((text, own))
            continue
        head = text[:len(text) - len(own)].rstrip() if own is not None else ""
        last = index == len(parts) - 1
        # "3. *Sutorejo*" above "Rumah kotak … 1,8M" and "Rumah hook … 1,95M": the item's title heads its units.
        title = "" if last else _first_line(own if own is not None else text)
        if not (_ITEM_PREFIX.match(title) and len(title.split()) <= 8 and not _has_price(title) and not _AREA_SPEC.search(title)):
            title = ""
        for k, (tail_text, tail_own) in enumerate(tail):
            numbered = k == 0 or bool(_ITEM_PREFIX.match(_first_line(tail_own or tail_text)))
            prefix = "\n\n".join(x for x in (head if numbered or not last else "", "" if numbered else title) if x)
            out.append((prefix + "\n\n" + tail_text, tail_text) if prefix else (tail_text, tail_own))
    if len(out) > MAX_SEGMENTS:
        raise _Whole()
    return out


def _expand_numbered(parts):
    """A section that itself lists numbered requests/offers ("🏠 RENTER REQUEST 1. … Budget 65 jt/th 2. … 125 jt/th")
    splits again; the bubble header and the section heading stay with every item."""
    out = []
    for text, own in parts:
        body = own or text
        inner = _numbered_parts(body) if len(_NUMBERED.findall(body)) >= 2 else None
        if not inner:
            out.append((text, own))
            continue
        head = text[:len(text) - len(own)].rstrip() if own else ""
        for inner_text, inner_own in inner:
            out.append(((head + "\n\n" + inner_text, inner_own or inner_text) if head else (inner_text, inner_own)))
    return out


# Forwarded compilations sign each listing on the line its next title starts ("📱 : 0812-… *DISEWAKAN/ DIJUAL*").
_INLINE_NEXT = re.compile(r"(" + PHONE_PATTERN + r")(?:[ \t]+(?=[*🏷🏠🏢🏡]|(?:di)?(?:jual|sewa)|for\s+(?:sale|rent|lease))|(?=\*\w))", re.I)
# "Selamat malam / Mohon dibantu hubungi yg bersangkutan langsung yah" above forwarded requests: shared by none.
_GREETING_LINE = re.compile(r"(?i)^\W*(?:selamat\s+(?:pagi|siang|sore|malam)|pagi|siang|sore|malam|halo|hallo|hai|hi|dear|shalom|"
                            r"assalamu\w*|mohon\s+(?:dibantu|bantuan|info)|dibantu|tolong\s+dibantu|terima\s+kasih|thank\w*|"
                            r"cobroke|co-?broke|rekan|teman)\b")


def _greeting(text):
    lines = [line for line in text.split("\n") if _HAS_WORD.search(line)]
    return bool(lines) and all(_GREETING_LINE.match(line) or not re.search(r"[A-Za-z]{3}", line) for line in lines) \
        and not _has_substance(text)


# Requests headed "CARI SEWA RUMAH" / "CARI BELI GUDANG" or "*Client J*" / "*Client E*": each its own context.
_SEEK_MARK = re.compile(r"(?im)^[^a-z0-9\n]*(?:(?:di)?cari|mencari|butuh)\s+(?:sewa|beli|kontrak)\b|^[^a-z0-9\n]*client\s+[a-z0-9]{1,2}[^a-z0-9\n]*$")
_BUDGET_STATEMENT = re.compile(r"(?i)\b(?:budget|bugdet|budged|budjet|buget|bujet|bajet)\w*\s*[:=]?\s*(?:sesuai|menyesuaikan|mengikuti|ikut|nego|bebas|terbuka|open|fleksibel|flexible)")


def _seek_parts(body):
    starts = [m.start() for m in _SEEK_MARK.finditer(body)]
    # A heading on its own line ("*Client J*") and the request line under it start one context.
    starts = [s for i, s in enumerate(starts) if i == 0 or
              sum(bool(_HAS_WORD.search(line)) for line in body[starts[i - 1]:s].split("\n")) > 1]
    if len(starts) < 2:
        return None
    chunks = [body[s:e] for s, e in zip(starts, starts[1:] + [len(body)])]

    def valid(chunk):
        return bool(_part_categories(chunk)) and (_has_price(chunk) or bool(_BUDGET_STATEMENT.search(chunk)))

    # A request above the first heading ("*Renter A* Cari ruko … Budget 90 jt") is no shared header.
    if sum(valid(_trim_part(c)) for c in chunks) < 2 or valid(body[:starts[0]]):
        return None
    head, parts = _assemble_parts(body[:starts[0]], chunks, valid, own_header=None)
    return _compose(head, parts) if len(parts) >= 2 else None


# "Best Regards, / Alfon / Bright⭕n Priority": a closing without a phone that split_contact keeps in the body.
_CLOSING = re.compile(r"(?is)\n[ \t]*\n[ \t]*(?:best\s+regards|kind\s+regards|regards|warm\s+regards|salam(?:\s+\w+)?|"
                      r"hormat\s+(?:saya|kami))\b[^\n]*(?:\n[^\n]*){0,5}$")


def _segments(body):
    """[(part text incl. shared header, own text or None)] when the body clearly holds several contexts, else None."""
    closing = _CLOSING.search(body)
    if closing and not _has_price(closing.group()):
        body = body[:closing.start()]
    try:
        parts = _find_segments(_INLINE_NEXT.sub(r"\1\n\n", body))
    except _Whole:
        return None
    if not parts or len(parts) > MAX_SEGMENTS:
        return None
    return parts


def _find_segments(body):
    if len(body) < 40:
        return None
    lowered = body.lower()
    parts = None
    if len(_SECTION_MARK.findall(lowered)) >= 2:
        parts = _section_parts(body)
        if parts:
            parts = _expand_numbered(parts)
    if not parts and len(_SEEK_MARK.findall(lowered)) >= 2:
        parts = _seek_parts(body)
    numbered = len(_NUMBERED.findall(body)) >= 2
    if not parts and numbered:
        parts = _numbered_parts(body)
        if parts:
            parts = _split_tail(parts)
    if not parts:
        parts = _block_parts(body)
        if parts:
            parts = _expand_numbered(parts)
    if not parts and numbered:
        parts = _numbered_parts(body, own_header=True)
    return parts


_REQUEST_CUE = re.compile(r"\b(?:request|req|dicari|mencari|buyer|renter|investor|kebutuhan)\b")
_WEAK_REQUEST_CUE = re.compile(r"\b(?:cari|butuh|budget)\b")
_DOCUMENT_KINDS = ("buyer_request", "property_listing")


def _part_kind(text, whole, siblings):
    """Classification for a split part that does not classify on its own (R1): its own request marker,
    else the whole bubble's kind, else its siblings' kind, else a weak request cue."""
    text = normalized(_URL.sub(" ", text))
    if _REQUEST_CUE.search(text):
        return "buyer_request"
    if whole in _DOCUMENT_KINDS:
        return whole
    if siblings:
        return max(_DOCUMENT_KINDS, key=siblings.count)
    return "buyer_request" if _WEAK_REQUEST_CUE.search(text) else "property_listing"


def parse_bubble(text: str, author: str | None = None, glossary=None) -> list[ParsedDocument]:
    """Every context of one WhatsApp bubble (R1). A single-context bubble gives one document with
    segment_text None. A multi-context bubble gives one document per part, each parsed from
    ``part text + signature`` and owned by the same sales contact; parts share nothing else."""
    original = clean_text(text)
    body, signature = split_contact(strip_top_contact(original))
    parts = _segments(body)
    if parts:
        contact_name, contact_phones = extract_contact(original)
        prepared = []
        for position, (part, own) in enumerate(parts):
            part_body, part_signature, name, phones = part, signature, contact_name, contact_phones
            if _PHONE_RE.search(own or part):
                # A forwarded compilation signs every part ("1. … Hub: Lung 0812…  2. … Hub: Kenfat 0812…").
                own_name, own_phones = extract_contact(own or part)
                cut, own_signature = split_contact(part)
                if cut.strip() and own_signature:
                    part_body, part_signature = cut, own_signature
                    own = (split_contact(own)[0] or own) if own else None
                # The last part's signature continues the bubble's own; the bubble's sales contact keeps its name.
                if own_phones and (position < len(parts) - 1 or not contact_phones):
                    same = contact_phones and own_phones[0] == contact_phones[0] and contact_name
                    name, phones = (contact_name if same else own_name), own_phones
            prepared.append((part, own, part_body, part_signature, name, phones))

        def parse(item, kind=None):
            part, own, part_body, part_signature, name, phones = item
            doc = _parse_text(part_body + ("\n\n" + part_signature if part_signature else ""), author, glossary,
                              split=(part_body, part_signature), own=own, kind=kind)
            doc.contact_name = (name or '')[:100] or author_name(author)
            doc.contact_phones = list(phones)
            doc.contact_phone = phones[0] if phones else None
            doc.segment_text = part
            doc.content_key = content_hash(part)
            return doc

        docs = [parse(item) for item in prepared]
        siblings = [d.classification for d in docs if d.classification in _DOCUMENT_KINDS]
        if len(siblings) < len(docs):
            whole = _classification(apply_glossary(re.sub(r'[*_~]', '', body), glossary))[0]
            docs = [doc if doc.classification in _DOCUMENT_KINDS else parse(item, _part_kind(item[0], whole, siblings))
                    for doc, item in zip(docs, prepared)]
        docs = [doc for doc in docs if doc.classification in _DOCUMENT_KINDS]
        # Splitting is unreliable when fewer than two parts read as a request or a listing.
        if len(docs) >= 2:
            for index, doc in enumerate(docs):
                doc.segment_index, doc.segment_count = index, len(docs)
            return docs
    doc = _parse_text(original, author, glossary, split=(body, signature))
    doc.content_key = content_hash(text)
    return [doc]


def score_range(req_min, req_max, listing_min, listing_max, tolerance_pct: float) -> tuple[float, str]:
    # PostgreSQL NUMERIC values arrive as Decimal. Normalise here so the
    # tolerance arithmetic behaves the same for API, worker, and unit inputs.
    req_min = float(req_min) if req_min is not None else None
    req_max = float(req_max) if req_max is not None else None
    listing_min = float(listing_min) if listing_min is not None else None
    listing_max = float(listing_max) if listing_max is not None else None
    if req_min is None and req_max is None:
        return 100.0, "Not restricted"
    if listing_min is None and listing_max is None:
        return 35.0, "Not stated in listing"
    listing_value = listing_min if listing_min is not None else listing_max
    if req_min is not None and listing_value < req_min:
        lower = req_min * (1 - tolerance_pct / 100)
        if listing_value < lower:
            return 0.0, "Below minimum tolerance"
        return max(40.0, 100 - ((req_min - listing_value) / req_min * 100 / tolerance_pct * 50)), "Within lower tolerance"
    if req_max is not None and listing_value > req_max:
        upper = req_max * (1 + tolerance_pct / 100)
        if listing_value > upper:
            return 0.0, "Above maximum tolerance"
        return max(40.0, 100 - ((listing_value - req_max) / req_max * 100 / tolerance_pct * 50)), "Within upper tolerance"
    return 100.0, "Within range"


def location_score(request_locations: list[str], listing_locations: list[str]) -> float:
    if not request_locations:
        return 70.0
    if not listing_locations:
        return 20.0
    req_tokens = set(" ".join(request_locations).split())
    listing_tokens = set(" ".join(listing_locations).split())
    exact = set(request_locations) & set(listing_locations)
    if exact:
        return 100.0
    overlap = len(req_tokens & listing_tokens) / max(1, len(req_tokens))
    return min(90.0, overlap * 100)
