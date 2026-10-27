"""v5.0: self-service registration reviewed by the platform admin, company activation, admin password resets,
per-member feature grants, service areas per unit category, the activity log and usage per company."""
import unittest
import uuid

from access import DEFAULT_MEMBER_FEATURES, FEATURE_KEYS, allowed, member_features, permissions, required_feature
from harness import ServerTestCase
import harness

if 'activity_log' not in harness.WORKSPACE_TABLES:
    harness.WORKSPACE_TABLES = (*harness.WORKSPACE_TABLES, 'activity_log')


def person(role, features=None):
    return {'id': uuid.UUID(int=1), 'role': role, 'features': features}


class AccessMatrixTests(unittest.TestCase):
    def test_members_need_a_grant_per_feature(self):
        self.assertEqual(required_feature('GET', '/stock/overview'), 'stock')
        self.assertEqual(required_feature('PUT', '/stock/tracked'), 'settings')
        self.assertEqual(required_feature('GET', '/buyers/list'), 'buyers')
        self.assertEqual(required_feature('POST', '/workspace/recommendations'), 'match')
        self.assertEqual(required_feature('POST', '/export/selection'), 'match')
        self.assertEqual(required_feature('GET', '/matches/recent'), 'recent')
        self.assertIsNone(required_feature('GET', '/matches/recent/summary'))
        self.assertIsNone(required_feature('POST', '/matches/recent/seen'))
        self.assertEqual(required_feature('POST', '/entities/status'), 'mark_status')
        self.assertEqual(required_feature('POST', '/imports'), 'upload')
        self.assertEqual(required_feature('PUT', '/settings'), 'settings')
        self.assertIsNone(required_feature('GET', '/settings'))
        self.assertEqual(required_feature('GET', '/team/users'), 'team')
        self.assertEqual(required_feature('GET', '/ai/models'), 'jev')
        self.assertIsNone(required_feature('GET', '/workspace/dates'))
        self.assertIsNone(required_feature('GET', '/company/settings'))

    def test_allowed_follows_the_grants(self):
        member = person('user', ['home', 'stock'])
        self.assertTrue(allowed(member, 'GET', '/stock/overview'))
        self.assertFalse(allowed(member, 'POST', '/workspace/recommendations'))
        self.assertFalse(allowed(member, 'GET', '/team/users'))
        self.assertTrue(allowed(member, 'GET', '/company/settings'))
        self.assertTrue(allowed(member, 'PUT', '/preferences'))
        self.assertFalse(allowed(member, 'PUT', '/settings'))
        self.assertFalse(allowed(member, 'GET', '/admin/companies'))
        self.assertFalse(allowed(member, 'POST', '/integration/keys'))
        granted = person('user', ['settings', 'team'])
        self.assertTrue(allowed(granted, 'PUT', '/settings'))
        self.assertTrue(allowed(granted, 'POST', '/team/users'))
        self.assertFalse(allowed(granted, 'GET', '/stock/overview'))
        # Company admins keep everything; the platform admin also the /admin routes.
        self.assertTrue(allowed(person('company_admin'), 'PUT', '/settings'))
        self.assertFalse(allowed(person('company_admin'), 'GET', '/admin/usage'))
        self.assertTrue(allowed(person('admin'), 'GET', '/admin/usage'))
        # Default grants = the main pages and status marks.
        self.assertEqual(member_features(person('user')), frozenset(DEFAULT_MEMBER_FEATURES))
        self.assertEqual(member_features(person('company_admin', ['home'])), frozenset(FEATURE_KEYS))
        perms = permissions('user', False, ['home', 'jev'])
        self.assertTrue(perms['jev_review'])
        self.assertFalse(perms['manage_team'])
        self.assertEqual(perms['pages'], {'home': True, 'match': False, 'recent': False, 'stock': False, 'buyers': False})


class V50Case(ServerTestCase):
    def register(self, label, password='register-pass'):
        import http.cookiejar
        import urllib.request
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        email = f'isol-{uuid.uuid4().hex}@example.com'
        user = self.call(client, '/auth/register', 'POST', {'company_name': 'Registered ' + label, 'email': email, 'password': password,
                                                             'confirm_password': password}, status=201)
        company = next(c for c in self.call(self.admin, '/admin/companies') if c['contact_email'] == email)
        self.companies.append(company['company_id'])
        return company, user, email, client


