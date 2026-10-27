"""Built-in place knowledge (Surabaya, Gresik, Sidoarjo hierarchy) shared by the parser and the matcher.

A place is a lower-case name. ``DEFAULT_LOCATION_PARENTS`` maps a child place to the
area that contains it; company glossary entries of kind 'location' extend or override it.
It merges the hand-curated tree below with the verified kecamatan/kelurahan/estate data in
``location_data``: hand-curated entries win, except a hand-curated kelurahan moves into its kecamatan
(when that lies inside the hand-curated parent) and the entries listed in ``_HAND_OVERRIDES``.
"""
import re
from functools import lru_cache

from location_data import (AREA_ALIASES, ESTATE_ALIASES, ESTATES, HAND_TO_ALIAS, KECAMATAN, KELURAHAN, LEFT_OUT_NAMES,
                           NESTED, PREFIX_EXCEPTIONS as _VERIFIED_PREFIX_EXCEPTIONS, STRICT_ONLY_NAMES,
                           SURABAYA_KECAMATAN, SURABAYA_KECAMATAN_NEIGHBORS, UNSAFE_NAMES)

KNOWN_LOCATIONS = [
    "surabaya barat", "surabaya timur", "surabaya utara", "surabaya selatan", "surabaya pusat",
    "surabaya", "sidoarjo", "gresik", "mojokerto", "pasuruan", "malang", "semarang", "jakarta utara",
    "citraland", "galeria golf", "selat golf", "puncak golf", "graha famili", "pakuwon indah",
    "pakuwon city", "rungkut", "rungkut asri", "mayjen sungkono", "ciputra world", "petra",
    "universitas petra", "ubaya", "universitas surabaya", "its", "unair", "margomulyo", "tanjung perak",
    "sambikerep", "semampir", "wiyung", "darmo", "darmo permai", "manyar", "klampis", "klampis indah",
    "klampis anom", "mulyosari", "sutorejo", "jemursari", "margorejo", "kendangsari", "tenggilis",
    "waru", "krian", "pandaan", "lamongan", "denpasar", "badung", "pondok tjandra", "tunjungan",
    "royal residence", "wisata bukit mas", "grand harvest", "greenlake", "northwest boulevard", "g walk",
    "dharmahusada", "kertajaya", "gayungsari", "graha family", "villa valencia", "prambanan",
]

# Built-in synonyms (text rewrites before parsing); company glossary entries override them.
DEFAULT_GLOSSARY = {"regensi": "regency", "rgcy": "regency", "nashos": "national hospital", "nathos": "national hospital",
                    "graha family": "graha famili", "bdg": "bukit darmo golf", "sby": "surabaya", "citra land": "citraland",
                    "mayjend": "mayjen", "darmahusada": "dharmahusada", "wbm": "wisata bukit mas"}

SURABAYA_REGIONS = ("surabaya barat", "surabaya timur", "surabaya utara", "surabaya selatan", "surabaya pusat")

# Top-level cities/islands: a chain ending here is complete.
ROOT_PLACES = frozenset({
    "surabaya", "sidoarjo", "gresik", "mojokerto", "pasuruan", "malang", "batu", "lamongan", "tuban",
    "jember", "madiun", "kediri", "bali", "jakarta", "semarang", "bangkalan", "banyuwangi", "probolinggo",
    "ngawi", "jombang", "blitar", "bojonegoro", "nganjuk", "sumenep", "pamekasan", "sampang", "situbondo",
    "bondowoso", "lumajang", "magetan", "ponorogo", "pacitan", "trenggalek", "tulungagung", "bandung",
    "yogyakarta", "tangerang", "bekasi", "bogor", "depok", "solo", "batam", "medan", "makassar",
})


def _tree(groups):
    return {child: parent for parent, children in groups.items() for child in children}


