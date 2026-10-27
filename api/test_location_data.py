"""Verified place data (location_data) merged into the built-in hierarchy (locations)."""
import time
import unittest

import location_data as D
from locations import (DEFAULT_LOCATION_ALIASES, DEFAULT_LOCATION_PARENTS, KNOWN_LOCATIONS, PREFIX_EXCEPTIONS,
                       ROOT_PLACES, STRICT_ONLY_PLACES, SURABAYA_REGIONS, Places, _UNSAFE_PLACES, default_places,
                       normalize_place)
from matching_rules import assess_pair
from parser import parse_message

SETTINGS = dict(land_tolerance_pct=10, building_tolerance_pct=20, price_tolerance_pct=10, location_weight_pct=35,
                land_weight_pct=20, building_weight_pct=15, price_weight_pct=20, semantic_weight_pct=5,
                data_quality_weight_pct=5)
DATA_DICTS = {'SURABAYA_KECAMATAN': D.SURABAYA_KECAMATAN, 'KECAMATAN': D.KECAMATAN, 'KELURAHAN': D.KELURAHAN,
              'ESTATES': D.ESTATES, 'ESTATE_ALIASES': D.ESTATE_ALIASES, 'AREA_ALIASES': D.AREA_ALIASES,
              'HAND_TO_ALIAS': D.HAND_TO_ALIAS, 'NESTED': D.NESTED}
NAME_SETS = {'PREFIX_EXCEPTIONS': D.PREFIX_EXCEPTIONS, 'UNSAFE_NAMES': D.UNSAFE_NAMES,
             'STRICT_ONLY_NAMES': D.STRICT_ONLY_NAMES, 'LEFT_OUT_NAMES': D.LEFT_OUT_NAMES}


def row(text):
    value = parse_message(text).dict()
    value['document_type'] = value.pop('classification')
    value['extraction_confidence'] = value.pop('confidence')
    return value


def match(buyer, listing):
    return assess_pair(row(buyer), row(listing), SETTINGS, 50.0)