class RegistrationTests(V50Case):
    def test_registration_waits_for_approval(self):
        company, user, email, client = self.register('Pending')
        self.assertEqual(user['company_status'], 'pending')
        self.assertEqual(user['role'], 'company_admin')
        self.assertEqual(company['status'], 'pending')
        self.assertEqual(company['name'], 'Registered Pending')
        # Signed in, but nothing works until the platform admin approves.
        self.assertEqual(self.call(client, '/auth/me')['company_status'], 'pending')
        blocked = self.call(client, '/stats', status=403)
        self.assertEqual(blocked['code'], 'company_pending')
        self.call(client, '/company/settings', status=403)
        approved = self.call(self.admin, f"/admin/companies/{company['company_id']}/status", 'POST', {'status': 'active'})
        self.assertEqual(approved['status'], 'active')
        self.assertIsNotNone(approved['reviewed_at'])
        self.assertEqual(self.call(client, '/auth/me')['company_status'], 'active')
        self.assertIn('raw_messages', self.call(client, '/stats'))
        # Deactivating ends the company's sessions; signing in again is refused with a clear code.
        paused = self.call(self.admin, f"/admin/companies/{company['company_id']}/status", 'POST', {'status': 'inactive'})
        self.assertEqual(paused['status'], 'inactive')
        self.call(client, '/auth/me', status=401)
        client = self.client(email, 'register-pass')
        self.assertEqual(self.call(client, '/stats', status=403)['code'], 'company_inactive')
        # Only a pending registration can be rejected; an inactive company is activated again.
        self.call(self.admin, f"/admin/companies/{company['company_id']}/status", 'POST', {'status': 'rejected'}, status=400)
        self.call(self.admin, f"/admin/companies/{company['company_id']}/status", 'POST', {'status': 'active'})
        self.assertIn('raw_messages', self.call(client, '/stats'))

    def test_registration_validation(self):
        import http.cookiejar
        import urllib.request
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        email = f'isol-{uuid.uuid4().hex}@example.com'
        self.call(client, '/auth/register', 'POST', {'company_name': 'X', 'email': email, 'password': 'short', 'confirm_password': 'short'}, status=422)
        self.call(client, '/auth/register', 'POST', {'company_name': 'X', 'email': email, 'password': 'secret1', 'confirm_password': 'secret2'}, status=400)
        company, _, email, _ = self.register('Twice')
        self.call(client, '/auth/register', 'POST', {'company_name': 'Again', 'email': email, 'password': 'secret1', 'confirm_password': 'secret1'}, status=409)
        rejected = self.call(self.admin, f"/admin/companies/{company['company_id']}/status", 'POST', {'status': 'rejected'})
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(self.call(self.client(email, 'register-pass'), '/stats', status=403)['code'], 'company_rejected')

    def test_the_platform_admin_is_not_listed_as_a_company(self):
        companies = self.call(self.admin, '/admin/companies')
        for company in companies:
            self.assertFalse(any(u['role'] == 'admin' for u in company['users']))