# Hand-curated hierarchy (before the verified data).
_HAND_PARENTS = {
    **{region: "surabaya" for region in SURABAYA_REGIONS},
    **_tree({
        "surabaya barat": (
            "citraland", "pakuwon indah", "graha famili", "graha family", "wiyung", "darmo permai",
            "mayjen sungkono", "ciputra world", "galeria golf", "selat golf", "puncak golf", "sambikerep",
            "margomulyo", "royal residence", "wisata bukit mas", "grand harvest",
            "lontar", "lakarsantri", "benowo", "tandes", "sukomanunggal", "dukuh pakis",
            "dukuh kupang", "darmo harapan", "darmo satelit", "darmo indah", "darmo baru", "villa bukit mas",
            "dian istana", "national hospital", "hr muhammad", "kupang indah", "sememi", "romokalisari",
            "manukan", "balongsari", "lidah kulon", "lidah wetan", "pradah", "babatan", "puncak permai",
            "puncak bukit golf", "simpang darmo permai", "pakuwon mall", "bukit mas", "graha natura", "grand pakuwon",
            "grand sungkono lagoon", "alam galaxy", "darmo hill", "bukit darmo", "lenmarc", "sambiroto", "made",
        ),
        "citraland": (
            "citraland utara", "northwest", "g walk", "gwalk", "bukit golf", "telaga golf", "teluk golf", "woodland",
            "alam hijau", "taman puspa", "diamond hill", "eastwood", "villa taman telaga", "taman gapura",
            "raffles garden", "bukit palma", "stamford", "fullerton", "puri widya kencana", "waterfront",
            "north west", "pelican hill", "dempsey", "royal park", "district 9", "san diego hills",
            "golf island", "mansion park", "the greenlake", "greenlake", "citraland utama", "waterpark",
        ),
        # Sub-areas sit inside the place their name starts with ("Northwest Park" is in Northwest).
        "northwest": ("northwest boulevard", "northwest park", "northwest lake", "northwest hill", "northwest central"),
        # v4.4: Pergudangan Suri Mulia (Permai) lies on Jl. Margomulyo.
        "margomulyo": ("suri mulia",),
        "dempsey": ("dempsey hill",),
        "bukit darmo": ("bukit darmo golf",),
        "gresik": ("citraland kedamean", "citraland gresik", "citraland driyorejo", "kedamean", "menganti", "driyorejo",
                   "cerme", "kebomas", "legundi", "golden east", "manyar gresik"),
        "malang": ("citraland puncak tidar", "citra land puncak tidar", "citraland malang", "puncak tidar", "araya malang"),
        "pakuwon indah": ("waterplace", "la riz", "la viz", "pakuwon mall"),
        "pakuwon mall": ("anderson", "benson", "orchard", "tanglin", "ptc", "pakuwon trade center", "supermall"),
        "ciputra world": ("voila", "vertu"),
        "graha famili": ("graha golf",),
        "surabaya timur": (
            "manyar", "mulyosari", "mulyorejo", "kertajaya", "dharmahusada", "darmahusada", "klampis",
            "klampis indah", "klampis anom", "klampis jaya", "sutorejo", "rungkut", "rungkut asri",
            "rungkut industri", "pakuwon city", "its", "unair", "ubaya", "universitas surabaya", "tenggilis",
            "tenggilis mejoyo", "gubeng", "keputih", "sukolilo", "medokan", "medokan ayu", "penjaringan",
            "gunung anyar", "kalijudan", "nginden", "semolowaru", "merr", "galaxy mall", "wonorejo", "baruk",
            "araya", "galaxy bumi permai", "nirwana eksekutif", "babatan pantai", "lebak", "lebak indah", "purimas",
            "kutisari", "wisma permai", "gunawangsa", "wiguna", "kalisari", "pucang",
            "ngagel", "tambaksari", "kertajaya indah", "manyar kertoarjo", "manyar garden", "sukolilo park", "east coast",
        ),
        "pakuwon city": ("educity", "amor"),
        "medokan": ("medokan ayu",),
        "rungkut": ("rungkut asri", "rungkut industri"),
        "klampis": ("klampis indah", "klampis anom", "klampis jaya"),
        "kertajaya": ("kertajaya indah",),
        "manyar": ("manyar kertoarjo", "manyar garden"),
        "tenggilis": ("tenggilis mejoyo",),
        "embong": ("embong kenongo",),
        "tunjungan": ("tunjungan plaza",),
        "dharmahusada": ("dharmahusada indah",),
        "lebak": ("lebak indah",),
        "sukolilo": ("sukolilo park",),
        "surabaya selatan": (
            "jemursari", "margorejo", "kendangsari", "wonocolo", "gayungsari", "gayungan", "ketintang",
            "siwalankerto", "petra", "universitas petra", "wonokromo", "menanggal", "jambangan", "karah",
            "kebonsari", "ahmad yani",
        ),
        "surabaya pusat": ("tunjungan", "darmo", "embong", "genteng", "tegalsari", "bubutan", "simokerto", "kedungdoro",
                           "basuki rahmat"),
        # v4.5: central streets agents give as the address ("Jl. Pahlawan no. 6", "Raya Pemuda/Gubernur Suryo").
        "bubutan": ("jl pahlawan",),                                       # Kel. Alun-alun Contong
        "genteng": ("jl pemuda", "gubernur suryo", "panglima sudirman"),  # Kel. Embong Kaliasin
        # v4.6: Jl. Lombok lies in Kel. Ngagel, Wonokromo (OpenStreetMap; rumah123 "Tanah Jl Lombok, Ngagel Wonokromo"); Jl.
        # Teuku Umar in Kel. Dr. Soetomo, Tegalsari (OpenStreetMap, postcode 60264). Agents call both "pusat kota".
        "ngagel": ("jl lombok",),
        "dr sutomo": ("teuku umar",),
        "probolinggo": ("kraksaan",),
        "surabaya utara": ("tanjung perak", "semampir", "kenjeran", "pabean", "krembangan", "kalimas", "pasar atom"),
        "kenjeran": ("pantai mentari", "kenpark"),
        "sidoarjo": ("waru", "krian", "gedangan", "sedati", "buduran", "porong", "tulangan", "juanda", "tanggulangin",
                     "citra harmoni", "citragarden sidoarjo"),
        "waru": ("pondok tjandra", "tropodo", "tambak sawah", "tambak sumur"),
        "pasuruan": ("pandaan", "bangil", "prigen"),
        "mojokerto": ("ngoro", "mojosari", "trawas", "pacet"),
        # UNTAG (Universitas 17 Agustus 1945) sits in Semolowaru, Sukolilo.
        "semolowaru": ("untag",),
        # v4.8: Jl. Karang Empat (Besar) and Jl. Karang Asem lie in Kel. Ploso, Kec. Tambaksari (Suara Surabaya on the
        # Karang Empat Besar floods in Ploso, Tambaksari; rumah123/Trovit listings "Karang Asem, Ploso, Tambaksari").
        "ploso": ("karang empat", "karang asem"),
        "bali": ("denpasar", "badung", "kuta", "seminyak", "canggu", "ubud", "nusa dua", "jimbaran", "sanur"),
        "jakarta": ("jakarta utara", "jakarta barat", "jakarta selatan", "jakarta timur", "jakarta pusat"),
    }),
}