class DataIntegrity(unittest.TestCase):
    def setUp(self):
        self.places = default_places()
        self.known = self.places.known

    def test_names_are_normalised_and_sorted(self):
        for label, data in DATA_DICTS.items():
            self.assertEqual(list(data), sorted(data), label)
            for key, value in data.items():
                self.assertEqual(normalize_place(key), key, (label, key))
                self.assertEqual(normalize_place(value), value, (label, key, value))
        for label, names in NAME_SETS.items():
            for name in names:
                self.assertEqual(normalize_place(name), name, (label, name))

    def test_kecamatan_follow_the_region_convention(self):
        self.assertEqual(len(D.SURABAYA_KECAMATAN), 31)
        regions = {}
        for kecamatan, region in D.SURABAYA_KECAMATAN.items():
            regions.setdefault(region, set()).add(kecamatan)
        self.assertEqual(regions['surabaya barat'], {'benowo', 'pakal', 'asemrowo', 'sukomanunggal', 'tandes', 'sambikerep',
                                                     'lakarsantri', 'wiyung', 'dukuh pakis'})
        self.assertEqual(regions['surabaya timur'], {'gubeng', 'gunung anyar', 'sukolilo', 'tambaksari', 'mulyorejo',
                                                     'rungkut', 'tenggilis mejoyo'})
        self.assertEqual(regions['surabaya selatan'], {'jambangan', 'gayungan', 'wonocolo', 'wonokromo', 'karang pilang',
                                                       'sawahan'})
        self.assertEqual(regions['surabaya utara'], {'bulak', 'kenjeran', 'semampir', 'pabean cantian', 'krembangan'})
        self.assertEqual(regions['surabaya pusat'], {'tegalsari', 'simokerto', 'genteng', 'bubutan'})
        self.assertEqual(sorted(set(D.KECAMATAN.values())), ['gresik', 'sidoarjo'])
        self.assertEqual(sum(1 for v in D.KECAMATAN.values() if v == 'gresik'), 18)
        self.assertEqual(sum(1 for v in D.KECAMATAN.values() if v == 'sidoarjo'), 18)
        for kecamatan, parent in {**D.SURABAYA_KECAMATAN, **D.KECAMATAN}.items():
            self.assertEqual(DEFAULT_LOCATION_PARENTS[kecamatan], parent, kecamatan)

    def test_data_dicts_do_not_overlap(self):
        groups = [set(D.SURABAYA_KECAMATAN), set(D.KECAMATAN), set(D.KELURAHAN), set(D.ESTATES)]
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                self.assertEqual(a & b, set())
        places = set().union(*groups)
        self.assertEqual(set(D.ESTATE_ALIASES) & places, set())
        self.assertEqual(set(D.AREA_ALIASES) & places, set())
        districts = set(D.SURABAYA_KECAMATAN) | set(D.KECAMATAN) | set(SURABAYA_REGIONS) | ROOT_PLACES
        for kelurahan, kecamatan in D.KELURAHAN.items():
            self.assertIn(kecamatan, districts, kelurahan)

    def test_hierarchy_has_no_cycles_and_every_chain_ends_in_a_city(self):
        for place, parent in DEFAULT_LOCATION_PARENTS.items():
            seen, current = {place}, place
            while current in DEFAULT_LOCATION_PARENTS:
                current = DEFAULT_LOCATION_PARENTS[current]
                self.assertNotIn(current, seen, f'cycle at {place}')
                seen.add(current)
            self.assertIn(current, ROOT_PLACES, place)
            self.assertTrue(self.places.located(place), place)

    def test_aliases_point_to_known_places(self):
        for alias in DEFAULT_LOCATION_ALIASES:
            canonical = self.places.canonical(alias)
            self.assertNotIn(canonical, DEFAULT_LOCATION_ALIASES, alias)
            self.assertTrue(canonical in DEFAULT_LOCATION_PARENTS or canonical in ROOT_PLACES or canonical in KNOWN_LOCATIONS,
                            (alias, canonical))
        for alias, canonical in {**D.ESTATE_ALIASES, **D.AREA_ALIASES, **D.HAND_TO_ALIAS}.items():
            self.assertTrue(self.places.located(alias) or canonical in KNOWN_LOCATIONS, alias)

    def test_names_shared_by_two_areas_are_unsafe_unless_kept(self):
        for name, areas in D.CROSS_AREA_NAMES.items():
            self.assertGreater(len(areas), 1, name)
            self.assertTrue(name in _UNSAFE_PLACES or name in D.KEPT_DUPLICATES, name)
        for name in D.LEFT_OUT_NAMES:
            self.assertNotIn(name, DEFAULT_LOCATION_PARENTS)
            self.assertNotIn(name, DEFAULT_LOCATION_ALIASES)
        # Spellings that only differ by spaces name one place, or the other one is never recognised.
        squashed = {}
        for name in DEFAULT_LOCATION_PARENTS:
            squashed.setdefault(name.replace(' ', ''), set()).add(name)
        for names in squashed.values():
            regions = {self.places.region_of(n) for n in names if n not in _UNSAFE_PLACES}
            self.assertLessEqual(len(regions), 1, names)

    def test_unsafe_and_strict_names_are_in_the_hierarchy(self):
        self.assertEqual(D.STRICT_ONLY_NAMES - D.UNSAFE_NAMES, set())
        self.assertEqual(D.UNSAFE_NAMES & set(KNOWN_LOCATIONS), set())
        for name in D.UNSAFE_NAMES:
            self.assertIn(name, self.known, name)
        self.assertTrue(set(D.STRICT_ONLY_NAMES) <= set(STRICT_ONLY_PLACES))

    def test_prefix_exceptions_and_nesting_are_needed(self):
        for name in D.PREFIX_EXCEPTIONS:
            self.assertIn(name, self.known, name)
            prefixes = [' '.join(name.split()[:i]) for i in range(1, len(name.split()))]
            outside = [p for p in prefixes if p in self.known and self.places.relation(name, p) != 'inside']
            self.assertTrue(outside, f'{name} needs no prefix exception')
        for child, parent in D.NESTED.items():
            self.assertEqual(self.places.relation(child, parent), 'inside', child)
        self.assertTrue(D.PREFIX_EXCEPTIONS <= PREFIX_EXCEPTIONS)