class PasswordResetTests(V50Case):
    def test_admin_reset_forces_a_new_password(self):
        company, user, boss = self.make_company('Reset')
        reset = self.call(self.admin, f"/admin/companies/{company['company_id']}/reset-password", 'POST', {})
        self.assertEqual(reset['email'], user['email'])
        self.assertGreaterEqual(len(reset['password']), 8)
        # The old session is gone, the old password no longer works, the temporary one does but only to choose a new one.
        self.call(boss, '/auth/me', status=401)
        self.call(boss, '/auth/login', 'POST', {'email': user['email'], 'password': 'isolation-user-pass'}, status=401)
        client = self.client(user['email'], reset['password'])
        self.assertTrue(self.call(client, '/auth/me')['must_change_password'])
        self.assertEqual(self.call(client, '/stats', status=403)['code'], 'password_change_required')
        self.call(client, '/auth/password/first', 'POST', {'new_password': 'brand-new', 'confirm_password': 'other'}, status=400)
        self.call(client, '/auth/password/first', 'POST', {'new_password': 'brand-new', 'confirm_password': 'brand-new'}, status=204)
        self.assertFalse(self.call(client, '/auth/me')['must_change_password'])
        self.assertIn('raw_messages', self.call(client, '/stats'))
        self.call(client, '/auth/password/first', 'POST', {'new_password': 'again123', 'confirm_password': 'again123'}, status=400)
        self.client(user['email'], 'brand-new')
        # A password set by the company admin for a member is temporary too; one set for yourself is not.
        member, staff = self.make_member(boss := self.client(user['email'], 'brand-new'))
        self.call(boss, f"/team/users/{member['id']}", 'PUT', {'email': member['email'], 'display_name': 'Member', 'password': 'temp-pass-1'})
        self.call(staff, '/auth/me', status=401)
        staff = self.client(member['email'], 'temp-pass-1')
        self.assertEqual(self.call(staff, '/stats', status=403)['code'], 'password_change_required')
        self.call(boss, f"/team/users/{user['id']}", 'PUT', {'email': user['email'], 'display_name': 'Boss', 'password': 'mine-again'})
        self.assertFalse(self.call(boss, '/auth/me')['must_change_password'])

    def test_short_passwords_are_refused_everywhere(self):
        company, user, boss = self.make_company('Short')
        self.call(boss, '/team/users', 'POST', {'email': f'isol-{uuid.uuid4().hex}@example.com', 'display_name': 'S', 'password': 'abc'}, status=422)
        self.call(boss, '/team/users', 'POST', {'email': f'isol-{uuid.uuid4().hex}@example.com', 'display_name': 'S', 'password': 'abcdef'}, status=201)
        self.call(boss, '/auth/password', 'PUT', {'current_password': 'isolation-user-pass', 'new_password': 'abcde'}, status=422)


class FeatureGrantTests(V50Case):
    def test_company_admin_grants_menus_per_member(self):
        company, user, boss = self.make_company('Grants')
        features = self.call(boss, '/team/features')
        self.assertEqual([f['id'] for f in features['features']], list(FEATURE_KEYS))
        member, staff = self.make_member(boss)
        self.assertEqual(member['features'], list(DEFAULT_MEMBER_FEATURES))
        settings = self.call(staff, '/company/settings')
        self.assertEqual(settings['permissions']['pages'], {'home': True, 'match': True, 'recent': True, 'stock': True, 'buyers': True})
        self.assertFalse(settings['permissions']['upload_data'])
        self.call(staff, '/stock/overview')
        self.call(staff, '/team/users', status=403)
        # Narrow to Home only: Stock is refused with a message naming the feature.
        edited = self.call(boss, f"/team/users/{member['id']}", 'PUT', {'email': member['email'], 'display_name': 'Member', 'features': ['home']})
        self.assertEqual(edited['features'], ['home'])
        refused = self.call(staff, '/stock/overview', status=403)
        self.assertIn('Sales stock', refused['detail'])
        self.assertEqual(self.call(staff, '/company/settings')['permissions']['pages']['stock'], False)
        self.call(staff, '/matches/recent/summary')  # the shell's badge stays available
        # Grant Team and Settings: the member manages members and matching settings.
        self.call(boss, f"/team/users/{member['id']}", 'PUT', {'email': member['email'], 'display_name': 'Member', 'features': ['home', 'team', 'settings']})
        self.call(staff, '/team/users')
        self.call(staff, '/settings', 'PUT', {'hot_price_floor_pct': 85})
        self.call(staff, '/team/users', 'POST', {'email': f'isol-{uuid.uuid4().hex}@example.com', 'display_name': 'Peer', 'password': 'peer-pass', 'role': 'company_admin'}, status=403)
        self.call(boss, f"/team/users/{member['id']}", 'PUT', {'email': member['email'], 'display_name': 'Member', 'features': ['nope']}, status=400)
        # A company admin may add another company admin, who then has every feature.
        peer = self.call(boss, '/team/users', 'POST', {'email': f'isol-{uuid.uuid4().hex}@example.com', 'display_name': 'Peer', 'password': 'peer-pass', 'role': 'company_admin'}, status=201)
        self.assertEqual(peer['features'], list(FEATURE_KEYS))
        self.assertTrue(peer['editable'])
        # Nobody locks or demotes their own account.
        self.call(boss, f"/team/users/{user['id']}", 'PUT', {'email': user['email'], 'display_name': 'Boss', 'is_locked': True}, status=400)
        self.call(boss, f"/team/users/{user['id']}", 'PUT', {'email': user['email'], 'display_name': 'Boss', 'role': 'user'}, status=400)