# Other spellings of one place (alias -> canonical). Company 'location' entries override them.
_HAND_ALIASES = {
    "north west": "northwest", "gwalk": "g walk", "the greenlake": "greenlake", "ptc": "pakuwon trade center",
    "universitas surabaya": "ubaya", "universitas petra": "petra", "graha family": "graha famili",
    "darmahusada": "dharmahusada", "citra land puncak tidar": "citraland puncak tidar", "batu malang": "batu",
    # v4.3: spellings agents write that named no place before ("hr mohamad", "manyar2 an" = around Manyar, "BSD").
    "hr mohamad": "hr muhammad", "hr mohammad": "hr muhammad", "hr muhamad": "hr muhammad", "nortwest": "northwest",
    "manyar2": "manyar", "darmo2": "darmo", "bsd": "tangerang", "bsd city": "tangerang", "gading serpong": "tangerang",
    # v4.3 fix: "satelit darmo dsk" is Darmo Satelit; "Eastcost" / "Eastcoast" is East Coast.
    "satelit darmo": "darmo satelit", "eastcost": "east coast", "eastcoast": "east coast", "east cost": "east coast",
    # v4.4: complexes agents name by their short form.
    "suri mulia permai": "suri mulia", "pergudangan suri mulia": "suri mulia", "kapas madya": "kapas madya baru",
    # v4.5: a street named like a common word is a place only with its street word ("Jl. Pahlawan", "Raya Pemuda").
    **{f"{street} {name}": f"jl {name}" for street in ("jln", "jalan", "raya") for name in ("pahlawan", "pemuda")},
    **{f"{street} diponegoro": "raya diponegoro" for street in ("jl", "jln", "jalan")},
    "gub suryo": "gubernur suryo",
    # v4.6: "Jalan Lombok" is the Surabaya street; "Lombok" alone is the island. Jakarta's city abbreviations.
    **{f"{street} lombok": "jl lombok" for street in ("jln", "jalan", "raya")},
    "jakpus": "jakarta pusat", "jaksel": "jakarta selatan", "jakbar": "jakarta barat", "jaktim": "jakarta timur",
    "jakut": "jakarta utara",
}
# Hand-curated entries the verified data proves wrong (child -> new parent).
_HAND_OVERRIDES = {
    "ngagel": "wonokromo",             # was surabaya timur: Kel. Ngagel is in Wonokromo (Surabaya Selatan)
    "kendangsari": "tenggilis mejoyo",  # was surabaya selatan: Kel. Kendangsari is in Tenggilis Mejoyo (Surabaya Timur)
    "east coast": "pakuwon city",       # was surabaya timur: East Coast Center/Park/Residence lie in Pakuwon City
    "villa bukit mas": "dukuh pakis",   # was surabaya barat: Jl. KH Abdul Wahab Siamin, Kel./Kec. Dukuh Pakis (OSM, estate data)
}
# Hand-curated places that are another name of one verified place become aliases (location_data.HAND_TO_ALIAS):
# araya -> galaxy bumi permai, tenggilis -> tenggilis mejoyo, pabean -> pabean cantian, kenpark -> kenjeran park,
# voila -> the voila.