class KeyPlaces(unittest.TestCase):
    def setUp(self):
        self.places = default_places()

    def chain(self, place):
        return [self.places.canonical(place), *self.places.ancestors(place)]

    def test_citraland_cluster_is_surabaya_barat(self):
        for cluster in ('woodland', 'stonegate park', 'emerald mansion', 'bukit palma', 'pelican hill', 'raffles garden'):
            self.assertIn('citraland', self.chain(cluster), cluster)
            self.assertEqual(self.places.region_of(cluster), 'surabaya barat', cluster)

    def test_pakuwon_city_cluster_is_surabaya_timur(self):
        for cluster in ('suvadiva', 'grand island', 'casa tobago', 'san antonio', 'east coast residence'):
            self.assertIn('pakuwon city', self.chain(cluster), cluster)
            self.assertEqual(self.places.region_of(cluster), 'surabaya timur', cluster)

    def test_pondok_tjandra_is_in_waru_sidoarjo(self):
        self.assertEqual(self.places.ancestors('pondok tjandra'), ('waru', 'sidoarjo'))
        self.assertEqual(self.places.ancestors('pondok tjandra indah'), ('waru', 'sidoarjo'))
        self.assertEqual(self.places.ancestors('opal residence'), ('pondok tjandra', 'waru', 'sidoarjo'))

    def test_gresik_and_sidoarjo_estates(self):
        self.assertEqual(self.places.ancestors('puri safira regency'), ('menganti', 'gresik'))
        self.assertEqual(self.places.region_of('garbera'), 'gresik')
        self.assertEqual(self.places.region_of('kota baru driyorejo'), 'gresik')
        self.assertEqual(self.places.ancestors('taman pinang indah'), ('sidoarjo kota', 'sidoarjo'))
        self.assertEqual(self.places.region_of('delta sari baru'), 'sidoarjo')
        self.assertEqual(self.places.region_of('citra harmoni'), 'sidoarjo')

    def test_names_starting_with_another_area_can_lie_elsewhere(self):
        self.assertEqual(self.places.region_of('kenjeran indah'), 'surabaya timur')   # Mulyorejo, not Kec. Kenjeran
        self.assertEqual(self.places.region_of('darmo permai selatan'), 'surabaya barat')
        self.assertEqual(self.places.relation('citraland vittorio', 'citraland'), 'outside')  # Wiyung, outside the township
        self.assertEqual(self.places.relation('ketintang baru', 'ketintang'), 'inside')

    def test_documented_overrides_of_the_hand_curated_tree(self):
        self.assertEqual(self.places.region_of('ngagel'), 'surabaya selatan')       # Kel. Ngagel, Wonokromo
        self.assertEqual(self.places.region_of('kendangsari'), 'surabaya timur')    # Kel. Kendangsari, Tenggilis Mejoyo
        self.assertEqual(self.places.canonical('araya'), 'galaxy bumi permai')
        self.assertEqual(self.places.relation('araya', 'galaxy bumi permai'), 'inside')

    def test_places_carrying_a_road_or_area_name_lie_inside_it(self):
        pairs = [('taman kendangsari', 'kendangsari'), ('ruko raya jemursari', 'jemursari'), ('rsi jemursari', 'jemursari'),
                 ('puncak dharmahusada', 'dharmahusada'), ('puncak kertajaya', 'kertajaya'), ('kedung baruk', 'baruk'),
                 ('dukuh sutorejo', 'sutorejo'), ('mulia residence kalikepiting', 'kalikepiting'),
                 ('graha gayungsari', 'gayungsari'), ('central park wiguna', 'wiguna'), ('kupang indah', 'kupang')]
        for place, target in pairs:
            self.assertEqual(self.places.relation(place, target), 'inside', place)
            self.assertEqual(self.places.relation(target, place), 'general', place)
        # Same words, other area: Puncak Bukit Golf (Dukuh Pakis) is not CitraLand's Bukit Golf.
        self.assertEqual(self.places.relation('puncak bukit golf', 'bukit golf'), 'outside')
        self.assertEqual(self.places.relation('darmo permai', 'darmo'), 'outside')
        self.assertEqual(self.places.relation('kenjeran indah', 'kenjeran'), 'outside')  # kecamatan: the tree decides

    def test_a_township_spanning_kecamatan_is_not_proven_outside_one(self):
        self.assertIsNone(self.places.relation('pakuwon indah', 'wiyung'))
        self.assertIsNone(self.places.relation('woodland', 'sambikerep'))
        self.assertEqual(self.places.relation('bumi wana lestari', 'sambikerep'), 'inside')
        self.assertEqual(self.places.relation('pakuwon city', 'sukomanunggal'), 'outside')
        self.assertEqual(self.places.relation('wisma tengger', 'lakarsantri'), 'outside')


