"""Imported, direct cluster distances. No geocoding or inferred paths."""
import csv
import io
import math
import re
import unicodedata
from collections import defaultdict

from locations import DEFAULT_GLOSSARY, GENERIC_PLACE_WORDS, ROOT_PLACES, Places, default_places, glossary_entries, normalize_place


def key(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return re.sub(r'[^\w]+', ' ', value).strip()


def parse_table(text, existing=None):
    text = text.lstrip('﻿').strip()
    if not text:
        raise ValueError('The table is empty.')
    delimiter = '\t' if '\t' in text.splitlines()[0] else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = reader.fieldnames or []
    nearby = next((h for h in headers if h and h.startswith('Cluster Terdekat')), None)
    if not {'Cluster', 'Alias', 'Area/Development'}.issubset(headers) or not nearby:
        raise ValueError('Use the columns Cluster, Alias, Area/Development and Cluster Terdekat (dalam radius 4 km).')
    existing = existing or {'clusters': [], 'edges': []}
    clusters = {c['id']: {**c, 'aliases': list(c['aliases'])} for c in existing['clusters']}
    seen, links = set(), []
    for line, row in enumerate(reader, 2):
        if None in row:
            raise ValueError(f'Row {line}: too many columns. Quote cells that contain commas.')
        if not any(row.values()):
            continue
        name = (row.get('Cluster') or '').strip()
        ident = key(name)
        if not ident or len(name) > 200 or ident in seen:
            raise ValueError(f'Row {line}: cluster name is empty, too long or duplicated.')
        seen.add(ident)
        aliases = [s.strip() for s in (row.get('Alias') or '').split(';') if s.strip()]
        area = (row.get('Area/Development') or '').strip()
        if ident in clusters:
            old = clusters[ident]
            if area and old['area'] and key(area) != key(old['area']):
                raise ValueError(f'Row {line}: area differs from the saved cluster: {name}')
            old['aliases'] = list({key(a): a for a in [*old['aliases'], *aliases]}.values())
            old['area'] = old['area'] or area
        else:
            clusters[ident] = {'id': ident, 'name': name, 'aliases': aliases, 'area': area}
        for part in (row.get(nearby) or '').split(';'):
            if not part.strip():
                continue
            match = re.fullmatch(r'\s*(.+)\s+\((\d+(?:[.,]\d+)?)\)\s*', part)
            if not match:
                raise ValueError(f'Row {line}: invalid distance: {part[:100]}')
            distance = float(match[2].replace(',', '.'))
            if not math.isfinite(distance) or not 0 <= distance <= 4:
                raise ValueError(f'Row {line}: distance must be 0–4 km.')
            links.append((ident, key(match[1]), distance, line))
    if not clusters or len(clusters) > 5000:
        raise ValueError('Provide 1–5,000 clusters.')
    if len(links) > 100000:
        raise ValueError('At most 100,000 location links are allowed.')
    edges = {tuple(sorted((e['a'], e['b']))): e['distance_km'] for e in existing['edges']}
    for a, b, distance, line in links:
        if b not in clusters:
            raise ValueError(f'Row {line}: target cluster is not in the table: {b}')
        if a == b:
            raise ValueError(f'Row {line}: a cluster does not need a link to itself.')
        pair = tuple(sorted((a, b)))
        if pair in edges and edges[pair] != distance:
            raise ValueError(f'Row {line}: distance differs from another/saved record for {clusters[a]["name"]} and {clusters[b]["name"]}.')
        edges[pair] = distance
    # Explicit aliases must identify one cluster. Optional abbreviated base names
    # are added by the resolver only when unambiguous.
    terms = {}
    for ident, cluster in clusters.items():
        for alias in [cluster['name'], *cluster['aliases']]:
            term = key(alias)
            if not term or len(alias) > 200 or (term in terms and terms[term] != ident):
                raise ValueError(f'Alias is not unique or not valid: {alias}')
            terms[term] = ident
    return {'clusters': list(clusters.values()), 'edges': [{'a': a, 'b': b, 'distance_km': d} for (a, b), d in sorted(edges.items())]}


# Words that describe a kind of housing estate rather than name it.
_GENERIC_PREFIX = re.compile(r'^(?:(?:perumahan|perum|perumnas|housing|kpr|cluster|the|de|new|mini)\s+)+')
_GENERIC_SUFFIX = re.compile(r'(?:\s+(?:cluster|perumahan|housing))+$')
# Two-word remainders that are too ordinary to identify an estate on their own.
_COMMON_BASES = {'grand city', 'green hill', 'green view', 'garden city', 'royal garden', 'permata indah', 'the mansion',
                 'city garden', 'green village', 'bumi permai', 'griya asri', 'taman sari', 'pondok indah', 'villa indah'}
_STREET_BEFORE = re.compile(r'(?:\bjl|\bjln|\bjalan|\braya|\bgang|\bgg|\bstreet|\bst)\s+$')
_LANDMARK_BEFORE = re.compile(r'\b(?:dekat|dkt|deket|dket|near|menuju|dari|sekitar|ke|arah|seberang|samping|sebelah|depan|belakang)\s+(?:\w+\s+){0,2}$')
_STRICT_TEXT = re.compile(r'\b(?:(?:hanya|wajib|harus|khusus|only)\s+(?:di|area|lokasi|cluster|kawasan|in)|tidak mau lokasi lain)\b', re.I)
_SPACED = re.compile(r'(?<!\w)(?:\w ){3,}\w(?!\w)')


_RESPELL = {key(k): key(v) for k, v in DEFAULT_GLOSSARY.items() if key(k) and key(v)}
_RESPELL_RE = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(k) for k in sorted(_RESPELL, key=len, reverse=True)) + r')(?!\w)')