def _chain(parents, place):
    chain, seen = [], {place}
    while place in parents and len(chain) < 20:
        place = parents[place]
        if place in seen:
            break
        seen.add(place)
        chain.append(place)
    return chain


def _merge():
    verified = {**SURABAYA_KECAMATAN, **KECAMATAN, **KELURAHAN, **ESTATES}
    parents = {region: "surabaya" for region in SURABAYA_REGIONS}
    parents.update(verified)
    for child, parent in _HAND_PARENTS.items():
        if child in HAND_TO_ALIAS:
            continue
        parent = _HAND_OVERRIDES.get(child, parent)
        parent = HAND_TO_ALIAS.get(parent, parent)
        if parent == child:
            continue
        placed = verified.get(child)
        # Hand-curated entries win; a kelurahan moves into its kecamatan when that lies inside the hand parent.
        if placed is None or placed == parent or child not in KELURAHAN or parent not in _chain(parents, placed):
            parents[child] = parent
    aliases = {**ESTATE_ALIASES, **AREA_ALIASES, **_HAND_ALIASES, **HAND_TO_ALIAS}
    merged = {}
    for child, parent in parents.items():
        if child in HAND_TO_ALIAS:
            continue
        for _ in range(3):
            parent = aliases.get(parent, parent)
        if parent == child:
            parent = verified.get(child)
        if parent:
            merged[child] = parent
    merged.update(NESTED)
    # A name agents use for another place ("Kertosono", "Singosari") would place that place here.
    merged = {child: parent for child, parent in merged.items() if child not in LEFT_OUT_NAMES}
    aliases = {alias: canonical for alias, canonical in aliases.items() if alias not in LEFT_OUT_NAMES}
    return merged, aliases


DEFAULT_LOCATION_PARENTS, DEFAULT_LOCATION_ALIASES = _merge()
# Prefix-named places that are genuinely elsewhere ("Darmo Permai" is not on Jl. Darmo).
PREFIX_EXCEPTIONS = frozenset({
    "araya malang", "babatan pantai", "manyar gresik", "citraland driyorejo", "citraland gresik", "citraland kedamean",
    "citraland malang", "citraland puncak tidar", "darmo baru", "darmo harapan", "darmo hill", "darmo indah",
    "darmo permai", "darmo satelit", "gading serpong",
}) | _VERIFIED_PREFIX_EXCEPTIONS
# Generic words that are never a place on their own ("hill", "central"), even after "only".
GENERIC_PLACE_WORDS = frozenset({
    "hill", "hills", "central", "golf", "park", "lake", "garden", "gardens", "residence", "residences", "city", "village",
    "regency", "indah", "permai", "raya", "baru", "asri", "mas", "utara", "selatan", "timur", "barat", "pusat", "tengah",
    "town", "house", "estate", "land", "view", "square", "boulevard", "avenue", "place", "point", "mansion", "villa",
    "cluster", "tower", "east", "west", "north", "south", "new", "the", "green", "royal", "grand", "golden", "elite",
})

# Generic words that would turn ordinary text into false places; never recognised alone.
_HAND_UNSAFE = {"waterfront", "orchard", "amor", "bukit golf", "bukit mas", "merr", "karah",
                "east coast", "anderson", "benson", "tanglin", "genteng", "embong", "kalimas", "babatan", "made",
                "lebak", "pucang", "waterpark", "royal park", "dempsey", "batu", "solo", "medan", "kenpark"}
