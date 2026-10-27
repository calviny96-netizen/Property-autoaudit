"""Jev review over real HTTP + PostgreSQL with a fake OpenRouter (no network, no real API key)."""
import io
import json
import os
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfReader

import ai_keys
import ai_review
from db import connect
from test_v4_features import V4Case

SECRET = 'sk-or-v1-0123456789abcdefXY12'
OTHER_SECRET = 'sk-or-v1-fedcba9876543210ZZ99'
DEEPSEEK = 'deepseek/deepseek-v4.1-flash'
MODELS = [
    {'id': DEEPSEEK, 'name': 'DeepSeek: V4.1 Flash', 'context_length': 128000,
     'pricing': {'prompt': '0.0000002', 'completion': '0.0000008'},
     'architecture': {'modality': 'text->text', 'input_modalities': ['text'], 'output_modalities': ['text']},
     'supported_parameters': ['temperature', 'response_format', 'max_tokens']},
    {'id': 'acme/plain-model', 'name': 'Acme Plain', 'context_length': 8000, 'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
     'architecture': {'modality': 'text->text', 'output_modalities': ['text']}, 'supported_parameters': ['temperature']},
    {'id': 'typesafe/jev-router', 'name': 'Jev Router', 'context_length': 64000, 'pricing': {'prompt': '-1', 'completion': '-1'},
     'architecture': {'modality': 'text->text', 'output_modalities': ['text']}, 'supported_parameters': ['response_format']},
    {'id': 'typesafe/jev-1.13', 'name': 'TypeSafe: Jev 1.13', 'context_length': 32000, 'pricing': {'prompt': '0.00000004'},
     'architecture': {'input_modalities': ['text'], 'output_modalities': ['decisions']}},
    {'id': 'acme/painter', 'name': 'Acme Painter', 'pricing': {'prompt': '0.00001', 'completion': '0.00004'},
     'architecture': {'modality': 'text->image', 'output_modalities': ['image']}},
]


def block(message, title):
    found = re.search(title + r':\n<<<\n(.*?)\n>>>', message, re.S)
    return found.group(1) if found else ''


def answer(decision='recommend', confidence=0.9, reasons=('Lokasi sama: Surabaya Barat', 'Harga masuk budget'), buyer=('rumah', 'Surabaya Barat'),
           listing=('rumah', 'surabaya barat', 'tidak ada di teks')):
    return json.dumps({'decision': decision, 'confidence': confidence, 'reasons': list(reasons),
                       'evidence': {'buyer': list(buyer), 'listing': list(listing)}, 'issues': ['Cek sertifikat']})


def completion(content, cost=0.00042, prompt_tokens=900, completion_tokens=120):
    return 200, {'choices': [{'message': {'role': 'assistant', 'content': content}}],
                 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'cost': cost}}


def default_chat(payload):
    """Recommend house pairs, reject the ruko pair (by the listing text the prompt carries)."""
    listing = block(payload['messages'][1]['content'], 'PROPERTY LISTING')
    if 'ruko' in listing.lower():
        return completion(answer('reject', 0.8, ['Bukan rumah yang dicari'], ['ruko'], ['ruko']))
    return completion(answer())


def default_decide(payload):
    """Decisions API: recommend house pairs, reject the ruko pair."""
    ruko = 'ruko' in payload['state']['listing'].lower()
    probs = {'recommend': 0.12 if ruko else 0.91, 'purpose_match': 0.97, 'unit_match': 0.05 if ruko else 0.95,
             'area_match': 0.55, 'price_match': 0.88, 'size_match': 0.9, 'condition_match': 0.8}
    return 200, {'id': 'd-1', 'model': payload['model'] + '-20260917', 'provider': 'TypeSafe',
                 'answers': {name: {'type': 'noul', 'noul': value} for name, value in probs.items()},
                 'usage': {'input_tokens': 476, 'output_tokens': 70, 'cost': 0.000019992}}