class NamesMeaningAnotherPlace(unittest.TestCase):
    def setUp(self):
        self.places = default_places()

    def regions(self, text):
        return {self.places.region_of(p) for p in parse_message(text).locations} - {None}

    def test_they_are_left_out_of_the_hierarchy(self):
        self.assertTrue(set(D.OTHER_MEANING) <= D.LEFT_OUT_NAMES)
        for name in D.OTHER_MEANING:
            self.assertFalse(self.places.located(name), name)
            self.assertNotIn(name, _UNSAFE_PLACES)
            self.assertNotIn(name, STRICT_ONLY_PLACES)

    def test_a_location_line_naming_another_town_is_not_placed_here(self):
        self.assertEqual(self.regions('Dijual gudang, lokasi Kertosono'), set())
        self.assertEqual(self.regions('Dijual gudang, akses container, second road, lokasi kertosono\nLT 1,3 ha'), set())
        self.assertEqual(self.regions('Dijual gudang\nlokasi masangan dkt sepanjang\nLT 1000'), set())
        self.assertEqual(self.regions('BUYER REQUEST\nDicari sewa gudang\n• area singosari, malang atau pakis\n• luas 1200 m2'),
                         {'malang'})
        self.assertEqual(self.regions('BUYER REQUEST\nDicari sentul atau bogor\nBudget 2 M'), {'bogor'})

    def test_surabaya_areas_named_like_a_desa_elsewhere(self):
        self.assertEqual(self.places.ancestors('kupang'), ('surabaya',))  # Dukuh Kupang (Dukuh Pakis) to Kupang Krajan (Sawahan)
        self.assertEqual(self.places.canonical('setro'), 'dukuh setro')   # Tambaksari, not desa Setro (Menganti)
        self.assertNotIn('sidoarjo', self.regions('BUYER REQUEST\nCari gudang\nlokasi perak ,kupang,darmo2an\nBudget 5 M'))
        self.assertEqual(self.regions('BUYER REQUEST\n1. rumah lokasi ploso, lebak, bronggalan, kalijudan, setro\nBudget 800 jt'),
                         {'surabaya timur'})


class StrictBuyers(unittest.TestCase):
    BUYER = 'BUYER REQUEST\nRumah Surabaya Barat only\nLT min 120\nBudget 3 M'

    def test_surabaya_barat_buyer_rejects_sidoarjo_estate_and_accepts_citraland_cluster(self):
        self.assertIsNone(match(self.BUYER, 'Dijual Rumah Taman Pinang Indah\nLT 150\nHarga 2,5 M'))
        self.assertIsNone(match(self.BUYER, 'Dijual Rumah Pondok Tjandra Indah\nLT 150\nHarga 2,5 M'))
        self.assertIsNone(match(self.BUYER, 'Dijual Rumah Grand Island Pakuwon City\nLT 150\nHarga 2,5 M'))
        self.assertEqual(match(self.BUYER, 'Dijual Rumah Stonegate Park\nLT 150\nHarga 2,5 M')['status'], 'hot')
        self.assertEqual(match(self.BUYER, 'Dijual Rumah Bukit Palma\nLT 150\nHarga 2,5 M')['status'], 'hot')

    def test_strict_road_buyer_accepts_an_estate_named_after_the_road(self):
        buyer = 'BUYER REQUEST\nSewa ruko Jemursari only\nLT min 80\nBudget 200 jt/thn'
        listing = 'Disewakan ruko raya jemursari (sebelum zoom hotel)\nLT 100\nHarga sewa 150 jt/thn'
        self.assertEqual(row(listing)['locations'], ['ruko raya jemursari'])
        self.assertIsNotNone(match(buyer, listing))

    def test_sidoarjo_buyer_accepts_waru_and_rejects_menganti(self):
        buyer = 'BUYER REQUEST\nRumah Sidoarjo only\nLT min 120\nBudget 3 M'
        self.assertEqual(match(buyer, 'Dijual Rumah Deltasari Indah\nLT 150\nHarga 2,5 M')['status'], 'hot')
        self.assertIsNone(match(buyer, 'Dijual Rumah Greenland Garbera\nLT 150\nHarga 2,5 M'))