_UNSAFE_PLACES = _HAND_UNSAFE | UNSAFE_NAMES
RECOGNIZED_PLACES = tuple(dict.fromkeys(
    [*KNOWN_LOCATIONS, *(p for p in DEFAULT_LOCATION_PARENTS if p not in _UNSAFE_PLACES),
     *(p for p in DEFAULT_LOCATION_PARENTS.values() if p not in _UNSAFE_PLACES),
     *(p for p in ROOT_PLACES if p not in _UNSAFE_PLACES),
     *(p for p in DEFAULT_LOCATION_ALIASES if p not in _UNSAFE_PLACES)]))
# On a line where the buyer names the only area they accept, these generic-looking names are places.
STRICT_ONLY_PLACES = tuple(sorted({p for p in _HAND_UNSAFE if p in DEFAULT_LOCATION_PARENTS and p != "made"}
                                  | STRICT_ONLY_NAMES))
# Districts that partition their parent: a place inside one of them is outside the others.
_PARTITIONS = frozenset(SURABAYA_REGIONS) | frozenset(SURABAYA_KECAMATAN) | frozenset(KECAMATAN) | ROOT_PLACES
# A space in a place name also matches "-" and an abbreviation dot ("G-Walk", "Jl. Dr. Soetomo").
_NAME_SEPARATOR = r"\.?[ -]"
# A city name after "Jl." is a street ("Jl. Bali", "Jl. Semarang"), not the city.
_STREET_PREFIX = re.compile(r"(?:\bjl|\bjln|\bjalan|\bgg|\bgang|\braya)\.?\s*$")


@lru_cache(maxsize=64)
def _names_regex(names, flexible=True):
    """One regex for many names, built as a character trie so matching stays fast with thousands
    of names. Longer names win over their prefixes (greedy optional branches)."""
    trie = {}
    for name in names:
        node = trie
        for char in name:
            node = node.setdefault(char, {})
        node[""] = {}

    def emit(node):
        branches = [(_NAME_SEPARATOR if flexible and char == " " else re.escape(char)) + emit(child)
                    for char, child in sorted(node.items()) if char]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return "(?:" + body + ")?" if "" in node else body

    return re.compile(r"(?<!\w)(?:" + emit(trie) + r")(?!\w)") if names else None


class _NameMatcher:
    """``finditer`` over the built-in names plus a company's own names as if they were one regex
    (leftmost match first, the longer name when both start at the same place). The built-in regex is
    compiled once per process; a company only compiles its few extra names."""

    def __init__(self, base, extra=None):
        self.base, self.extra = base, extra

    def finditer(self, text, pos=0):
        if self.extra is None or self.base is None:
            yield from (self.base or self.extra).finditer(text, pos)
            return
        a, b = self.base.search(text, pos), self.extra.search(text, pos)
        while a or b:
            if a and b and a.start() == b.start():
                match = a if a.end() >= b.end() else b
            elif a and (not b or a.start() < b.start()):
                match = a
            else:
                match = b
            yield match
            end = match.end()
            if a and a.start() < end:
                a = self.base.search(text, end)
            if b and b.start() < end:
                b = self.extra.search(text, end)

    def match(self, text, pos):
        """The longest name starting exactly at ``pos``."""
        found = [m for m in (r.match(text, pos) for r in (self.base, self.extra) if r is not None) if m]
        return max(found, key=lambda m: m.end()) if found else None

    def sub(self, replace, text):
        for regex in (self.base, self.extra):
            if regex is not None:
                text = regex.sub(replace, text)
        return text


def _matcher(base_names, names, flexible=True):
    extra = tuple(sorted(set(names) - set(base_names)))
    return _NameMatcher(_names_regex(base_names, flexible), _names_regex(extra, flexible) if extra else None)


_WORD_START = re.compile(r"(?<!\w)\w")


class _SafeNames:
    """``finditer`` over the names recognised in free text (or on a strict line), where a longer name that
    is not recognised still hides the names inside it: "Trans Icon Mall" is not "Icon Mall", "Royal Plaza
    Surabaya" is not "Plaza Surabaya". A name inside it stays when it agrees with the longer one
    ("Jemursari" in "RSI Jemursari", "Menganti" in "Garden Hills Menganti") or reaches past it."""

    def __init__(self, places, every, names, allowed):
        self.places, self.every, self.names, self.allowed = places, every, names, allowed

    def finditer(self, text):
        pos = 0
        while True:
            m = next(self.every.finditer(text, pos), None)
            if m is None:
                return
            name = normalize_place(m.group())
            if name in self.allowed:
                yield m
                pos = m.end()
                continue
            pos, inner_pos = m.end(), m.start()
            for start in _WORD_START.finditer(text, m.start(), m.end()):
                if start.start() < inner_pos:
                    continue
                inner = self.names.match(text, start.start())
                if inner and (inner.end() > m.end() or self.places._agrees(normalize_place(inner.group()), name)):
                    yield inner
                    inner_pos = inner.end()
                    pos = max(pos, inner.end())