class FakeOpenRouter:
    def __init__(self, chat=default_chat):
        self.chat = chat
        self.decide = default_decide
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, method, url, headers=None, payload=None, timeout=60):
        with self.lock:
            self.calls.append({'method': method, 'url': url, 'headers': dict(headers or {}), 'payload': payload, 'timeout': timeout})
        if url == ai_review.MODELS_URL:
            return 200, {'data': [m for m in MODELS if 'decisions' not in m['architecture'].get('output_modalities', [])]}
        if url == ai_review.DECISION_MODELS_URL:
            return 200, {'data': [m for m in MODELS if 'decisions' in m['architecture'].get('output_modalities', [])]}
        if url == ai_review.DECISIONS_URL:
            return self.decide(payload)
        assert url == ai_review.CHAT_URL, url
        return self.chat(payload)

    def decisions(self):
        return [call for call in self.calls if call['url'] == ai_review.DECISIONS_URL]

    def chats(self):
        return [call for call in self.calls if call['url'] == ai_review.CHAT_URL]


class JevCase(V4Case):
    def setUp(self):
        super().setUp()
        self.fake = FakeOpenRouter()
        for item in (patch.dict(os.environ, {'XM_SECRET_KEY': 'jev-test-secret'}), patch('ai_review.http_json', self.fake),
                     patch('ai_review._sleep', lambda seconds: None)):
            item.start()
            self.addCleanup(item.stop)
        ai_review._models_cache.update(at=0.0, models=None, error=None)

    def add_key(self, client=None, label='Main key', secret=SECRET, **extra):
        return self.call(client or self.boss, '/ai/keys', 'POST', {'label': label, 'api_key': secret, **extra}, status=201)

    def work(self, limit=50):
        for _ in range(limit):
            if not ai_review.process_next_review():
                return
        self.fail('the review queue did not drain')

    def start(self, **scope):
        return self.call(self.boss, '/ai/review-runs', 'POST', {'model': DEEPSEEK, **scope}, status=202)


class KeyTests(JevCase):
    def test_key_crud_encryption_masking_and_member_403(self):
        first = self.add_key()
        self.assertEqual((first['last4'], first['masked'], first['is_default'], first['provider']), ('XY12', '••••XY12', True, 'openrouter'))
        self.assertEqual(first['created_by_name'], 'Admin V4')
        second = self.add_key(label='Backup', secret=OTHER_SECRET)
        self.assertFalse(second['is_default'])
        listed = self.call(self.boss, '/ai/keys')
        text = json.dumps(listed)
        for secret in (SECRET, OTHER_SECRET):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret[:-4], text)
        with connect() as conn:
            row = conn.execute('SELECT secret_encrypted,last4 FROM xm.ai_keys WHERE id=%s', (first['id'],)).fetchone()
        self.assertNotIn(SECRET, row['secret_encrypted'])
        self.assertEqual(ai_keys.decrypt(row['secret_encrypted']), SECRET)
        # Default moves; deleting the default promotes another key.
        keys = self.call(self.boss, f"/ai/keys/{second['id']}/default", 'POST')['keys']
        self.assertEqual([k['id'] for k in keys if k['is_default']], [second['id']])
        keys = self.call(self.boss, f"/ai/keys/{second['id']}", 'DELETE')['keys']
        self.assertEqual([(k['id'], k['is_default']) for k in keys], [(first['id'], True)])
        self.call(self.boss, '/ai/keys', 'POST', {'label': 'x', 'api_key': 'has spaces in it'}, status=422)
        # Members get 403 on every Jev endpoint, reads included, and do not get the menu permission.
        for path, method, body in (('/ai/keys', 'GET', None), ('/ai/keys', 'POST', {'label': 'm', 'api_key': SECRET}),
                                   (f"/ai/keys/{first['id']}", 'DELETE', None), ('/ai/models', 'GET', None),
                                   ('/ai/review-runs/estimate', 'POST', {}), ('/ai/review-runs', 'GET', None),
                                   ('/ai/reviews/lookup', 'POST', {'pairs': []})):
            self.call(self.staff, path, method, body, status=403)
        self.assertTrue(self.call(self.boss, '/company/settings')['permissions']['jev_review'])
        self.assertFalse(self.call(self.staff, '/company/settings')['permissions']['jev_review'])
        # Another company never sees or touches these keys.
        _, _, other = self.make_company('Other AI')
        self.assertEqual(self.call(other, '/ai/keys')['keys'], [])
        self.call(other, f"/ai/keys/{first['id']}", 'DELETE', status=404)
        self.call(other, f"/ai/keys/{first['id']}/default", 'POST', status=404)

    def test_secret_file_is_created_once_with_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XM_SECRET_KEY': ''}), \
                patch('ai_keys.SECRET_FILE', Path(folder) / 'xm-secret.key'):
            token = ai_keys.encrypt(SECRET)
            path = Path(folder) / 'xm-secret.key'
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            saved = path.read_text()
            self.assertEqual(ai_keys.decrypt(token), SECRET)
            self.assertEqual(path.read_text(), saved)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ['xm-secret.key'])
        with patch.dict(os.environ, {'XM_SECRET_KEY': 'another-secret'}):
            with self.assertRaises(ai_keys.SecretUnavailable):
                ai_keys.decrypt(token)