class FreeTextRecognition(unittest.TestCase):
    def setUp(self):
        self.places = default_places()

    def test_ordinary_words_are_not_places(self):
        text = ('Dijual rumah ada taman depan, lantai cor beton, serah terima Desember, pinggir jalan raya, kusen jati, '
                'daun pintu solid, posisi ujung, dekat simpang empat, sepanjang jalan ramai, kasur florence')
        self.assertEqual(self.places.recognize(text.lower()), [])
        self.assertEqual(parse_message(text).locations, [])

    def test_unsafe_names_are_never_recognised_alone(self):
        for name in sorted(D.UNSAFE_NAMES):
            found = [normalize_place(m.group()) for m in self.places.pattern.finditer(f'dijual rumah {name} bagus')]
            self.assertNotIn(name, found, name)

    def test_a_longer_unsafe_name_hides_the_names_inside_it(self):
        self.assertEqual(self.places.recognize('dekat trans icon mall'), [])
        self.assertEqual(self.places.recognize('jual / sewa royal plaza surabaya'), ['surabaya'])
        self.assertEqual(self.places.recognize('ke icon mall beach 250 m'), [])  # Icon Bali, Trans Icon Mall or Icon Mall Gresik
        self.assertEqual(self.places.recognize('apartemen icon mall gresik'), ['icon mall gresik'])
        self.assertEqual(self.places.recognize('dekat rsi jemursari'), ['jemursari'])
        self.assertEqual(self.places.recognize('rumah garden hills menganti'), ['menganti'])
        for name in sorted(n for n in _UNSAFE_PLACES if ' ' in n):
            region = self.places.region_of(name)
            for place in self.places.recognize(f'dijual {name} murah'):
                self.assertNotEqual(self.places.relation(name, place), 'outside', (name, place))
                self.assertIn(self.places.region_of(place), (None, region), (name, place))
        company = Places([{'kind': 'location', 'term': 'trans icon mall', 'canonical': 'trans icon mall', 'parent': 'gayungan'}])
        self.assertEqual(company.recognize('dekat trans icon mall'), ['trans icon mall'])

    def test_strict_only_names_count_on_strict_lines(self):
        self.assertEqual(self.places.recognize('lontar', strict=True), ['lontar'])
        self.assertEqual(self.places.recognize('tpi', strict=True), ['taman pondok indah'])
        self.assertEqual(self.places.recognize('lontar'), [])

    def test_hyphen_and_dot_spellings(self):
        self.assertEqual(self.places.recognize('dekat g-walk, jl. dr. soetomo dan hi-tech mall'),
                         ['g walk', 'jl dr soetomo', 'hi tech mall'])

    def test_new_estates_locate_messages(self):
        p = parse_message('Dijual rumah Suvadiva Grand Island\nLT 120\nHarga 3 M')
        self.assertTrue(any(default_places().region_of(x) == 'surabaya timur' for x in p.locations), p.locations)

    def test_company_places_build_fast(self):
        default_places()
        Places([{'kind': 'location', 'term': 'warm up', 'canonical': 'warm up', 'parent': 'wiyung'}])
        start = time.perf_counter()
        company = Places([{'kind': 'location', 'term': 'tka', 'canonical': 'taman kenari asri', 'parent': 'wiyung'}])
        self.assertLess(time.perf_counter() - start, 0.25)
        self.assertEqual(company.recognize('rumah tka dekat citraland'), ['taman kenari asri', 'citraland'])
        self.assertEqual(company.ancestors('tka'), ('wiyung', 'surabaya barat', 'surabaya'))


if __name__ == '__main__':
    unittest.main()