_BASE_NAMES = tuple(sorted(set(RECOGNIZED_PLACES)))
_BASE_LOOSE = tuple(sorted(set(RECOGNIZED_PLACES) | set(STRICT_ONLY_PLACES)))
_BASE_ALL = tuple(sorted(set(_BASE_LOOSE) | _UNSAFE_PLACES))
_SAFE_SET, _LOOSE_SET = frozenset(_BASE_NAMES), frozenset(_BASE_LOOSE)
_BASE_SPELLED = tuple(sorted(a for a in DEFAULT_LOCATION_ALIASES if " " in a
                             or a.replace(" ", "") != DEFAULT_LOCATION_ALIASES[a].replace(" ", "")))


def normalize_place(value):
    return re.sub(r"\s+", " ", re.sub(r"[^\w&' ]+", " ", str(value or "").lower())).strip()


def glossary_entries(glossary=None):
    """Normalise a legacy {alias: canonical} dict or a list of entry dicts.

    Returns dicts with lower-case ``term``, ``canonical`` (descriptions after " -- " removed),
    ``kind`` ('synonym'|'location'|'category'), ``parent`` and ``described`` (legacy description flag).
    """
    out = []
    if not glossary:
        return out
    items = glossary.items() if isinstance(glossary, dict) else (
        ((e.get("term") or e.get("alias")), e) for e in glossary if isinstance(e, dict))
    for term, value in items:
        entry = value if isinstance(value, dict) else {"canonical": value}
        canonical = str(entry.get("canonical") or "")
        term = str(term or "").strip().lower()
        described = " -- " in canonical or canonical.strip().startswith("--")
        canonical = re.split(r"\s+--\s+", canonical, maxsplit=1)[0].strip().lower()
        kind = (entry.get("kind") or "synonym").lower()
        if not term or not canonical or kind not in {"synonym", "location", "category"}:
            continue
        parent = normalize_place(entry.get("parent")) or None
        out.append({"term": term, "canonical": canonical, "kind": kind, "parent": parent, "described": described})
    return out