class ServiceAreaTests(V50Case):
    def test_settings_round_trip_and_validation(self):
        company, user, boss = self.make_company('Areas')
        before = self.call(boss, '/settings')
        self.assertEqual(before['category_areas'], {})
        self.assertIn('surabaya', before['service_cities'])
        self.assertTrue(any(c['id'] == 'house' for c in before['categories']))
        saved = self.call(boss, '/settings', 'PUT', {'category_areas': {'house': ['surabaya', 'Surabaya'], 'warehouse': ['surabaya', 'gresik', 'sidoarjo'], 'land': []}})
        self.assertEqual(saved['category_areas'], {'house': ['surabaya'], 'warehouse': ['surabaya', 'gresik', 'sidoarjo']})
        self.assertEqual(self.call(boss, '/settings')['category_areas'], saved['category_areas'])
        # (A company without data has nothing to reprocess, so no pending reason is recorded here.)
        self.call(boss, '/settings', 'PUT', {'category_areas': {'house': ['mars']}}, status=400)
        self.call(boss, '/settings', 'PUT', {'category_areas': {'castle': ['surabaya']}}, status=400)
        # Other fields are untouched by an areas-only save, and vice versa.
        self.assertEqual(float(self.call(boss, '/settings')['hot_price_floor_pct']), 80)
        self.call(boss, '/settings', 'PUT', {'hot_price_floor_pct': 90})
        self.assertEqual(self.call(boss, '/settings')['category_areas'], saved['category_areas'])


class ServiceAreaRuleTests(unittest.TestCase):
    """The matcher itself: a house buyer in Surabaya never gets a house in Malang when houses are Surabaya-only."""

    def pair(self, buyer, listing, areas):
        from dataclasses import asdict
        from matching_rules import assess_pair
        from parser import parse_message
        request = {**asdict(parse_message(buyer)), 'document_type': 'buyer_request', 'raw_text': buyer}
        offer = {**asdict(parse_message(listing)), 'document_type': 'property_listing', 'raw_text': listing}
        from workspace import SETTING_DEFAULTS
        return assess_pair(request, offer, {**SETTING_DEFAULTS, 'category_areas': areas})

    BUYER = 'Buyer request rumah Pakuwon sby barat, Graha, Alana. LT 200 budget 3 M\nHub Rina 081311110000'
    MALANG = 'Dijual rumah baru renov Graha Golf, Malang. LT 200 LB 180 harga 2,8 M\nHub Andi 081322220000'
    SURABAYA = 'Dijual rumah Graha Famili Surabaya Barat LT 200 LB 180 harga 2,8 M\nHub Andi 081322220000'

    def test_a_listing_proven_in_another_city_is_out(self):
        self.assertIsNone(self.pair(self.BUYER, self.MALANG, {'house': ['surabaya']}))
        inside = self.pair(self.BUYER, self.SURABAYA, {'house': ['surabaya']})
        self.assertIsNotNone(inside)
        self.assertIn(inside['status'], ('hot', 'warm', 'custom'))

    def test_a_city_inside_a_place_name_proves_the_city(self):
        # L-AB286: "Graha Golf" alone also exists in Surabaya Barat; "Araya Malang" puts it in Malang.
        araya = ('*Dijual Rumah Mewah, Raya Graha Golf, Araya Malang*\nLT 375 m² (15x25)\nLB 450 m²\nKT 5+1\n'
                 '*Harga 5.25 M Nego*\nIvan 082226811158')
        buyer = 'Dicari rumah di pakuwon, graha, dian istana. Lt min 180. Budget 4-5M. Min 4KT\nBobby 081230297262'
        self.assertIsNone(self.pair(buyer, araya, {'house': ['surabaya']}))

    def test_wider_areas_keep_the_neighbouring_cities(self):
        buyer = 'Buyer request gudang Gresik LT 1000 budget 10 M\nHub Rina 081311110000'
        gresik = 'Dijual gudang Kawasan Industri Gresik LT 1000 LB 800 harga 9 M\nHub Andi 081322220000'
        self.assertIsNotNone(self.pair(buyer, gresik, {'warehouse': ['surabaya', 'gresik', 'sidoarjo']}))
        self.assertIsNone(self.pair(buyer, gresik, {'warehouse': ['surabaya']}))
        # No restriction for the category: unchanged behaviour.
        self.assertIsNotNone(self.pair(buyer, gresik, {'house': ['surabaya']}))
        self.assertIsNotNone(self.pair(buyer, gresik, {}))


