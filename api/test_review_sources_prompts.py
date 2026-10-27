"""Review source parity and tenant/account prompt isolation; fake LLM only."""
from datetime import datetime, timedelta

from db import connect
from tenant import workspace_scope
from test_jev_review import JevCase, DEEPSEEK, SECRET
from periods import WIB
import ai_review


class ReviewSourcesPrompts(JevCase):
    def add_key(self):
        return self.call(self.admin, '/ai/keys', 'POST', {'label': 'Test', 'api_key': SECRET}, owner=self.user, status=201)

    def test_recent_keeps_old_postings_and_manual_dates_stay_independent(self):
        self.assertEqual(ai_review.ReviewScope(buyer_date_from='2026-09-01').source, 'match')
        self.assertEqual(ai_review.ReviewScope().source, 'recent')
        self.seed()
        today = datetime.now(WIB).date().isoformat()
        with workspace_scope(self.company['company_id']), connect() as conn:
            conn.execute("UPDATE xm.raw_messages SET sent_at=now()-interval '40 days' WHERE company_id=current_setting('xm.workspace_id')")
            conn.commit()
        for direction in ('buyer', 'property'):
            recent = self.call(self.boss, f'/matches/recent?direction={direction}&date_from={today}&date_to={today}')
            estimate = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'directions': direction})
            self.assertGreater(estimate['pairs'], 0)
            self.assertEqual(estimate['pairs'], recent['totals']['pairs'])
            manual = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {
                'model': DEEPSEEK, 'source': 'match', 'directions': direction,
                'buyer_date_from': today, 'buyer_date_to': today, 'listing_date_from': today, 'listing_date_to': today})
            self.assertEqual(manual['pairs'], 0)
        past = (datetime.now(WIB).date() - timedelta(days=10)).isoformat()
        empty = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'found_from': past, 'found_to': past})
        self.assertEqual(empty['pairs'], 0)
        self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'found_from': 'invalid'}, status=400)

    def test_saved_prompts_isolation_snapshot_and_cache(self):
        self.seed(); self.add_key()
        body = {'name': 'Budget', 'content': 'Check the budget carefully.', 'visibility': 'company'}
        shared = self.call(self.boss, '/ai/prompts', 'POST', body, status=201)
        personal = self.call(self.boss, '/ai/prompts', 'POST', {**body, 'visibility': 'account'}, status=201)
        catalog = self.call(self.boss, '/ai/prompts')
        self.assertEqual(len(catalog['prompts']), 2)
        self.assertEqual(catalog['system_prompt'], ai_review.SYSTEM_PROMPT)
        _, _, other = self.make_company('Other review prompts')
        self.assertEqual(self.call(other, '/ai/prompts')['prompts'], [])
        self.call(other, '/ai/prompts/' + shared['id'], 'PUT', body, status=404)
        self.call(other, '/ai/review-runs/estimate', 'POST', {'prompt_id': shared['id']}, status=404)
        # Account-scoped prompt is not visible to another permitted account in the same company.
        with connect() as conn:
            conn.execute("UPDATE xm.users SET features='[\"jev\"]'::jsonb WHERE id=%s", (self.member['id'],))
            conn.commit()
        self.assertEqual([p['id'] for p in self.call(self.staff, '/ai/prompts')['prompts']], [shared['id']])
        self.call(self.staff, '/ai/prompts/' + personal['id'], 'DELETE', status=404)
        self.call(self.staff, '/ai/prompts', 'POST', body, status=403)
        self.call(self.staff, '/ai/prompts/' + shared['id'], 'DELETE', status=403)
        first = self.start(prompt_id=shared['id'])
        self.call(self.boss, '/ai/prompts/' + shared['id'], 'PUT', {**body, 'content': 'Check legal documents.'})
        self.work()
        requests = [c['payload'] for c in self.fake.calls if c['url'] == ai_review.CHAT_URL]
        self.assertTrue(requests)
        self.assertIn(body['content'], requests[0]['messages'][0]['content'])
        snapshot = self.call(self.boss, '/ai/review-runs/' + first['id'])
        self.assertEqual(snapshot['payload']['custom_prompt'], body['content'])
        self.assertEqual(snapshot['payload']['system_prompt'], ai_review.SYSTEM_PROMPT)
        cached = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'custom_prompt': body['content']})
        self.assertEqual(cached['to_call'], 0)
        changed = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'prompt_id': shared['id']})
        self.assertGreater(changed['to_call'], 0)
        self.call(self.boss, '/ai/prompts/' + shared['id'], 'DELETE', status=204)
        self.call(self.boss, '/ai/prompts/' + personal['id'], 'DELETE', status=204)

    def test_custom_prompt_reaches_decision_models(self):
        self.seed(); self.add_key()
        run = self.start(model='typesafe/jev-1.13', custom_prompt='Verify the certificate.')
        self.work()
        calls = [c['payload'] for c in self.fake.calls if c['url'] == ai_review.DECISIONS_URL]
        self.assertTrue(calls)
        self.assertIn('Verify the certificate.', calls[0]['questions']['recommend']['instructions'])
        self.assertEqual(self.call(self.boss, '/ai/review-runs/' + run['id'])['status'], 'completed')