def _respell(value):
    """Built-in spelling variants folded the same way on both sides ("citra land" = "citraland")."""
    return _RESPELL_RE.sub(lambda m: _RESPELL[m.group()], value)


def _camel_split(value):
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', value)


def _distance(a, b, limit=2):
    """Levenshtein distance, cut off above ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row[0] = row[0], i
        best = row[0]
        for j, cb in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
            best = min(best, row[j])
        if best > limit:
            return limit + 1
    return row[-1]


# Words of a cluster name that never identify it on their own.
_NAME_FILLER = {'cluster', 'perumahan', 'perum', 'residence', 'residences', 'garden', 'gardens', 'village', 'regency', 'park',
                'city', 'indah', 'permai', 'raya', 'baru', 'asri', 'golf', 'hill', 'hills', 'surabaya', 'housing', 'estate', 'the'}


def _variants(name):
    """Name variants in priority order: (level, term). Level 0 explicit, 1 base, 2 shortened."""
    out = []
    base = re.sub(r'\s*\([^)]*\)', '', name).strip()
    for level, value in ((0, name), (1, base), (1, _camel_split(name)), (1, _camel_split(base))):
        term = key(value)
        if term:
            out.append((level, term))
    stripped = _GENERIC_SUFFIX.sub('', _GENERIC_PREFIX.sub('', key(_camel_split(base))))
    words = stripped.split()
    if stripped and stripped != key(base) and len(words) >= 2 and stripped not in _COMMON_BASES and len(stripped) >= 7:
        out.append((2, stripped))
    return out


class LocationIndex:
    def __init__(self, data=None, glossary=None):
        data = data or {'clusters': [], 'edges': []}
        entries = glossary_entries(glossary)
        self.places = Places(entries) if any(e['kind'] == 'location' for e in entries) else default_places()
        self.clusters = {c['id']: c for c in data['clusters']}
        self.neighbors = defaultdict(dict)
        for edge in data['edges']:
            self.neighbors[edge['a']][edge['b']] = edge['distance_km']
            self.neighbors[edge['b']][edge['a']] = edge['distance_km']
        # Areas/developments: a name that expands to every cluster of that development.
        self.area_of, self.area_clusters, self.area_names = {}, defaultdict(set), {}
        self._name_regions = {}
        for ident, c in self.clusters.items():
            area = key(c.get('area') or '')
            if area:
                self.area_names.setdefault(area, c['area'])
                # A development name shared by estates in other cities ("Citra Land Puncak Tidar
                # Malang" filed under CitraLand) does not make that estate part of the Surabaya one.
                area_region, own = self.places.region_of(area), self._own_regions(ident)
                if area_region and own and area_region not in own:
                    continue
                self.area_of[ident] = area
                self.area_clusters[area].add(ident)
        candidates = defaultdict(lambda: defaultdict(set))
        for ident, c in self.clusters.items():
            for level, term in _variants(c['name']):
                # Shortened names only for estates inside a known development: elsewhere they
                # collide with street names and same-named estates in other cities.
                if level < 2 or ident in self.area_of:
                    candidates[term][level].add(ident)
                    candidates[_respell(term)][level].add(ident)
            for alias in c['aliases']:
                candidates[key(alias)][0].add(ident)
                candidates[_respell(key(alias))][0].add(ident)
        terms, self.term_level = {}, {}
        for term, levels in candidates.items():
            level = min(levels)
            if len(levels[level]) == 1:
                terms[term] = next(iter(levels[level]))
                self.term_level[term] = level
        area_terms = {}
        for area, name in self.area_names.items():
            if area not in self.area_clusters:
                continue  # every estate filed under it is elsewhere
            for level, term in {(lv, t) for lv, base in _variants(name) for t in (base, _respell(base))}:
                # A development name means the whole development, unless it is one cluster's own alias.
                if term in terms and self.term_level.get(term) == 0 and terms[term] in self.clusters \
                        and key(self.clusters[terms[term]]['name']) != term:
                    continue
                area_terms.setdefault(term, area)
        # Glossary entries (built-in synonyms, then company entries) extend the resolver:
        # a term whose canonical is a cluster or development resolves to it.
        builtin = [{'term': k, 'canonical': v, 'kind': 'synonym'} for k, v in DEFAULT_GLOSSARY.items()]
        for e in entries + builtin:
            if e['kind'] == 'category':
                continue
            term, canonical = _respell(key(e['term'])), _respell(key(e['canonical']))
            if not term or term in terms or term in area_terms:
                continue
            if canonical in area_terms:
                area_terms[term] = area_terms[canonical]
            elif canonical in terms:
                terms[term] = terms[canonical]
                self.term_level[term] = 0
            elif canonical in self.area_clusters:
                area_terms[term] = canonical
        self.terms, self.area_terms = terms, area_terms
        # Distinctive single words of a development's cluster names ("palazzate"): a misspelling written right after
        # the development's name ("Pakuwon Indah Pallazate") still names that cluster.
        words = defaultdict(set)
        for ident in self.area_of:
            for word in key(re.sub(r'\s*\([^)]*\)', '', self.clusters[ident]['name'])).split():
                if len(word) >= 6 and word not in _NAME_FILLER and not word.isdigit():
                    words[word].add(ident)
        area_words = set(' '.join(self.area_names).split())
        self.cluster_words = {w: next(iter(ids)) for w, ids in words.items()
                              if len(ids) == 1 and w not in area_words and w not in self.places.known}
        names = sorted(set(terms) | set(area_terms), key=len, reverse=True)
        self.pattern = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(t) for t in names) + r')(?!\w)') if names else None
        self._resolved = {}
        self._members = {}
        self._scopes = {}

    # ------------------------------------------------------------------ regions
    def _own_regions(self, ident):
        """Regions of the specific places a cluster's own name/aliases contain (cities alone do not count:
        "Kokoh City Cluster Bali" is not in Bali)."""
        if ident not in self._name_regions:
            c = self.clusters[ident]
            names = [c['name'], *c.get('aliases', [])]
            places = {p for n in names for p in self.places.recognize(_respell(key(n))) if p not in ROOT_PLACES}
            self._name_regions[ident] = frozenset(r for r in (self.places.region_of(p) for p in places) if r)
        return self._name_regions[ident]

    def cluster_region(self, ident):
        """Surabaya region/city of a cluster: its development's, else its own name's (None when unknown)."""
        if ident in self.area_of:
            region = self.places.region_of(self.area_of[ident])
            if region:
                return region
        own = self._own_regions(ident)
        return next(iter(own)) if len(own) == 1 else None

    def target_region(self, kind, target):
        if kind == 'cluster':
            return self.cluster_region(target)
        if kind == 'area':
            region = self.places.region_of(target)
            if region:
                return region
            regions = {self.cluster_region(i) for i in self.area_members(target)}
            return next(iter(regions)) if len(regions) == 1 else None
        return None

    # ------------------------------------------------------------------ areas
    def area_chain(self, area):
        return (area, *self.places.ancestors(area))

    def area_members(self, area):
        """Clusters of a development, including developments registered as its children."""
        members = self._members.get(area)
        if members is None:
            members = set(self.area_clusters.get(area, ()))
            for other, ids in self.area_clusters.items():
                if other != area and area in self.places.ancestors(other):
                    members |= ids
            self._members[area] = members = frozenset(members)
        return members

    def scope(self, ids):
        """('cluster', id) | ('area', area) | ('mixed', None) | ('none', None) for a resolved listing."""
        if not ids:
            return 'none', None
        if len(ids) == 1:
            return 'cluster', ids[0]
        cache_key = tuple(ids)
        if cache_key not in self._scopes:
            if len(self._scopes) > 50000:
                self._scopes.clear()
            self._scopes[cache_key] = self._scope(ids)
        return self._scopes[cache_key]

    def _scope(self, ids):
        chains = [self.area_chain(self.area_of[i]) if i in self.area_of else None for i in ids]
        if all(chains):
            common = next((a for a in chains[0] if all(a in chain for chain in chains[1:])), None)
            if common and self.area_members(common):
                return 'area', common
        return 'mixed', None

    # ------------------------------------------------------------------ resolution
    def _term_region(self, term):
        if term in self.area_terms:
            return self.target_region('area', self.area_terms[term])
        return self.cluster_region(self.terms[term])

    def _matches(self, value, listing):
        found = []
        spans = None
        for m in self.pattern.finditer(value):
            before = value[max(0, m.start() - 40):m.start()]
            # A nearby landmark is not evidence that the property is IN it.
            if listing and _LANDMARK_BEFORE.search(before):
                continue
            term = m.group()
            if self.term_level.get(term) == 2 and _STREET_BEFORE.search(before):
                continue  # "Jl. Imam Bonjol" is a street, not "Cluster Imam Bonjol"
            if spans is None:
                spans = [(p.start(), p.end(), p.group()) for p in self.places.pattern.finditer(value)]
            # "Citraland Driyorejo (Gresik)" is a satellite elsewhere, not the CitraLand development.
            longer = [name for start, end, name in spans
                      if start <= m.start() and end >= m.end() and end - start > m.end() - m.start()]
            if longer:
                region = self._term_region(term)
                if region and any(self.places.region_of(name) not in (None, region) for name in longer):
                    continue
            found.append(term)
        return found

    def _fuzzy_clusters(self, value, terms):
        """Clusters named by a misspelt word right after their development's name ("pakuwon indah pallazate")."""
        out = []
        for m in self.pattern.finditer(value):
            area = self.area_terms.get(m.group())
            if area is None:
                continue
            following = value[m.end():].split()[:2]
            if following and following[0] in ('cluster', 'klaster'):
                following = following[1:]
            word = following[0] if following else ''
            if len(word) < 6 or word in self.terms or word in self.area_terms:
                continue
            members = self.area_members(area)
            close = {ident for w, ident in self.cluster_words.items()
                     if ident in members and w[0] == word[0] and _distance(w, word) <= 2}
            if len(close) == 1:
                out.append(close.pop())
        return out

    def area_followers(self, text):
        """[(area, the two words after its name)] for every development name in ``text``; a list separator
        ("Royal Residence–Lidah Wetan", "Pakuwon Indah | Graha Family") ends the words that follow it."""
        if not self.pattern:
            return []
        out = []
        for segment in re.split(r'[,;/|()\[\]\n–—]|\s-\s|\s+(?:atau|dan|or|and|&)\s+', text.lower()):
            value = _respell(_SPACED.sub(lambda m: m.group().replace(' ', ''), key(segment)))
            out += [(self.area_terms[m.group()], value[m.end():].split()[:2])
                    for m in self.pattern.finditer(value) if m.group() in self.area_terms]
        return out

    def _ids(self, terms, extra=()):
        clusters, areas = list(extra), []
        for term in terms:
            # A development name wins over a cluster sharing it ("Kokoh City").
            if term in self.area_terms:
                areas.append(self.area_terms[term])
            else:
                clusters.append(self.terms[term])
        # A named cluster is more specific than the development it belongs to.
        specific_areas = {a for c in clusters if c in self.area_of for a in self.area_chain(self.area_of[c])}
        out = list(clusters)
        for area in areas:
            if area not in specific_areas:
                out += sorted(self.area_members(area))
        return list(dict.fromkeys(out))

    def resolve(self, text, listing=False):
        if not self.pattern:
            return []
        cache_key = (text, listing) if len(text) <= 200 else None  # short phrases (strict places) repeat
        if cache_key in self._resolved:
            return list(self._resolved[cache_key])
        lines = [line for line in text.splitlines() if line.strip()]
        value = _respell(_SPACED.sub(lambda m: m.group().replace(' ', ''), key(text)))
        terms = self._matches(value, listing)
        if listing and terms and len(lines) > 3:
            # Prefer the title: later lines often describe surroundings or other listings.
            title = _respell(_SPACED.sub(lambda m: m.group().replace(' ', ''), key('\n'.join(lines[:3]))))
            title_terms = self._matches(title, listing)
            if title_terms:
                title_ids = self._ids(title_terms)
                title_areas = {a for i in title_ids if i in self.area_of for a in self.area_chain(self.area_of[i])}
                extra = [t for t in terms if t not in title_terms and t in self.terms and t not in self.area_terms
                         and self.area_of.get(self.terms[t]) in title_areas]
                terms = title_terms + extra
        result = self._ids(terms, self._fuzzy_clusters(value, terms) if self.cluster_words else ())
        if cache_key is not None:
            if len(self._resolved) > 20000:
                self._resolved.clear()
            self._resolved[cache_key] = tuple(result)
        return result

    def describe(self, ids):
        kind, value = self.scope(ids)
        if kind == 'cluster':
            return self.clusters[value]['name']
        if kind == 'area':
            return self.area_label(value)
        return None

    # ------------------------------------------------------------------ comparison
    def area_label(self, area):
        return self.area_names.get(area) or ' '.join(w if w.isupper() else w.capitalize() for w in str(area).split())

    def compare(self, request, listing, request_ids=None, listing_ids=None, strict=None, listing_regions=None, radius_km=4):
        """``listing_regions``: regions of the places the listing itself names; a resolved cluster in
        another region is not evidence (the parsed places and the index must agree). ``radius_km``: nearby
        alternatives only within this distance (0 = the same cluster/area only)."""
        radius_km = min(4.0, max(0.0, float(radius_km if radius_km is not None else 4)))
        buyers = request_ids if request_ids is not None else self.resolve(request)
        properties = listing_ids if listing_ids is not None else self.resolve(listing, listing=True)
        if not buyers:
            return {'kind': 'unindexed'}
        if strict is None:
            strict = bool(_STRICT_TEXT.search(request or ''))
        buyer_set = set(buyers)
        buyer_kind, buyer_area = self.scope(list(buyers))
        kind, target = self.scope(list(properties))
        if kind == 'none':
            return {'kind': 'unknown', 'reason': 'Exact location not recognised'}
        if kind == 'mixed':
            return {'kind': 'unknown', 'reason': 'Listing names several places; exact cluster not confirmed'}
        region = self.target_region(kind, target) if listing_regions else None
        if region and region not in listing_regions:
            return {'kind': 'unknown', 'reason': 'Listing names a place in another area; exact location not confirmed'}
        if kind == 'cluster':
            name = self.clusters[target]['name']
            if target in buyer_set:
                if buyer_kind == 'area' and len(buyer_set) > 1:
                    return {'kind': 'exact', 'score': 100.0, 'reason': f'Inside {self.area_label(buyer_area)}: {name}'}
                return {'kind': 'exact', 'score': 100.0, 'reason': 'Same cluster: ' + name}
            if strict:
                return {'kind': 'excluded'}
            distances = [(self.neighbors[b][target], b) for b in buyers
                         if target in self.neighbors[b] and self.neighbors[b][target] <= radius_km]
            if not distances:
                # Missing edges mean unknown, not proven >4 km. Do not offer an
                # unverified alternative under a bounded-radius policy.
                return {'kind': 'excluded'}
            distance, source = min(distances)
            return {'kind': 'nearby', 'score': 100 - distance * 10, 'distance_km': distance,
                    'source': self.clusters[source]['name'], 'target': name,
                    'reason': f'Nearby alternative: {self.clusters[source]["name"]} → {name} · {distance:g} km'}
        members = self.area_members(target)
        area_name = self.area_label(target)
        if members <= buyer_set:
            return {'kind': 'exact', 'score': 100.0, 'reason': 'Same area: ' + area_name, 'area': target}
        if buyer_set & members:
            return {'kind': 'unknown', 'area': target, 'reason': f'Same development ({area_name}); exact cluster not confirmed'}
        if strict:
            return {'kind': 'excluded'}
        near = sorted((self.neighbors[b][m], b) for b in buyers for m in members
                      if m in self.neighbors[b] and self.neighbors[b][m] <= radius_km)
        if near:
            return {'kind': 'unknown', 'area': target,
                    'reason': f'Listing area {area_name} is near {self.clusters[near[0][1]]["name"]}; exact cluster not confirmed'}
        return {'kind': 'excluded'}

    # ------------------------------------------------------------------ strict buyers
    def strict_ids(self, place):
        return set(self.resolve(place)) if self.pattern else set()

    def cluster_places(self, ident):
        c = self.clusters[ident]
        names = [key(re.sub(r'\s*\([^)]*\)', '', c['name'])), key(c['name']), *(key(a) for a in c['aliases'])]
        if ident in self.area_of:
            names.append(self.area_of[ident])
        return [normalize_place(n) for n in names if n]

    def strict_verdict(self, targets, places, ids, place_text=''):
        """'inside' | 'outside' | 'unknown' for a listing against places a buyer insists on.

        ``places``: listing place names (parser + title); ``ids``: resolved clusters;
        ``place_text``: listing text without landmark phrases, for literal evidence.
        """
        verdicts = []
        for target in targets:
            target = self.places.canonical(target)
            target_ids = self.strict_ids(target)
            verdict = None
            for place in places:
                relation = self.places.relation(place, target)
                if relation == 'inside':
                    verdict = 'inside'
                    break
                if relation == 'outside':
                    verdict = verdict or 'outside'
            # Literal evidence only for places outside the hierarchy: a known place is already
            # recognised in the listing ("darmo" inside "darmo permai" is a different place).
            # A generic word ("hill", "central") is never evidence on its own.
            if verdict != 'inside' and place_text and target not in self.places.known \
                    and not set(target.split()) <= GENERIC_PLACE_WORDS \
                    and re.search(r'(?<!\w)' + re.escape(target) + r'(?!\w)', place_text):
                verdict = 'inside'
            if verdict != 'inside' and ids:
                # Several resolved clusters (a development or a multi-listing message): the exact
                # cluster is unknown, so all of them must agree before anything is proven.
                cluster_verdicts = []
                for ident in ids:
                    if target_ids:
                        cluster_verdicts.append('inside' if ident in target_ids else 'outside')
                        continue
                    relations = [self.places.relation(p, target) for p in self.cluster_places(ident)]
                    cluster_verdicts.append('inside' if 'inside' in relations else 'outside' if 'outside' in relations else None)
                if all(v == 'inside' for v in cluster_verdicts):
                    # The places the listing names and the index must agree: a conflict proves nothing.
                    verdict = 'unknown' if verdict == 'outside' else 'inside'
                elif all(v == 'outside' for v in cluster_verdicts):
                    verdict = verdict or 'outside'
            verdicts.append(verdict)
        if 'inside' in verdicts:
            return 'inside'
        if verdicts and all(v == 'outside' for v in verdicts):
            return 'outside'
        return 'unknown'


_EMPTY = None


def empty_index():
    global _EMPTY
    if _EMPTY is None:
        _EMPTY = LocationIndex()
    return _EMPTY


def load_glossary(conn, company_id):
    return [dict(r) for r in conn.execute(
        'SELECT alias, canonical, kind, parent FROM xm.glossary WHERE company_id=%s', (company_id,)).fetchall()]


def load_index(conn, company_id='xm'):
    row = conn.execute('SELECT data FROM xm.location_indexes WHERE company_id=%s', (company_id,)).fetchone()
    return LocationIndex(row['data'] if row else None, glossary=load_glossary(conn, company_id))