class ModelTests(JevCase):
    def test_models_are_text_only_with_pinned_presets_and_cached(self):
        data = self.call(self.boss, '/ai/models')
        self.assertEqual([p['id'] for p in data['presets']],
                         ['typesafe/jev-1.13', 'typesafe/jev-1.14', '~typesafe/jev-latest', 'typesafe/jev-router', DEEPSEEK,
                          'meta/muse-spark-1.3-contributor'])
        self.assertEqual([p['name'] for p in data['presets']], ['Jev 1.13', 'Jev 1.14', 'Jev Latest',
                         'Jev Router (text model that picks a model for you)', 'DeepSeek V4.1 Flash', 'Muse Spark 1.3 Contributor'])
        jev113, jev114, latest, router, deepseek, muse = data['presets']
        self.assertEqual((jev113['default'], jev113['listed'], jev113['kind'], jev113['pricing']),
                         (True, True, 'decision', {'prompt': 0.00000004, 'completion': 0.0}))
        self.assertEqual((jev114['kind'], latest['kind'], router['kind'], deepseek['kind']), ('decision', 'decision', 'text', 'text'))
        self.assertEqual((latest['default'], latest['listed'], latest['price_varies'], latest['pricing']), (False, False, True, None))
        self.assertTrue(router['listed'] and router['price_varies'])  # -1 pricing: varies
        self.assertEqual(deepseek['pricing'], {'prompt': 0.0000002, 'completion': 0.0000008})
        self.assertFalse(muse['listed'])
        self.assertEqual([m['id'] for m in data['models']], ['acme/plain-model', DEEPSEEK, 'typesafe/jev-router', 'typesafe/jev-1.13'])
        self.assertEqual((data['default_model'], data['last_model'], data['error']), ('typesafe/jev-1.13', 'typesafe/jev-1.13', None))
        self.call(self.boss, '/ai/models')
        self.assertEqual(len(self.fake.calls), 2)  # one hour cache (text list + decisions list)
        self.call(self.boss, '/ai/models?refresh=true')
        self.assertEqual(len(self.fake.calls), 4)