class ActivityAndUsageTests(V50Case):
    def test_actions_are_logged_and_usage_is_summarised(self):
        company, user, boss = self.make_company('Logs')
        member, staff = self.make_member(boss)
        self.call(boss, '/settings', 'PUT', {'hot_price_floor_pct': 85})
        self.call(boss, '/auth/logout', 'POST', status=204)
        log = self.call(self.admin, f"/admin/logs?company_id={company['company_id']}")
        actions = [row['action'] for row in log['rows']]
        for expected in ('company_created', 'login', 'member_added', 'settings_changed', 'logout'):
            self.assertIn(expected, actions, actions)
        row = next(r for r in log['rows'] if r['action'] == 'settings_changed')
        self.assertEqual(row['user_email'], user['email'])
        self.assertEqual(row['company_name'], company['name'])
        self.assertEqual(row['method'], 'PUT')
        self.assertTrue(row['label'])
        self.assertTrue(any(a['id'] == 'login' for a in log['actions']))
        from datetime import date, timedelta
        today = date.today().isoformat()
        ranged = self.call(self.admin, f"/admin/logs?company_id={company['company_id']}&from={today}&to={today}")
        self.assertEqual(ranged['total'], log['total'])
        old = (date.today() - timedelta(days=30)).isoformat()
        self.assertEqual(self.call(self.admin, f"/admin/logs?company_id={company['company_id']}&from={old}&to={old}")['total'], 0)
        self.call(self.admin, '/admin/logs?from=yesterday', status=400)
        filtered = self.call(self.admin, f"/admin/logs?company_id={company['company_id']}&action=login")
        self.assertTrue(filtered['rows'] and all(r['action'] == 'login' for r in filtered['rows']))
        self.assertEqual(filtered['total'], len(filtered['rows']))
        # Members never see the log; the platform admin acting inside a company is logged as acting for it.
        self.call(boss := self.client(user['email'], 'isolation-user-pass'), '/admin/logs', status=403)
        self.call(self.admin, '/settings', 'PUT', {'hot_price_floor_pct': 86}, owner=user)
        acted = self.call(self.admin, f"/admin/logs?company_id={company['company_id']}&action=settings_changed")['rows'][0]
        self.assertEqual(acted['user_email'], self.ADMIN_EMAIL)
        self.assertEqual(acted['detail'].get('acting_for'), company['company_id'])
        usage = self.call(self.admin, '/admin/usage?days=7')
        mine = next(c for c in usage['companies'] if c['company_id'] == company['company_id'])
        self.assertEqual(mine['members'], 2)
        self.assertGreaterEqual(mine['logins_period'], 2)
        self.assertEqual(mine['cost_total'], 0)
        self.assertIsNotNone(mine['last_login_at'])
        self.assertIn('cost_period', usage['totals'])
        monthly = self.call(self.admin, f"/admin/usage/monthly?company_id={company['company_id']}")
        self.assertEqual(monthly['rows'], [])
        daily = self.call(self.admin, f"/admin/companies/{company['company_id']}/activity?days=7")
        self.assertTrue(daily['rows'] and daily['rows'][-1]['logins'] >= 1)


if __name__ == '__main__':
    unittest.main()