class Places:
    """Location hierarchy, aliases and place recognition for one company."""

    def __init__(self, entries=()):
        parents = dict(DEFAULT_LOCATION_PARENTS)
        aliases = dict(DEFAULT_LOCATION_ALIASES)
        extra = []
        for e in entries:
            if e["kind"] != "location":
                continue
            term, canonical = normalize_place(e["term"]), normalize_place(e["canonical"])
            if not term or not canonical:
                continue
            if term != canonical:
                aliases[term] = canonical
            else:
                aliases.pop(term, None)  # a company place of its own is not a built-in alias
            if e.get("parent") and e["parent"] != canonical:
                parents[canonical] = e["parent"]
            extra += [term, canonical]
        self.parents, self.aliases = parents, aliases
        self.known = set(RECOGNIZED_PLACES) | set(parents) | set(parents.values()) | set(extra) | ROOT_PLACES | set(aliases)
        extra = {n for n in extra if n}
        names, loose = _SAFE_SET | extra, _LOOSE_SET | extra
        every = _matcher(_BASE_ALL, loose | _UNSAFE_PLACES)
        self.pattern = _SafeNames(self, every, _matcher(_BASE_NAMES, names), names)
        self.loose_pattern = _SafeNames(self, every, _matcher(_BASE_LOOSE, loose), loose)
        spelled = [a for a in aliases if " " in a or a.replace(" ", "") != aliases[a].replace(" ", "")]
        self._alias_re = _matcher(_BASE_SPELLED, spelled, flexible=False) if spelled else None
        self._relations = {}
        self._chains = {}
        self._canonical = {}
        self._regions = {}
        self._holders = None
        self._exceptions = PREFIX_EXCEPTIONS | {self.canonical(name) for name in PREFIX_EXCEPTIONS}

    def canonical(self, place):
        value = self._canonical.get(place)
        if value is None:
            value, seen = normalize_place(place), set()
            if value not in self.known and self._alias_re:
                # "north west park" is "northwest park" when that spelling is a known place.
                respelled = self._alias_re.sub(lambda m: self.aliases.get(m.group(), m.group()), value)
                value = respelled if respelled in self.known else value
            while value in self.aliases and value not in seen and len(seen) < 6:
                seen.add(value)
                value = self.aliases[value]
            if len(self._canonical) < 100000:
                self._canonical[place] = value
        return value

    def ancestors(self, place):
        place = self.canonical(place)
        chain = self._chains.get(place)
        if chain is None:
            chain, seen, current = [], {place}, place
            while current in self.parents and len(chain) < 12:
                current = self.parents[current]
                if current in seen:
                    break
                seen.add(current)
                chain.append(current)
            self._chains[place] = chain = tuple(chain)
        return chain

    def _partition(self, place):
        """Regions, kecamatan and cities; a kelurahan while it is still placed in its kecamatan."""
        if place in KELURAHAN:
            return KELURAHAN[place] in self.ancestors(place)
        return place in _PARTITIONS

    def _may_overlap(self, place, target):
        """A place not placed in any district of an area (a township spanning several kecamatan, a road,
        a brand) may overlap a district inside that area: being elsewhere in the tree proves nothing."""
        if self._partition(place) or not self._partition(target):
            return False
        anchor = next((a for a in self.ancestors(place) if self._partition(a)), None)
        return anchor is not None and anchor in self.ancestors(target)

    def _agrees(self, inner, outer):
        """A name read inside a longer one may stand for it unless they are in different regions."""
        a, b = self.canonical(inner), self.canonical(outer)
        if a == b or a in self.ancestors(b) or b in self.ancestors(a):
            return True
        ra, rb = self.region_of(a), self.region_of(b)
        return not (ra and rb and ra != rb)

    def _named_after(self, place, target, place_names, target_names):
        """``place`` carries the whole name of ``target``, a road, area or development rather than a district
        ("Ruko Raya Jemursari" on Jl. Jemursari, "Kedung Baruk" around Baruk), and lies in the area directly
        holding ``target`` ("Puncak Bukit Golf" in Dukuh Pakis is not CitraLand's Bukit Golf). ``*_names``:
        the canonical and the written name. ``PREFIX_EXCEPTIONS`` lists the names that lie elsewhere."""
        parent = self.parents.get(target)
        if parent is None or place in self._exceptions or self._partition(target):
            return False
        if not any(name != other and not set(name.split()) <= GENERIC_PLACE_WORDS
                   and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", other)
                   for name in target_names for other in place_names):
            return False
        return parent in self.ancestors(place)

    def development(self, place):
        """A named development or township: a located place with places of its own that is no district or city."""
        if self._holders is None:
            self._holders = frozenset(self.parents.values())
        place = self.canonical(place)
        return place in self._holders and not self._partition(place) and self.region_of(place) is not None

    def development_of(self, place):
        """The nearest development holding ``place`` (None when only districts hold it)."""
        return next((a for a in self.ancestors(place) if self.development(a)), None)

    def located(self, place):
        place = self.canonical(place)
        chain = self.ancestors(place)
        return place in ROOT_PLACES or bool(chain and chain[-1] in ROOT_PLACES)

    def region_of(self, place):
        """Surabaya sub-region or top-level city of a place, None for 'surabaya' itself/unknown."""
        if place in self._regions:
            return self._regions[place]
        result = None
        for p in (self.canonical(place), *self.ancestors(place)):
            if p in SURABAYA_REGIONS or (p in ROOT_PLACES and p != "surabaya"):
                result = p
                break
        self._regions[place] = result
        return result

    def recognize(self, text, strict=False):
        """Known places in ``text`` (non-overlapping, longest first), canonicalised.

        ``strict``: the text is a line naming the only area a buyer accepts, where names that are
        too generic for free text ("Dempsey", "Tanglin") are places too."""
        text = text.lower()
        found = []
        for m in (self.loose_pattern if strict else self.pattern).finditer(text):
            place = m.group()
            if place in ROOT_PLACES and place != "surabaya" and _STREET_PREFIX.search(text[max(0, m.start() - 8):m.start()]):
                continue
            found.append(self.canonical(place))
        return list(dict.fromkeys(found))

    def _target_chain(self, target):
        chain = self.ancestors(target)
        if chain or target in self.known:
            return chain, self.located(target)
        # An unlisted place is treated as a child of the longest known place it names.
        inner = next(iter(self.recognize(target)), None)
        if inner and inner != target:
            return (inner, *self.ancestors(inner)), self.located(inner)
        return (), False

    def relation(self, place, target):
        """How a listing place relates to a place a buyer insists on.

        'inside'  : the place is the target or lies within it.
        'general' : the place only names a broader area that contains the target.
        'outside' : both are located in the hierarchy and do not overlap.
        None      : no evidence either way.
        """
        key = (place, target)
        if key in self._relations:
            return self._relations[key]
        p, t = self.canonical(place), self.canonical(target)
        result = None
        if p == t or t in self.ancestors(p):
            result = "inside"
        else:
            t_chain, t_located = self._target_chain(t)
            if p in t_chain:
                result = "general"
            elif p not in self.known:
                # Free-text fragments ("lokasi: pondok tjandra indah blok c") only prove inclusion.
                if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", p):
                    result = "inside"
            elif self._named_after(p, t, {p, normalize_place(place)}, {t, normalize_place(target)}):
                result = "inside"
            elif self._named_after(t, p, {t, normalize_place(target)}, {p, normalize_place(place)}):
                result = "general"
            elif t_located and self.located(p) and not self._may_overlap(p, t):
                result = "outside"
        self._relations[key] = result
        return result


    def kecamatan(self, place):
        """The Surabaya kecamatan a place lies in (its own hierarchy first, then the verified kelurahan/estate data), or
        None. v4.8: "Medayu" sits under Medokan in the hand-curated tree; the verified data puts Medokan Ayu in Rungkut."""
        for p in (self.canonical(place), *self.ancestors(place)):
            seen = set()
            while p and p not in seen and len(seen) < 8:
                if p in SURABAYA_KECAMATAN:
                    return p
                seen.add(p)
                p = _VERIFIED.get(p)
        return None

    def far_apart(self, place, others):
        """v4.8: ``place`` lies in a Surabaya kecamatan that neither holds nor borders the kecamatan of any of ``others``
        (all of them placed in a kecamatan); False when any kecamatan is unknown."""
        own = self.kecamatan(place)
        wanted = {self.kecamatan(o) for o in others}
        if not own or not wanted or None in wanted:
            return False
        return not any(own == k or own in SURABAYA_KECAMATAN_NEIGHBORS.get(k, ()) for k in wanted)