class RunTests(JevCase):
    def test_estimate_run_processing_prompt_and_progress(self):
        self.seed()
        key = self.add_key()
        estimate = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK})
        # Both directions: 3 pairs from the listings' side and 3 from the buyers', 3 unique texts to review.
        self.assertEqual((estimate['pairs'], estimate['unique_inputs'], estimate['cached'], estimate['to_call']), (6, 3, 0, 3))
        self.assertEqual(estimate['by_direction'], {'property': 3, 'buyer': 3})
        self.assertFalse(estimate['price_varies'])
        self.assertGreater(estimate['cost'], 0)
        self.assertGreater(estimate['tokens_in'], 3 * ai_review.SYSTEM_TOKENS)
        self.assertEqual({g['title'] for g in estimate['groups']}, {'Andi', 'Budi', 'Rina', 'Dodi'})
        one = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'directions': 'buyer', 'max_pairs': 2})
        self.assertEqual((one['pairs'], one['available'], one['capped']), (2, 3, True))
        only = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK, 'directions': 'property', 'group_keys': ['~ Andi']})
        self.assertEqual(only['pairs'], 2)
        alias = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': '~typesafe/jev-latest'})
        self.assertEqual((alias['price_varies'], alias['cost']), (True, None))
        self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': 'bad model id'}, status=422)
        self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'max_pairs': 2001}, status=422)

        run = self.start(statuses=['hot', 'warm', 'custom'])
        self.assertEqual((run['status'], run['total'], run['pending'], run['key_id']), ('queued', 6, 6, key['id']))
        self.assertEqual(self.call(self.boss, '/ai/models')['last_model'], DEEPSEEK)
        self.work()
        done = self.call(self.boss, f"/ai/review-runs/{run['id']}")
        self.assertEqual((done['status'], done['done'], done['failed'], done['pending']), ('completed', 6, 0, 0))
        self.assertEqual((done['recommend'], done['reject'], done['cached']), (4, 2, 3))
        self.assertAlmostEqual(done['cost'], 3 * 0.00042)
        chats = self.fake.chats()
        self.assertEqual(len(chats), 3)
        call = chats[0]
        self.assertEqual(call['headers']['Authorization'], 'Bearer ' + SECRET)
        self.assertTrue(call['headers']['HTTP-Referer'] and call['headers']['X-Title'])
        self.assertEqual((call['payload']['model'], call['payload']['temperature'], call['payload']['response_format']),
                         (DEEPSEEK, 0, {'type': 'json_object'}))
        self.assertEqual(call['timeout'], 60)
        prompt = call['payload']['messages']
        self.assertEqual(prompt[0]['role'], 'system')
        self.assertIn('Purpose first', prompt[0]['content'])
        self.assertIn('Surat Ijo', prompt[0]['content'])
        for sent in chats:  # signatures (names and phone numbers) stay out of the prompt
            self.assertNotIn('081234567890', sent['payload']['messages'][1]['content'])
            self.assertNotIn('6282233744657', sent['payload']['messages'][1]['content'])
        results = self.call(self.boss, f"/ai/review-runs/{run['id']}/results")
        items = [item for group in results['groups'] for item in group['items']]
        self.assertEqual(len(items), 6)
        house = next(item for item in items if item['effective_decision'] == 'recommend' and item['direction'] == 'buyer')
        self.assertEqual(house['reasons'], ['Lokasi sama: Surabaya Barat', 'Harga masuk budget'])
        self.assertEqual(house['confidence'], 0.9)
        # Evidence keeps only exact phrases of each text (with the text's own spelling).
        self.assertEqual(house['evidence'], {'source': ['rumah', 'Surabaya Barat'], 'target': ['rumah', 'Surabaya Barat']})
        self.assertIn(house['engine_status'], ('hot', 'warm', 'custom'))
        self.assertTrue(house['engine_explanation'])
        rejected = self.call(self.boss, f"/ai/review-runs/{run['id']}/results?decision=reject")
        self.assertEqual({g['title'] for g in rejected['groups']}, {'Budi', 'Dodi'})
        listed = self.call(self.boss, '/ai/review-runs')['runs']
        self.assertEqual([r['id'] for r in listed], [run['id']])
        # The Match page badge: latest review of a pair.
        lookup = self.call(self.boss, '/ai/reviews/lookup', 'POST', {'pairs': [
            {'buyer_entity_id': house['buyer_entity_id'], 'listing_entity_id': house['listing_entity_id']}]})['reviews']
        self.assertEqual([(r['effective_decision'], r['model_label']) for r in lookup], [('recommend', 'DeepSeek V4.1 Flash')])
        # Another company cannot read the run.
        _, _, other = self.make_company('Other run')
        self.call(other, f"/ai/review-runs/{run['id']}", status=404)
        self.call(other, f"/ai/review-runs/{run['id']}/results", status=404)
        self.assertEqual(self.call(other, '/ai/reviews/lookup', 'POST', {'pairs': [
            {'buyer_entity_id': house['buyer_entity_id'], 'listing_entity_id': house['listing_entity_id']}]})['reviews'], [])

    def test_alias_model_is_sent_unchanged_and_needs_a_key(self):
        self.seed()
        self.call(self.boss, '/ai/review-runs', 'POST', {'model': '~typesafe/jev-latest'}, status=400)  # no key yet
        self.add_key()
        run = self.start(model='~typesafe/jev-latest', directions='buyer')
        self.work()
        self.assertEqual({c['payload']['model'] for c in self.fake.decisions()}, {'~typesafe/jev-latest'})
        self.assertEqual(self.fake.chats(), [])  # a decision model never goes to chat/completions
        self.assertEqual(self.call(self.boss, f"/ai/review-runs/{run['id']}")['status'], 'completed')

    def test_jev_router_still_uses_chat_completions(self):
        self.seed()
        self.add_key()
        run = self.start(model='typesafe/jev-router', directions='buyer')
        self.work()
        self.assertTrue(self.fake.chats())
        self.assertEqual(self.fake.decisions(), [])
        self.assertEqual({c['payload']['model'] for c in self.fake.chats()}, {'typesafe/jev-router'})
        self.assertEqual(self.call(self.boss, f"/ai/review-runs/{run['id']}")['status'], 'completed')

    def test_decision_model_request_mapping_and_pdf(self):
        self.seed()
        self.add_key()
        estimate = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': 'typesafe/jev-1.13'})
        self.assertEqual((estimate['model_kind'], estimate['tokens_out'], estimate['prompt_version']), ('decision', 0, 'jev-d1'))
        self.assertGreater(estimate['tokens_in'], 0)
        self.assertAlmostEqual(estimate['cost'], estimate['tokens_in'] * 0.00000004, places=6)
        run = self.start(model='typesafe/jev-1.13')
        self.work()
        calls = self.fake.decisions()
        self.assertTrue(calls)
        self.assertEqual(self.fake.chats(), [])
        call = calls[0]
        self.assertEqual(call['headers']['Authorization'], 'Bearer ' + SECRET)
        payload = call['payload']
        self.assertEqual(set(payload), {'model', 'state', 'questions'})
        self.assertEqual(payload['model'], 'typesafe/jev-1.13')
        self.assertEqual(set(payload['state']), {'buyer_request', 'listing', 'direction', 'engine_parsed', 'engine_notes'})
        self.assertEqual(set(payload['state']['engine_parsed']), {'buyer', 'listing'})
        self.assertNotIn('08', payload['state']['buyer_request'][-14:])  # signature (phone) stays out
        self.assertEqual(set(payload['questions']), {'recommend', 'purpose_match', 'unit_match', 'area_match', 'price_match',
                                                     'size_match', 'condition_match'})
        for question in payload['questions'].values():
            self.assertEqual((question['type'], set(question['criteria'])), ('noul', {'true', 'false'}))
        self.assertLess(len(json.dumps(payload)), 32000)
        results = self.call(self.boss, f"/ai/review-runs/{run['id']}/results")
        items = [i for g in results['groups'] for i in g['items']]
        house = next(i for i in items if i['decision'] == 'recommend')
        ruko = next(i for i in items if i['decision'] == 'reject')
        self.assertEqual(house['confidence'], 0.91)
        self.assertEqual(house['probabilities']['area_match'], 0.55)
        self.assertEqual(house['reasons'], ['Area meragukan', 'Tujuan cocok', 'Jenis unit cocok', 'Harga cocok', 'Ukuran cocok', 'Kondisi cocok'])
        self.assertEqual(ruko['reasons'][0], 'Jenis unit tidak cocok')
        summary = self.call(self.boss, f"/ai/review-runs/{run['id']}")
        self.assertEqual(summary['status'], 'completed')
        self.assertAlmostEqual(summary['cost'], 0.000019992 * len(calls), places=9)
        single = self.call(self.boss, f"/ai/review-runs/{run['id']}/export?output=single")
        text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(single)).pages)
        self.assertIn('Saran AI', text)
        self.assertIn('Area meragukan', text)
        for hidden in ('Jev', 'jev', '91%', 'keyakinan', 'typesafe'):
            self.assertNotIn(hidden, text)

    def test_invalid_json_becomes_unsure_with_the_error(self):
        self.seed()
        self.add_key()
        self.fake.chat = lambda payload: completion('Sure! The pair looks good.')
        run = self.start(directions='buyer')
        self.work()
        results = self.call(self.boss, f"/ai/review-runs/{run['id']}/results")
        items = [item for group in results['groups'] for item in group['items']]
        self.assertTrue(items)
        for item in items:
            self.assertEqual((item['status'], item['decision'], item['reasons']), ('done', 'unsure', []))
            self.assertIn('Invalid answer from the model: not valid JSON', item['error'])
            self.assertIn('Sure! The pair looks good.', item['error'])
        # Wrong fields are invalid too, and an invalid answer is never reused from the cache.
        self.fake.chat = lambda payload: completion(json.dumps({'decision': 'maybe', 'confidence': 2, 'reasons': ['x']}))
        again = self.start(directions='buyer')
        self.work()
        items = [i for g in self.call(self.boss, f"/ai/review-runs/{again['id']}/results")['groups'] for i in g['items']]
        self.assertTrue(all('"decision" must be' in item['error'] for item in items))
        self.assertTrue(all(not item['cached'] for item in items if item['cost']))

    def test_401_stops_the_run_with_a_clear_error(self):
        self.seed()
        self.add_key()
        self.fake.chat = lambda payload: (401, {'error': {'code': 401, 'message': 'No auth credentials found'}})
        run = self.start()
        self.work()
        failed = self.call(self.boss, f"/ai/review-runs/{run['id']}")
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('401', failed['error'])
        self.assertIn('API key', failed['error'])
        self.assertEqual(failed['done'], 0)
        self.assertGreater(failed['pending'], 0)
        self.assertLessEqual(len(self.fake.chats()), ai_review.MAX_CONCURRENCY)
        self.assertFalse(ai_review.process_next_review())  # nothing else is attempted

    def test_402_and_retries(self):
        self.seed()
        self.add_key()
        attempts = []

        def flaky(payload):
            attempts.append(1)
            return (503, {'error': {'message': 'overloaded'}}) if len(attempts) < 3 else completion(answer())
        self.fake.chat = flaky
        run = self.start(directions='buyer', max_pairs=1)
        self.work()
        self.assertEqual(len(attempts), 3)  # two 503s retried, then answered
        self.assertEqual(self.call(self.boss, f"/ai/review-runs/{run['id']}")['done'], 1)
        self.fake.chat = lambda payload: (402, {'error': {'code': 402, 'message': 'Insufficient credits'}})
        broke = self.start(directions='property', model='acme/plain-model')
        self.work()
        failed = self.call(self.boss, f"/ai/review-runs/{broke['id']}")
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('credits', failed['error'])
        # A model without response_format support is asked without it.
        self.assertNotIn('response_format', self.fake.chats()[-1]['payload'])

    def test_cache_reuses_reviews_without_calling_the_api(self):
        self.seed()
        self.add_key()
        first = self.start()
        self.work()
        calls = len(self.fake.chats())
        estimate = self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': DEEPSEEK})
        self.assertEqual((estimate['cached'], estimate['to_call'], estimate['cost']), (3, 0, 0))
        second = self.start()
        self.work()
        self.assertEqual(len(self.fake.chats()), calls)
        summary = self.call(self.boss, f"/ai/review-runs/{second['id']}")
        self.assertEqual((summary['status'], summary['done'], summary['cached'], summary['cost']), ('completed', 6, 6, 0))
        before = {(i['buyer_entity_id'], i['listing_entity_id'], i['direction']): i['decision']
                  for g in self.call(self.boss, f"/ai/review-runs/{first['id']}/results")['groups'] for i in g['items']}
        after = {(i['buyer_entity_id'], i['listing_entity_id'], i['direction']): i['decision']
                 for g in self.call(self.boss, f"/ai/review-runs/{second['id']}/results")['groups'] for i in g['items']}
        self.assertEqual(before, after)
        # Another model is a different cache entry.
        self.assertEqual(self.call(self.boss, '/ai/review-runs/estimate', 'POST', {'model': 'acme/plain-model'})['to_call'], 3)

    def test_cancel_stops_before_the_next_chunk(self):
        self.seed()
        self.add_key()
        run = self.start()
        cancelled = self.call(self.boss, f"/ai/review-runs/{run['id']}/cancel", 'POST')
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertFalse(ai_review.process_next_review())
        self.assertEqual(self.fake.chats(), [])
        self.call(self.staff, f"/ai/review-runs/{run['id']}/cancel", 'POST', status=403)

    def test_override_and_export_only_approved_pairs_per_person(self):
        self.seed()
        self.add_key()
        run = self.start()
        self.work()
        results = self.call(self.boss, f"/ai/review-runs/{run['id']}/results")
        items = [i for g in results['groups'] for i in g['items']]
        zipped = self.call(self.boss, f"/ai/review-runs/{run['id']}/export?output=grouped")
        with ZipFile(io.BytesIO(zipped)) as bundle:
            names = sorted(bundle.namelist())
            pdfs = {name: PdfReader(io.BytesIO(bundle.read(name))) for name in names}
        # Only Jev's recommendations: Andi's two listings (Listing -> Buyer) and Rina's buyer (Buyer -> Listing, two pairs).
        self.assertEqual(len(names), 2)
        self.assertTrue(any('_Andi_' in name for name in names) and any('_Rina_' in name for name in names), names)
        for name, reader in pdfs.items():
            self.assertEqual(len(reader.pages), 3)  # one cover + two pair pages
            text = '\n'.join(page.extract_text() for page in reader.pages)
            self.assertIn('Saran AI', text)
            self.assertNotIn('Jev', text)
            self.assertNotIn('DeepSeek', text)
            self.assertNotIn('90%', text)
            self.assertIn('Lokasi sama: Surabaya Barat', text)
            self.assertIn('Hal. 1 dari 3', text)
            self.assertIn('No. 2', text)
            self.assertNotIn('ruko', text.lower())
        # An admin override to "recommend" adds the pair to both people (it covers both directions of the pair);
        # going back to Jev's decision removes it again.
        ruko = next(i for i in items if i['decision'] == 'reject' and i['direction'] == 'property')
        other = next(i for i in items if i['decision'] == 'reject' and i['direction'] == 'buyer')
        changed = self.call(self.boss, f"/ai/reviews/{ruko['id']}/override", 'PUT', {'decision': 'recommend'})
        self.assertEqual((changed['effective_decision'], changed['override_by_name'], changed['also_updated']),
                         ('recommend', 'Admin V4', [other['id']]))
        self.call(self.staff, f"/ai/reviews/{ruko['id']}/override", 'PUT', {'decision': 'reject'}, status=403)
        single = self.call(self.boss, f"/ai/review-runs/{run['id']}/export?output=single")
        reader = PdfReader(io.BytesIO(single))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertEqual(len(reader.pages), 3 + 3 + 2 + 2)  # Andi, Rina, then Budi and Dodi (cover + one pair each)
        self.assertIn('Disetujui manual oleh admin', text)
        self.assertEqual(self.call(self.boss, f"/ai/review-runs/{run['id']}")['overrides'], 2)
        badge = self.call(self.boss, '/ai/reviews/lookup', 'POST', {'pairs': [
            {'buyer_entity_id': ruko['buyer_entity_id'], 'listing_entity_id': ruko['listing_entity_id']}]})['reviews']
        self.assertEqual([(r['decision'], r['effective_decision']) for r in badge], [('reject', 'recommend')])
        reset = self.call(self.boss, f"/ai/reviews/{ruko['id']}/override", 'PUT', {'decision': None})
        self.assertEqual((reset['effective_decision'], reset['override_decision']), ('reject', None))
        with ZipFile(io.BytesIO(self.call(self.boss, f"/ai/review-runs/{run['id']}/export"))) as bundle:
            self.assertEqual(len(bundle.namelist()), 2)
        self.call(self.staff, f"/ai/review-runs/{run['id']}/export", status=403)

    def test_export_without_approved_pairs_is_404(self):
        self.seed()
        self.add_key()
        self.fake.chat = lambda payload: completion(answer('reject', 0.7, ['Tidak cocok']))
        run = self.start(directions='buyer')
        self.work()
        self.call(self.boss, f"/ai/review-runs/{run['id']}/export", status=404)


class AnswerTests(unittest.TestCase):
    def test_decision_answers_map_to_decision_and_reasons(self):
        noul = lambda value: {'type': 'noul', 'noul': value}
        mapped = ai_review.decision_from_answers({'recommend': noul(0.7), 'purpose_match': noul(0.1), 'area_match': noul(0.3),
                                                  'price_match': noul(0.31), 'unit_match': noul(0.69)})
        self.assertEqual((mapped['decision'], mapped['confidence']), ('recommend', 0.7))
        self.assertEqual(mapped['reasons'], ['Tujuan tidak cocok', 'Area tidak cocok', 'Harga tidak cocok', 'Jenis unit meragukan'])
        self.assertEqual(mapped['evidence'], {'buyer': [], 'listing': []})
        self.assertEqual(ai_review.decision_from_answers({'recommend': noul(0.3)})['decision'], 'reject')
        self.assertEqual(ai_review.decision_from_answers({'recommend': noul(0.5)})['decision'], 'unsure')
        with self.assertRaises(ValueError):
            ai_review.decision_from_answers({'purpose_match': noul(0.9)})
        self.assertTrue(ai_review.is_known_decision('~typesafe/jev-1.13'))
        self.assertFalse(ai_review.is_known_decision('typesafe/jev-router'))

    def test_answer_validation(self):
        parse = ai_review.parse_answer
        good = parse('```json\n' + answer() + '\n```', 'Cari rumah Surabaya Barat', 'Dijual rumah  Surabaya   Barat')
        self.assertEqual(good['evidence'], {'buyer': ['rumah', 'Surabaya Barat'], 'listing': ['rumah', 'Surabaya   Barat']})
        for bad in ('', '[]', '{"decision":"yes","confidence":0.5,"reasons":["a"]}', '{"decision":"reject","confidence":true,"reasons":["a"]}',
                    '{"decision":"reject","confidence":0.5,"reasons":[]}', '{"decision":"reject","confidence":0.5,"reasons":["a"],"evidence":[]}'):
            with self.assertRaises(ValueError):
                parse(bad, 'a', 'b')

    def test_pdf_highlights_come_from_jev_phrases(self):
        from report import jev_callout, jev_evidence
        source = {'raw_text': 'Cari rumah di Darmo budget 3 M', 'id': 1}
        target = {'raw_text': 'Dijual rumah Darmo harga 2,8 M', 'id': 2,
                  'jev': {'model': 'Jev Latest', 'confidence': 0.876, 'reasons': ['Lokasi sama'], 'issues': [],
                          'evidence': {'buyer': ['Darmo', 'budget 3 M'], 'listing': ['harga 2,8 M']}}}
        title, items = jev_callout(target)
        self.assertEqual(title, 'Saran AI')
        self.assertEqual(items, [('positive', 'Lokasi sama')])
        buyer_marks, listing_marks = jev_evidence(source, target, 'buyer')
        self.assertEqual([source['raw_text'][a:b] for a, b in buyer_marks], ['Darmo', 'budget 3 M'])
        self.assertEqual([target['raw_text'][a:b] for a, b in listing_marks], ['harga 2,8 M'])


if __name__ == '__main__':
    unittest.main()