_VERIFIED = {**SURABAYA_KECAMATAN, **KECAMATAN, **KELURAHAN, **ESTATES}


# v4.8: "Manyar2an", "Manyar²an" (NFKC: "Manyar2an"), "manyar-an", "Manyaran": a known place with the suffix "-an" means
# that place and the area around it ("Manyar dan sekitarnya"). The plain "-an" form only when the whole word names no place
# itself ("Gayungan", "Pandaan", "Krembangan" are places).
_SUFFIXED = re.compile(r"(?<![\w-])((?:[^\W\d_]+[ \t]){0,2}?)([^\W\d_²]{3,})(?:([ \t]?[2²][ \t]?|-)|(?=an\b))an(?!\w)")
_SUFFIX_STEMS_OFF = frozenset({"jal", "tam", "kal", "bul", "kem", "jual", "jalan", "taman", "ban", "lap", "pas"})


def expand_place_suffixes(text, places=None):
    """``text`` with "<place>2an" / "<place>-an" / "<place>an" written as "<place> dsk"."""
    if not text or "an" not in text.lower():
        return text
    places = places or default_places()

    def replace(m):
        words, stem, joined = m.group(1) or "", m.group(2), m.group(3)
        whole = normalize_place(m.group(0))
        if not joined and (len(stem) < 5 or whole in places.known or stem.lower() in _SUFFIX_STEMS_OFF):
            return m.group(0)
        prefix = words.split()
        for n in range(len(prefix), -1, -1):
            name = normalize_place(" ".join(prefix[len(prefix) - n:] + [stem]))
            if name in places.known and places.recognize(name) == [places.canonical(name)]:
                kept = " ".join(prefix[:len(prefix) - n])
                return (kept + " " if kept else "") + " ".join(prefix[len(prefix) - n:] + [stem]) + " dsk"
        return m.group(0)

    return _SUFFIXED.sub(replace, text)


@lru_cache(maxsize=1)
def default_places():
    return Places()
