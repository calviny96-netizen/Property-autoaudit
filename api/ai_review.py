"""Jev review: an AI second opinion on the pairs the rule engine found (company admins only).

Flow: ``POST /ai/review-runs/estimate`` (how many pairs, tokens and cost) -> ``POST /ai/review-runs`` (a queued run
with one ``xm.ai_reviews`` row per pair) -> the worker's review thread calls OpenRouter for the pending rows in small
chunks (``process_next_review``), at most ``MAX_CONCURRENCY`` requests at a time, and never blocks the maintenance and
import loop. A pair whose two texts were already reviewed with the same model and prompt version reuses that answer.
The run page reads progress and results, an admin may override a decision, and "Export Jev-approved" renders the
recommended pairs with the same per-person PDF layout as "Export both directions".
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import workspace_cache
from ai_keys import SecretUnavailable, key_secret, resolve_key_id
from auth import current_user
from company import company_row
from db import connect
from parser import clean_text, split_contact
from periods import MatchingPeriods, WIB, shift_month
from tenant import workspace_id, workspace_scope

router = APIRouter(prefix='/ai')

PROMPT_VERSION = 'jev-v1'
DECISION_PROMPT_VERSION = 'jev-d2'
RECOMMEND_AT = 0.70  # recommend probability at or above: recommend
REJECT_AT = 0.40     # strictly below: reject; 40% through <70%: unsure
OPENROUTER = os.getenv('XM_OPENROUTER_URL', 'https://openrouter.ai/api/v1').rstrip('/')
MODELS_URL = OPENROUTER + '/models'
DECISION_MODELS_URL = MODELS_URL + '?output_modalities=decisions'
CHAT_URL = OPENROUTER + '/chat/completions'
# Decision models (OpenRouter output modality "decisions", e.g. TypeSafe's Jev) answer typed questions with probabilities
# on a separate endpoint instead of writing text.
DECISIONS_URL = os.getenv('XM_OPENROUTER_DECISIONS_URL', re.sub(r'/v1$', '', OPENROUTER) + '/alpha/decisions')
APP_URL = os.getenv('XM_PUBLIC_URL', 'https://autoaudit.id')
APP_TITLE = 'Property Auto Audit - Jev review'
# Pinned on top of the model list. "~" ids are OpenRouter aliases and are sent unchanged.
PRESETS = (
    {'id': 'typesafe/jev-1.13', 'name': 'Jev 1.13'},
    {'id': 'typesafe/jev-1.14', 'name': 'Jev 1.14'},
    {'id': '~typesafe/jev-latest', 'name': 'Jev Latest'},
    {'id': 'typesafe/jev-router', 'name': 'Jev Router (text model that picks a model for you)'},
    {'id': 'deepseek/deepseek-v4.1-flash', 'name': 'DeepSeek V4.1 Flash'},
    {'id': 'meta/muse-spark-1.3-contributor', 'name': 'Muse Spark 1.3 Contributor'},
)
DEFAULT_MODEL = PRESETS[0]['id']
# Known decision models; the model list's output_modalities ("decisions") adds any others.
KNOWN_DECISION_MODELS = frozenset({
    'typesafe/jev-1.13', 'typesafe/jev-1.14', '~typesafe/jev-latest', 'perplexity/pplx-decider-v1.2', 'microsoft/microsoft-decision-1',
    'openai/gpt-6-luna-decisions', 'cloudflare/clef', 'liquid/d1', 'upstage/solar-decide', 'inception/mercury-decide'})
MODEL_ID = re.compile(r'~?[A-Za-z0-9][A-Za-z0-9._\-/:]{0,199}')
MODELS_TTL = 3600
REQUEST_TIMEOUT = 60
RETRIES = 3
RETRY_BASE_SECONDS = float(os.getenv('XM_JEV_RETRY_SECONDS', '1'))
MAX_CONCURRENCY = 4
CHUNK = 8
MAX_PAIRS = 2000
SCAN_LIMIT = 20000  # candidate pairs read before sorting; protects the API process on very large companies
MAX_OUTPUT_TOKENS = int(os.getenv('XM_JEV_MAX_TOKENS', '2000'))
OUTPUT_TOKENS_GUESS = 260
STATUS_ORDER = ('hot', 'warm', 'custom')
DECISIONS = ('recommend', 'reject', 'unsure')
_sleep = time.sleep


# ---------------------------------------------------------------- HTTP
class TransportError(Exception):
    """No HTTP answer (timeout, DNS, connection reset)."""


def http_json(method, url, headers=None, payload=None, timeout=REQUEST_TIMEOUT):
    """(status, parsed JSON or {'_text': body}). Network failures raise TransportError. Tests replace this function."""
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method=method, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise TransportError(str(getattr(exc, 'reason', exc)) or type(exc).__name__) from exc
    try:
        return status, json.loads(raw.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return status, {'_text': raw[:500].decode('utf-8', 'replace')}


# ---------------------------------------------------------------- models
_models_cache = {'at': 0.0, 'models': None, 'error': None}
_models_lock = threading.Lock()


def _price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None  # routers report -1: the price depends on the model they pick


def _text_model(item):
    arch = item.get('architecture') or {}
    outputs = arch.get('output_modalities')
    if isinstance(outputs, list) and outputs:
        return 'text' in outputs and 'image' not in outputs and 'audio' not in outputs
    modality = str(arch.get('modality') or 'text->text')
    return modality.split('->')[-1].strip() == 'text'


def _decision_item(item):
    outputs = (item.get('architecture') or {}).get('output_modalities')
    return (isinstance(outputs, list) and 'decisions' in outputs) or item.get('id') in KNOWN_DECISION_MODELS


def _model_row(item):
    pricing = item.get('pricing') or {}
    decision = _decision_item(item)
    prompt, completion = _price(pricing.get('prompt')), _price(pricing.get('completion'))
    if decision and prompt is not None and completion is None:
        completion = 0.0  # decision models bill input tokens
    known = prompt is not None and completion is not None
    supported = item.get('supported_parameters')
    return {'id': item['id'], 'name': item.get('name') or item['id'], 'context_length': item.get('context_length'),
            'pricing': {'prompt': prompt, 'completion': completion} if known else None, 'price_varies': not known,
            'json_mode': 'response_format' in supported if isinstance(supported, list) else None,
            'kind': 'decision' if decision else 'text'}


def load_models(refresh=False):
    """OpenRouter's public text and decision models, cached for an hour; a failed refresh keeps serving the last good list."""
    with _models_lock:
        fresh = _models_cache['models'] is not None and time.time() - _models_cache['at'] < MODELS_TTL
        if fresh and not refresh:
            return _models_cache['models'], None
        try:
            status, data = http_json('GET', MODELS_URL, timeout=20)
            if status != 200 or not isinstance(data.get('data'), list):
                raise TransportError(f'HTTP {status}')
            items = list(data['data'])
            try:  # decision models may only be listed when asked for by output modality
                extra_status, extra = http_json('GET', DECISION_MODELS_URL, timeout=20)
                if extra_status == 200 and isinstance(extra.get('data'), list):
                    items += extra['data']
            except TransportError:
                pass
            rows = {}
            for item in items:
                if isinstance(item, dict) and item.get('id') and item['id'] not in rows and (_text_model(item) or _decision_item(item)):
                    rows[item['id']] = _model_row(item)
            models = list(rows.values())
            models.sort(key=lambda row: row['name'].lower())
            _models_cache.update(at=time.time(), models=models, error=None)
            return models, None
        except TransportError as exc:
            error = f'The OpenRouter model list could not be loaded ({exc}).'
            _models_cache['error'] = error
            return _models_cache['models'] or [], error


def preset_rows(models):
    by_id = {row['id']: row for row in models}
    rows = []
    for preset in PRESETS:
        found = by_id.get(preset['id'])
        rows.append({**(found or {'id': preset['id'], 'context_length': None, 'pricing': None, 'price_varies': True, 'json_mode': None,
                                  'kind': 'decision' if is_known_decision(preset['id']) else 'text'}),
                     'name': preset['name'], 'preset': True, 'listed': bool(found), 'default': preset['id'] == DEFAULT_MODEL})
    return rows


def model_info(model):
    models, _ = load_models()
    for row in preset_rows(models) + models:
        if row['id'] == model:
            return row
    return {'id': model, 'name': model, 'pricing': None, 'price_varies': True, 'json_mode': None, 'listed': False,
            'kind': 'decision' if is_known_decision(model) else 'text'}


def is_known_decision(model):
    return model in KNOWN_DECISION_MODELS or model.lstrip('~') in KNOWN_DECISION_MODELS


def is_decision_model(model):
    return is_known_decision(model) or model_info(model).get('kind') == 'decision'


def prompt_version_for(model):
    return DECISION_PROMPT_VERSION if is_decision_model(model) else PROMPT_VERSION


def model_label(model):
    return next((preset['name'] for preset in PRESETS if preset['id'] == model), model)


@router.get('/models')
def list_models(refresh: bool = False):
    models, error = load_models(refresh)
    with connect() as conn:
        last = conn.execute("SELECT ai_review_model FROM xm.app_preferences WHERE company_id=current_setting('xm.workspace_id')").fetchone()
    return {'presets': preset_rows(models), 'models': models, 'default_model': DEFAULT_MODEL,
            'last_model': (last or {}).get('ai_review_model') or DEFAULT_MODEL, 'error': error}


# ---------------------------------------------------------------- prompt
SYSTEM_PROMPT = """You are Jev, a senior property-matching reviewer for real-estate agents in Surabaya, Indonesia. Agents post buyer requests and property listings in WhatsApp groups, mostly in Indonesian with slang and abbreviations. A rule engine has paired one BUYER REQUEST with one PROPERTY LISTING. Decide whether this pair is worth recommending to the agent: could the listing realistically satisfy what the buyer asked for? Judge only the two texts you are given. The rule engine's parsed fields and explanation are hints and may be wrong; always check them against the texts.

Check in this order (a clear failure on an earlier point is enough to reject):
1. Purpose first. beli / cari / dicari / butuh / mau beli / investasi / WTB = wants to buy. sewa / kontrak / renter / for rent / lease / disewakan = rent. A buyer who wants to buy needs a listing for sale (dijual / jual / for sale); a renter needs a listing for rent. A listing for "jual / sewa" fits both.
2. Unit type. rumah (house), apartemen / apartment / apt with unit types studio, 1BR, 2BR, 3BR (the unit type must match when the buyer names one), ruko / rukan (shophouse), gudang (warehouse), tanah / kavling / lahan (land), kantor / office, pabrik (factory), kost, villa. A house is not a ruko; land is not a house.
3. Area, at the level the buyer named it: a region (Surabaya Barat / Timur / Utara / Selatan / Pusat), a district or street area, or a cluster / development (for example Citraland, Pakuwon City, Graha Family). A cluster inside the named region fits; a different cluster does not fit a buyer who named one specific cluster. "only / hanya / saja / harus" makes the area strict. "❌", "jangan", "tidak mau", "no", "kecuali", "selain" mark exclusions: a listing in an excluded area is a reject. Nearby is NOT equal: a neighbouring area or "dekat X" does not satisfy a buyer who named X.
4. Price and budget. "M" / "milyar" / "miliar" = billion rupiah, "jt" / "juta" = million, "rb" = thousand. "1M-an" means about 0.9 to 1.49 billion (likewise "2M-an" about 1.9 to 2.49 billion). "max / maks" is a ceiling. Hot band: listing price 80-100 % of the budget. Warm: within about plus or minus 15 % of the budget. Clearly above the budget is a reject; below the budget is fine unless the buyer set a minimum. A price per m2 (/m2, per meter) must be multiplied by the land area before comparing. For rent compare like with like: per year (/th, /thn, per tahun) versus per month (/bln, per bulan); one year = 12 months. "Nego" does not change the asking price.
5. Sizes. LT = land area (luas tanah), LB = building area (luas bangunan), in m2. Dimensions like 10x20 mean 200 m2. A listing clearly smaller than a minimum the buyer stated is a reject.
6. Legal status. SHM (freehold) is not Surat Ijo (Surabaya municipal land lease). SHMRS / strata title for apartments. PPJB is a sale agreement, not a certificate. KPR / KPA (mortgage) needs a bankable title (SHM, HGB or SHMRS), not Surat Ijo, Petok D or PPJB only.
7. Other requirements the buyer states: apartment tower letter (Tower A is not Tower B), floor level (lantai rendah / tinggi), furnishing (kosongan / unfurnished versus furnished / full furnish / semi furnish), building condition ("hitung tanah", rumah tua, hancur = land value only; baru, gress, siap huni, minimalis = good condition), lease length (for example minimal 2 tahun), facing (hadap timur / utara / selatan / barat), bedrooms (KT), bathrooms (KM), carport, road width.
8. One WhatsApp bubble may contain several properties or requests. Judge only the part you are given and ignore the others.

Decisions:
- "recommend": purpose, unit type and area fit, price and sizes are within reach, and nothing the buyer insisted on is contradicted.
- "reject": at least one hard requirement is clearly contradicted (purpose, unit type, area outside or excluded, price far outside the budget, a required legal status, tower or unit type).
- "unsure": important information is missing or ambiguous, so an agent must check by hand.
"confidence" is your probability (0 to 1) that the decision is right.

Answer with ONE JSON object and nothing else:
{"decision":"recommend|reject|unsure","confidence":0.0,"reasons":["..."],"evidence":{"buyer":["..."],"listing":["..."]},"issues":["..."]}
- reasons: 1 to 5 short sentences in Indonesian (Bahasa Indonesia), most important first, for example "Lokasi sama: Citraland", "Harga 1,8 M masuk budget 2 M", "Buyer cari sewa, listing dijual".
- evidence.buyer and evidence.listing: short phrases copied EXACTLY (same spelling) from the buyer text and from the listing text that support the reasons. Do not paraphrase. At most 6 each.
- issues: short Indonesian notes on what an agent should double-check (may be empty).
Never invent facts that are not in the texts."""

HINT_FIELDS = ('transaction_type', 'categories', 'locations', 'strict_locations', 'exclusions', 'land_area_min', 'land_area_max',
               'building_area_min', 'building_area_max', 'price_min', 'price_max', 'price_basis', 'rent_price_min',
               'rent_price_max', 'rent_price_basis', 'facing', 'requirements')


def prompt_text(row):
    """The part's own text without the sender's signature (phones and names stay out of the prompt)."""
    count = int(row.get('segment_count') or 1)
    if count > 1 and row.get('segment_text'):
        return row['segment_text'].strip()
    text = clean_text(row.get('raw_text') or row.get('normalized_text') or '')
    return split_contact(text)[0].strip() or text


def parsed_hints(row):
    """The rule engine's reading of one side, without empty fields (numbers as plain JSON numbers)."""
    hints = {}
    for field in HINT_FIELDS:
        value = row.get(field)
        if value in (None, '', [], 'unknown'):
            continue
        hints[field] = float(value) if isinstance(value, Decimal) else value
    if row.get('location_strict'):
        hints['location_strict'] = True
    return hints


def input_hash(buyer_text, listing_text):
    return hashlib.sha256(json.dumps([buyer_text, listing_text], ensure_ascii=False).encode()).hexdigest()


def user_message(item):
    hints = item['hints'] or {}
    direction = 'Buyer -> Listing (finding listings for this buyer)' if item['direction'] == 'buyer' \
        else 'Listing -> Buyer (finding buyers for this listing)'
    explanation = '\n'.join(f'- {line}' for line in hints.get('explanation') or []) or '- (none)'
    return (f"Direction: {direction}\n"
            f"Rule engine status: {hints.get('status') or 'unknown'} (score {hints.get('score')})\n\n"
            f"BUYER REQUEST:\n<<<\n{item['buyer_text']}\n>>>\n\n"
            f"PROPERTY LISTING:\n<<<\n{item['listing_text']}\n>>>\n\n"
            "Rule engine hints (may be wrong):\n"
            f"Buyer parsed: {json.dumps(hints.get('buyer') or {}, ensure_ascii=False, default=str)}\n"
            f"Listing parsed: {json.dumps(hints.get('listing') or {}, ensure_ascii=False, default=str)}\n"
            f"Engine explanation:\n{explanation}\n\n"
            "Return only the JSON object.")


def messages_for(item):
    return [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': user_message(item)}]


def approx_tokens(text):
    return max(1, round(len(text) / 3.6))


SYSTEM_TOKENS = approx_tokens(SYSTEM_PROMPT)


# ---------------------------------------------------------------- decision models
STATE_TEXT_LIMIT = 6000  # characters per part text; keeps a request well under ~32 KB
ASPECTS = (  # (question, Indonesian label used in the reasons)
    ('purpose_match', 'Tujuan'), ('unit_match', 'Jenis unit'), ('area_match', 'Area'), ('price_match', 'Harga'),
    ('size_match', 'Ukuran'), ('condition_match', 'Kondisi'))


def _noul(instructions, true, false):
    return {'type': 'noul', 'instructions': instructions, 'criteria': {'true': true, 'false': false}}


DECISION_QUESTIONS = {
    'recommend': _noul('Should this listing be recommended to this buyer as a real match?',
                       'Purpose (buy/rent), unit type, area and budget fit, and the listing has nothing the buyer refuses.',
                       'There is at least one clear conflict (purpose, unit type, area, budget or something the buyer refuses).'),
    'purpose_match': _noul('Does the listing purpose (for sale / for rent) fit what the buyer wants (buy / rent)?',
                           'Buyer wants to buy and the listing is for sale, or wants to rent and it is for rent (a "jual/sewa" listing fits both).',
                           'Buy versus rent conflict.'),
    'unit_match': _noul('Is the listing the property type the buyer asked for (house, apartment and its unit type, ruko, land, warehouse, office...)?',
                        'Same property type (and the same apartment unit type when the buyer named one).',
                        'Different property type or apartment unit type.'),
    'area_match': _noul("Is the listing in the buyer's requested area, at the level the buyer named it (region, district, or a specific cluster), and outside any area the buyer excludes?",
                        'The listing is inside the requested area and not in an excluded area.',
                        'The listing is in another area, only nearby, or in an area the buyer excludes.'),
    'price_match': _noul("Is the listing price within the buyer's budget (sale versus rent compared like with like, per year versus per month, a price per m2 multiplied by the land area)?",
                         'Within the budget, or the buyer gave no budget.',
                         'Clearly above the budget, or a rent price against a sale budget (or the reverse).'),
    'size_match': _noul('Do the land area, building area and unit size fit what the buyer requested?',
                        'They fit, or the buyer did not request a size.',
                        'Clearly smaller (or otherwise outside) what the buyer requested.'),
    'condition_match': _noul('Do the legal status, furnishing, building condition, tower and floor fit what the buyer requested?',
                             'They fit, or the buyer did not request any of them.',
                             'At least one of them contradicts what the buyer requested.'),
}


def _trim(text):
    text = (text or '').strip()
    return text if len(text) <= STATE_TEXT_LIMIT else text[:STATE_TEXT_LIMIT] + ' …'


def decision_state(item):
    hints = item.get('hints') or {}
    return {'buyer_request': _trim(item['buyer_text']), 'listing': _trim(item['listing_text']),
            'direction': 'finding listings for this buyer' if item['direction'] == 'buyer' else 'finding buyers for this listing',
            'engine_parsed': {'buyer': hints.get('buyer') or {}, 'listing': hints.get('listing') or {}},
            'engine_notes': list(hints.get('explanation') or [])[:12]}


def decision_payload(model, item):
    return {'model': model, 'state': decision_state(item), 'questions': DECISION_QUESTIONS}


def decision_tokens(item):
    """Input tokens of one decisions request (state + questions, about 4 characters a token)."""
    text = json.dumps({'state': decision_state(item), 'questions': DECISION_QUESTIONS}, ensure_ascii=False, default=str)
    return max(1, round(len(text) / 4))


def _probability(answer):
    """P(true) of a noul answer, else None."""
    if not isinstance(answer, dict):
        return None
    value = answer.get('noul')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return min(1.0, max(0.0, float(value)))


def decision_from_answers(answers):
    """The decisions response's answers -> a review (decision, confidence, Indonesian reasons, per-aspect probabilities)."""
    if not isinstance(answers, dict):
        raise ValueError('no answers')
    recommend = _probability(answers.get('recommend'))
    if recommend is None:
        raise ValueError('"recommend" has no probability')
    decision = 'recommend' if recommend >= RECOMMEND_AT else 'reject' if recommend < REJECT_AT else 'unsure'
    probabilities = {'recommend': round(recommend, 4)}
    bad, doubtful, good = [], [], []
    for name, label in ASPECTS:
        value = _probability(answers.get(name))
        if value is None:
            continue
        probabilities[name] = round(value, 4)
        if value >= RECOMMEND_AT:
            good.append(f'{label} cocok')
        elif value < REJECT_AT:
            bad.append(f'{label} tidak cocok')
        else:
            doubtful.append(f'{label} meragukan')
    reasons = bad + doubtful + good
    if not reasons:
        reasons = ['Cocok secara umum' if decision == 'recommend' else 'Tidak cocok secara umum' if decision == 'reject' else 'Perlu dicek manual']
    issues = [f'Cek {text.split(" meragukan")[0].lower()} secara manual' for text in doubtful]
    return {'decision': decision, 'confidence': round(recommend, 4), 'reasons': reasons, 'issues': issues,
            'evidence': {'buyer': [], 'listing': []}, 'probabilities': probabilities}


# ---------------------------------------------------------------- answer validation
def find_phrase(text, phrase):
    """The exact substring of ``text`` that ``phrase`` names (case and spacing tolerant), else None."""
    words = str(phrase or '').split()
    if not words or len(' '.join(words)) < 2:
        return None
    found = re.search(r'\s+'.join(re.escape(word) for word in words), text or '', re.I)
    return found.group(0) if found else None


def _strings(value, field, limit, length):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f'"{field}" must be a list of strings')
    return [item.strip()[:length] for item in value if item.strip()][:limit]


def parse_answer(content, buyer_text, listing_text):
    """The model's JSON as a validated review. Raises ValueError with a short reason when anything is off."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError('empty answer')
    text = content.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f'not valid JSON ({exc.msg})') from exc
    if not isinstance(data, dict):
        raise ValueError('the answer is not a JSON object')
    decision = data.get('decision')
    if not isinstance(decision, str) or decision.strip().lower() not in DECISIONS:
        raise ValueError('"decision" must be recommend, reject or unsure')
    confidence = data.get('confidence')
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError('"confidence" must be a number from 0 to 1')
    reasons = _strings(data.get('reasons'), 'reasons', 6, 300)
    if not reasons:
        raise ValueError('"reasons" is empty')
    issues = _strings(data.get('issues'), 'issues', 6, 300)
    evidence = data.get('evidence')
    evidence = {} if evidence is None else evidence
    if not isinstance(evidence, dict):
        raise ValueError('"evidence" must be an object')
    phrases = {}
    for side, source in (('buyer', buyer_text), ('listing', listing_text)):
        kept = []
        for phrase in _strings(evidence.get(side), f'evidence.{side}', 12, 160):
            exact = find_phrase(source, phrase)
            if exact and exact not in kept:
                kept.append(exact)
        phrases[side] = kept[:8]
    return {'decision': decision.strip().lower(), 'confidence': round(float(confidence), 4), 'reasons': reasons,
            'issues': issues, 'evidence': phrases}


def orient(evidence, direction):
    """{'buyer','listing'} phrases -> {'source','target'} of a row in ``direction``."""
    buyer, listing = evidence.get('buyer') or [], evidence.get('listing') or []
    return {'source': buyer, 'target': listing} if direction == 'buyer' else {'source': listing, 'target': buyer}


def unorient(evidence, direction):
    source, target = evidence.get('source') or [], evidence.get('target') or []
    return {'buyer': source, 'listing': target} if direction == 'buyer' else {'buyer': target, 'listing': source}


# ---------------------------------------------------------------- scope and candidates
class ReviewScope(MatchingPeriods):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(default=DEFAULT_MODEL, max_length=200)
    key_id: uuid.UUID | None = None
    directions: Literal['property', 'buyer', 'both'] = 'both'
    statuses: list[Literal['hot', 'warm', 'custom']] = Field(default_factory=lambda: list(STATUS_ORDER), min_length=1, max_length=3)
    group_keys: list[Annotated[str, Field(max_length=1000)]] | None = Field(default=None, max_length=500)
    max_pairs: int = Field(default=150, ge=1, le=MAX_PAIRS)

    @model_validator(mode='before')
    @classmethod
    def default_windows(cls, values):
        if not isinstance(values, dict):
            return values
        values = dict(values)
        today = datetime.now(WIB).date()
        # Explicit empty strings mean all dates. Preserve custom and legacy windows.
        for kind, start in (('buyer', today - timedelta(days=1)), ('listing', shift_month(today, -3))):
            if not any(key in values for key in (kind + '_date_from', kind + '_date_to', 'date_from', 'date_to')):
                values[kind + '_date_from'] = start.isoformat()
                values[kind + '_date_to'] = today.isoformat()
        return values

    @field_validator('model')
    @classmethod
    def model_value(cls, value):
        value = (value or '').strip()
        if not MODEL_ID.fullmatch(value):
            raise ValueError('Choose a valid model.')
        return value

    def chosen(self):
        return [status for status in STATUS_ORDER if status in self.statuses]

    def direction_list(self):
        return ['buyer', 'property'] if self.directions == 'both' else [self.directions]

    def stored(self):
        return self.model_dump(mode='json', exclude={'model', 'key_id'})


def _part(row):
    return (row.get('segment_text') or row.get('raw_text') or '').strip()


def collect_candidates(conn, scope):
    """(group_by, deduplicated pairs in review order, number of pairs found, scan truncated?)."""
    from export_all import Filters, scope as export_scope
    from report import pair_status
    group_by = company_row(conn)['listing_group_by'] or 'sender'
    if not workspace_cache.ready(conn):
        return group_by, [], 0, False
    chosen = scope.chosen()
    wanted = set(scope.group_keys) if scope.group_keys is not None else None
    periods = scope.model_dump(include=set(MatchingPeriods.model_fields))
    found, truncated = [], False
    for direction in scope.direction_list():
        filters = Filters(direction=direction, statuses=','.join(chosen), stock_status='ready', group_by=group_by, **periods)
        _, eligible, params = export_scope(conn, None, filters)
        sources = conn.execute('WITH ' + eligible + ' SELECT id,group_key FROM eligible ORDER BY sent_at DESC NULLS LAST,id', params).fetchall()
        keys = {row['id']: row['group_key'] for row in sources if wanted is None or row['group_key'] in wanted}
        _, (target_clause, target_params) = filters.windows(direction)
        ids = list(keys)
        for start in range(0, len(ids), 100):
            groups = workspace_cache.recommendations(conn, direction, ids[start:start + 100], target_clause=target_clause,
                                                     target_params=target_params)['groups']
            for group in groups:
                source = group['source']
                for rank, target in enumerate(group['recommendations']):
                    status = pair_status(target)
                    if status in chosen:
                        found.append({'direction': direction, 'source': source, 'target': target, 'status': status, 'rank': rank,
                                      'group_key': keys.get(source['id'], '')})
            if len(found) >= SCAN_LIMIT:
                truncated = True
                break
        if truncated:
            break
    # Hot first, then Warm, then Custom; newest source first; the list order of each source's matches.
    found.sort(key=lambda c: (STATUS_ORDER.index(c['status']), -(c['source']['sent_at'].timestamp() if c['source'].get('sent_at') else float('-inf')),
                              c['direction'] != 'property', str(c['source']['id']), c['rank']))
    seen, pairs = set(), []
    for candidate in found:
        key = (candidate['direction'], _part(candidate['source']), _part(candidate['target']))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(candidate)
    return group_by, pairs, len(pairs), truncated


def review_item(candidate, group_by, position, model):
    direction, source, target = candidate['direction'], candidate['source'], candidate['target']
    buyer, listing = (source, target) if direction == 'buyer' else (target, source)
    buyer_text, listing_text = prompt_text(buyer), prompt_text(listing)
    hints = {'status': candidate['status'], 'score': float(target.get('score') or 0), 'explanation': list(target.get('explanation') or []),
             'buyer': parsed_hints(buyer), 'listing': parsed_hints(listing)}
    key = candidate['group_key']
    return {'position': position, 'direction': direction, 'source_document_id': source['id'], 'target_document_id': target['id'],
            'source_entity_id': source.get('entity_id'), 'target_entity_id': target.get('entity_id'),
            'buyer_entity_id': buyer.get('entity_id'), 'listing_entity_id': listing.get('entity_id'),
            'source_public_id': source.get('public_id'), 'target_public_id': target.get('public_id'),
            'group_key': key, 'group_title': workspace_cache.group_title(group_by, key, source.get('contact_name')),
            'engine_status': candidate['status'], 'engine_score': float(target.get('score') or 0),
            'engine_explanation': list(target.get('explanation') or []), 'buyer_text': buyer_text, 'listing_text': listing_text,
            'hints': hints, 'model': model, 'prompt_version': PROMPT_VERSION, 'input_hash': input_hash(buyer_text, listing_text)}


def plan_run(conn, scope):
    """(items to review, estimate) for ``scope``."""
    group_by, pairs, available, truncated = collect_candidates(conn, scope)
    version = prompt_version_for(scope.model)
    decision = version == DECISION_PROMPT_VERSION
    items = [review_item(candidate, group_by, index, scope.model) for index, candidate in enumerate(pairs[:scope.max_pairs])]
    for item in items:
        item['prompt_version'] = version
    hashes = list(dict.fromkeys(item['input_hash'] for item in items))
    cached = set()
    if hashes:
        cached = {row['input_hash'] for row in conn.execute('''SELECT DISTINCT input_hash FROM xm.ai_reviews
          WHERE company_id=current_setting('xm.workspace_id') AND input_hash=ANY(%s) AND model=%s AND prompt_version=%s
            AND status='done' AND error IS NULL''', (hashes, scope.model, version)).fetchall()}
    first = {}
    for item in items:
        first.setdefault(item['input_hash'], item)
    calls = [item for key, item in first.items() if key not in cached]
    if decision:  # no text output: input tokens only
        tokens_in, tokens_out = sum(decision_tokens(item) for item in calls), 0
    else:
        tokens_in = sum(SYSTEM_TOKENS + approx_tokens(user_message(item)) for item in calls)
        tokens_out = OUTPUT_TOKENS_GUESS * len(calls)
    info = model_info(scope.model)
    pricing = info.get('pricing')
    cost = round(tokens_in * pricing['prompt'] + tokens_out * pricing['completion'], 6) if pricing else None
    groups = {}
    for item in items:
        group = groups.setdefault(item['group_key'], {'key': item['group_key'], 'title': item['group_title'], 'pairs': 0})
        group['pairs'] += 1
    estimate = {
        'model': scope.model, 'model_label': model_label(scope.model), 'prompt_version': version, 'group_by': group_by,
        'model_kind': 'decision' if decision else 'text',
        'pairs': len(items), 'available': available, 'capped': available > len(items), 'scan_truncated': truncated,
        'unique_inputs': len(first), 'cached': len(first) - len(calls), 'to_call': len(calls),
        'tokens_in': tokens_in, 'tokens_out': tokens_out, 'cost': cost, 'price_varies': pricing is None, 'pricing': pricing,
        'by_status': {status: sum(1 for item in items if item['engine_status'] == status) for status in STATUS_ORDER},
        'by_direction': {direction: sum(1 for item in items if item['direction'] == direction) for direction in ('property', 'buyer')},
        'groups': sorted(groups.values(), key=lambda group: (-group['pairs'], group['title'] or '')),
    }
    return items, estimate, info


@router.post('/review-runs/estimate')
def estimate_run(scope: ReviewScope):
    with connect() as conn:
        _, estimate, _ = plan_run(conn, scope)
    return estimate


ITEM_COLUMNS = ('position', 'direction', 'source_document_id', 'target_document_id', 'source_entity_id', 'target_entity_id',
                'buyer_entity_id', 'listing_entity_id', 'source_public_id', 'target_public_id', 'group_key', 'group_title',
                'engine_status', 'engine_score', 'engine_explanation', 'buyer_text', 'listing_text', 'hints', 'model',
                'prompt_version', 'input_hash')
JSON_COLUMNS = {'engine_explanation', 'hints'}


@router.post('/review-runs', status_code=202)
def create_run(scope: ReviewScope, request: Request):
    with connect() as conn:
        if scope.key_id and current_user(request)['role'] != 'admin':
            raise HTTPException(403, 'Only the platform administrator can choose an API key.')
        key_id = resolve_key_id(conn, scope.key_id)
        items, estimate, info = plan_run(conn, scope)
        if not items:
            raise HTTPException(400, 'Nothing to review for these filters. Widen the dates or choose more statuses.')
        run_id = uuid.uuid4()
        payload = {**scope.stored(), 'json_mode': info.get('json_mode') is not False, 'pricing': info.get('pricing'),
                   'model_label': model_label(scope.model), 'kind': estimate['model_kind']}
        conn.execute('''INSERT INTO xm.ai_review_runs(id,company_id,created_by,key_id,model,prompt_version,group_by,payload,estimate)
          VALUES(%s,current_setting('xm.workspace_id'),%s,%s,%s,%s,%s,%s,%s)''',
          (run_id, current_user(request)['id'], key_id, scope.model, estimate['prompt_version'], estimate['group_by'], json.dumps(payload),
           json.dumps(estimate, default=str)))
        columns = ('run_id', 'company_id', *ITEM_COLUMNS)
        with conn.cursor() as cursor:
            cursor.executemany(
                f"INSERT INTO xm.ai_reviews({','.join(columns)}) VALUES (%s,current_setting('xm.workspace_id'),{','.join(['%s'] * len(ITEM_COLUMNS))})",
                [(run_id, *[json.dumps(item[c], default=str) if c in JSON_COLUMNS else item[c] for c in ITEM_COLUMNS]) for item in items])
        conn.execute("UPDATE xm.app_preferences SET ai_review_model=%s WHERE company_id=current_setting('xm.workspace_id')", (scope.model,))
        conn.commit()
        return run_summary(conn, run_id)


# ---------------------------------------------------------------- runs, results, overrides
RUN_SQL = '''SELECT r.id,r.model,r.prompt_version,r.group_by,r.payload,r.estimate,r.status,r.error,r.created_at,r.started_at,
    r.finished_at,r.key_id,k.label key_label,k.last4 key_last4,coalesce(nullif(u.display_name,''),u.email) created_by_name,c.*
  FROM xm.ai_review_runs r LEFT JOIN xm.ai_keys k ON k.id=r.key_id LEFT JOIN xm.users u ON u.id=r.created_by
  CROSS JOIN LATERAL (SELECT count(*) total,count(*) FILTER (WHERE a.status='done') done,
      count(*) FILTER (WHERE a.status='failed') failed,count(*) FILTER (WHERE a.status='pending') pending,
      count(*) FILTER (WHERE a.cached) cached,
      count(*) FILTER (WHERE a.status='done' AND coalesce(a.override_decision,a.decision)='recommend') recommend,
      count(*) FILTER (WHERE a.status='done' AND coalesce(a.override_decision,a.decision)='reject') reject,
      count(*) FILTER (WHERE a.status='done' AND coalesce(a.override_decision,a.decision)='unsure') unsure,
      count(*) FILTER (WHERE a.override_decision IS NOT NULL) overrides,
      coalesce(sum(a.cost),0) cost,bool_or(a.cost_estimated) cost_estimated,
      coalesce(sum(a.tokens_in),0) tokens_in,coalesce(sum(a.tokens_out),0) tokens_out
    FROM xm.ai_reviews a WHERE a.run_id=r.id) c
  WHERE r.company_id=current_setting('xm.workspace_id')'''


def _summary(row):
    row = dict(row)
    payload = row.get('payload') or {}
    row['model_label'] = payload.get('model_label') or model_label(row['model'])
    row['key_masked'] = ('••••' + row['key_last4']) if row.get('key_last4') else None
    row['cost'] = float(row['cost'] or 0)
    row['cost_estimated'] = bool(row.get('cost_estimated'))
    for name in ('total', 'done', 'failed', 'pending', 'cached', 'recommend', 'reject', 'unsure', 'overrides', 'tokens_in', 'tokens_out'):
        row[name] = int(row.get(name) or 0)
    return row


def run_summary(conn, run_id):
    row = conn.execute(RUN_SQL + ' AND r.id=%s', (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Review run not found.')
    return _summary(row)


@router.get('/review-runs')
def list_runs(limit: int = 30):
    with connect() as conn:
        rows = conn.execute(RUN_SQL + ' ORDER BY r.created_at DESC LIMIT %s', (min(max(limit, 1), 100),)).fetchall()
    return {'runs': [_summary(row) for row in rows]}


@router.get('/review-runs/{run_id}')
def get_run(run_id: uuid.UUID):
    with connect() as conn:
        return run_summary(conn, run_id)


@router.post('/review-runs/{run_id}/cancel')
def cancel_run(run_id: uuid.UUID):
    with connect() as conn:
        row = conn.execute("""UPDATE xm.ai_review_runs SET status='cancelled',finished_at=now()
          WHERE id=%s AND company_id=current_setting('xm.workspace_id') AND status IN ('queued','running') RETURNING id""", (run_id,)).fetchone()
        if not row and not conn.execute("SELECT 1 FROM xm.ai_review_runs WHERE id=%s AND company_id=current_setting('xm.workspace_id')",
                                        (run_id,)).fetchone():
            raise HTTPException(404, 'Review run not found.')
        conn.commit()
        return run_summary(conn, run_id)


RESULT_FIELDS = '''a.id,a.position,a.direction,a.source_entity_id,a.target_entity_id,a.buyer_entity_id,a.listing_entity_id,
  a.source_public_id,a.target_public_id,a.group_key,a.group_title,a.engine_status,a.engine_score,a.engine_explanation,
  a.buyer_text,a.listing_text,a.status,a.cached,a.decision,a.confidence,a.reasons,a.evidence,a.issues,a.tokens_in,a.tokens_out,
  a.cost,a.cost_estimated,a.error,a.override_decision,a.override_at,coalesce(nullif(u.display_name,''),u.email) override_by_name,
  coalesce(a.override_decision,a.decision) effective_decision,a.model,a.reviewed_at,a.probabilities'''


def _result(row):
    row = dict(row)
    for name in ('confidence', 'cost', 'engine_score'):
        row[name] = float(row[name]) if row.get(name) is not None else None
    return row


@router.get('/review-runs/{run_id}/results')
def run_results(run_id: uuid.UUID, decision: Literal['all', 'recommend', 'reject', 'unsure', 'failed', 'pending'] = 'all'):
    with connect() as conn:
        run = run_summary(conn, run_id)
        clause, params = '', [run_id]
        if decision in DECISIONS:
            clause = " AND a.status='done' AND coalesce(a.override_decision,a.decision)=%s"
            params.append(decision)
        elif decision in ('failed', 'pending'):
            clause = ' AND a.status=%s'
            params.append(decision)
        rows = conn.execute(f'''SELECT {RESULT_FIELDS} FROM xm.ai_reviews a LEFT JOIN xm.users u ON u.id=a.override_by
          WHERE a.run_id=%s AND a.company_id=current_setting('xm.workspace_id'){clause} ORDER BY a.position''', params).fetchall()
    groups = {}
    for row in rows:
        group = groups.setdefault(row['group_key'], {'key': row['group_key'], 'title': row['group_title'], 'items': [],
                                                     'counts': {name: 0 for name in (*DECISIONS, 'failed', 'pending')}})
        group['items'].append(_result(row))
        bucket = row['effective_decision'] if row['status'] == 'done' else row['status']
        group['counts'][bucket] = group['counts'].get(bucket, 0) + 1
    ordered = sorted(groups.values(), key=lambda group: (-len(group['items']), group['title'] or ''))
    return {'run': run, 'groups': ordered, 'total': len(rows)}


class Override(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['recommend', 'reject', 'unsure'] | None = None


@router.put('/reviews/{review_id}/override')
def override_review(review_id: int, payload: Override, request: Request):
    """An admin's decision for a pair. It applies to both directions of the same buyer/listing pair in the run
    (the pair appears once under the listing's person and once under the buyer's); ``null`` returns to Jev's decision."""
    user = current_user(request)
    with connect() as conn:
        row = conn.execute('''SELECT id,run_id,buyer_entity_id,listing_entity_id FROM xm.ai_reviews
          WHERE id=%s AND company_id=current_setting('xm.workspace_id') AND status='done' ''', (review_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Only a finished review can be overridden.')
        changed = conn.execute('''UPDATE xm.ai_reviews SET override_decision=%s,override_by=%s,
            override_at=CASE WHEN %s::text IS NULL THEN NULL ELSE now() END
          WHERE company_id=current_setting('xm.workspace_id') AND status='done' AND run_id=%s
            AND (id=%s OR (buyer_entity_id=%s AND listing_entity_id=%s)) RETURNING id''',
          (payload.decision, user['id'] if payload.decision else None, payload.decision, row['run_id'], review_id,
           row['buyer_entity_id'], row['listing_entity_id'])).fetchall()
        conn.commit()
        result = conn.execute(f'''SELECT {RESULT_FIELDS} FROM xm.ai_reviews a LEFT JOIN xm.users u ON u.id=a.override_by
          WHERE a.id=%s''', (review_id,)).fetchone()
    return {**_result(result), 'also_updated': [item['id'] for item in changed if item['id'] != review_id]}


class PairRef(BaseModel):
    buyer_entity_id: uuid.UUID
    listing_entity_id: uuid.UUID


class Lookup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pairs: list[PairRef] = Field(max_length=500)


@router.post('/reviews/lookup')
def lookup_reviews(payload: Lookup):
    """The latest finished review of each pair (newest run first), for the Jev badge on Match cards."""
    if not payload.pairs:
        return {'reviews': []}
    buyers = [str(pair.buyer_entity_id) for pair in payload.pairs]
    listings = [str(pair.listing_entity_id) for pair in payload.pairs]
    with connect() as conn:
        rows = conn.execute('''SELECT DISTINCT ON (a.buyer_entity_id,a.listing_entity_id) a.id,a.run_id,a.buyer_entity_id,
            a.listing_entity_id,a.decision,a.override_decision,coalesce(a.override_decision,a.decision) effective_decision,
            a.confidence,a.reasons,a.model,a.reviewed_at
          FROM xm.ai_reviews a JOIN xm.ai_review_runs r ON r.id=a.run_id
          JOIN unnest(%s::uuid[],%s::uuid[]) p(b,l) ON a.buyer_entity_id=p.b AND a.listing_entity_id=p.l
          WHERE a.company_id=current_setting('xm.workspace_id') AND a.status='done'
          ORDER BY a.buyer_entity_id,a.listing_entity_id,r.created_at DESC,a.override_decision IS NULL,a.id DESC''', (buyers, listings)).fetchall()
    for row in rows:
        row['confidence'] = float(row['confidence']) if row['confidence'] is not None else None
        row['model_label'] = model_label(row['model'])
    return {'reviews': rows}


# ---------------------------------------------------------------- export
def _documents(conn, ids, entities):
    from matchlog import PAIR_FIELDS
    rows = conn.execute(f'''SELECT {PAIR_FIELDS} FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
      LEFT JOIN xm.document_group_members gm ON gm.document_id=d.id LEFT JOIN xm.document_groups g ON g.group_id=gm.group_id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.id=ANY(%s::uuid[])''', ([str(i) for i in ids],)).fetchall()
    by_id = {row['id']: row for row in rows}
    # A rebuilt document keeps its entity: fall back to the entity's current card.
    missing = [str(entity) for doc, entity in entities if doc not in by_id and entity]
    by_entity = {}
    if missing:
        for row in conn.execute(f'''SELECT {PAIR_FIELDS} FROM xm.document_groups g JOIN xm.documents d ON d.id=g.group_id
          JOIN xm.raw_messages r ON r.id=d.raw_message_id
          WHERE g.company_id=current_setting('xm.workspace_id') AND g.entity_id=ANY(%s::uuid[])''', (missing,)).fetchall():
            by_entity[row['entity_id']] = row
    return by_id, by_entity


def approved_pairs(conn, run):
    """[(direction, source, target, position)] of the pairs Jev recommended (or an admin set to recommend)."""
    rows = conn.execute('''SELECT * FROM xm.ai_reviews WHERE run_id=%s AND company_id=current_setting('xm.workspace_id')
      AND status='done' AND coalesce(override_decision,decision)='recommend' ORDER BY position''', (run['id'],)).fetchall()
    refs = [(row['source_document_id'], row['source_entity_id']) for row in rows] + \
           [(row['target_document_id'], row['target_entity_id']) for row in rows]
    by_id, by_entity = _documents(conn, [doc for doc, _ in refs], refs)
    label = run.get('model_label') or model_label(run['model'])
    selected = []
    for row in rows:
        source = by_id.get(row['source_document_id']) or by_entity.get(row['source_entity_id'])
        target = by_id.get(row['target_document_id']) or by_entity.get(row['target_entity_id'])
        if not source or not target:
            continue
        evidence = unorient(row['evidence'] or {}, row['direction'])
        jev = {'model': label, 'confidence': float(row['confidence']) if row['confidence'] is not None else None,
               'reasons': list(row['reasons'] or []), 'issues': list(row['issues'] or []), 'evidence': evidence,
               'override': row['override_decision'] == 'recommend' and row['decision'] != 'recommend'}
        target = {**target, 'status': row['engine_status'], 'score': float(row['engine_score'] or 0),
                  'explanation': list(row['engine_explanation'] or []), 'jev': jev}
        selected.append((row['direction'], dict(source), target, row['position']))
    return selected


@router.get('/review-runs/{run_id}/export')
def export_approved(run_id: uuid.UUID, output: Literal['grouped', 'single'] = 'grouped'):
    from export_all import people_file, selection_people
    with connect() as conn:
        run = run_summary(conn, run_id)
        selected = approved_pairs(conn, run)
        company = company_row(conn)
    if not selected:
        raise HTTPException(404, 'Jev has not recommended any pair in this run yet.')
    group_by = company['listing_group_by'] or 'sender'
    return people_file(selection_people(selected, group_by), group_by, company['company_name'], output, 'Saran-AI-Disetujui')


# ---------------------------------------------------------------- worker
class StopRun(Exception):
    """The whole run must stop (key rejected, no credits, model unusable)."""


class PairFailed(Exception):
    def __init__(self, message, client_error=False):
        super().__init__(message)
        self.client_error = client_error


def _error_text(data, status):
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get('message'):
        return str(error['message'])[:300]
    if isinstance(data, dict) and data.get('_text'):
        return str(data['_text'])[:300]
    return f'HTTP {status}'


def _content(data):
    try:
        content = data['choices'][0]['message'].get('content')
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return content


def call_model(secret, model, item, json_mode=True):
    """One chat completion; returns the parsed response JSON. Retries timeouts, 429 and 5xx with backoff."""
    payload = {'model': model, 'messages': messages_for(item), 'temperature': 0, 'max_tokens': MAX_OUTPUT_TOKENS, 'usage': {'include': True}}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    return post_openrouter(secret, CHAT_URL, payload, 'choices')


def post_openrouter(secret, url, payload, answer_key):
    """POST to OpenRouter; returns the parsed response JSON. Retries timeouts, 429 and 5xx with backoff."""
    headers = {'Authorization': 'Bearer ' + secret, 'HTTP-Referer': APP_URL, 'X-Title': APP_TITLE}
    last = 'no answer'
    for attempt in range(RETRIES + 1):
        if attempt:
            _sleep(RETRY_BASE_SECONDS * 2 ** (attempt - 1))
        try:
            status, data = http_json('POST', url, headers=headers, payload=payload, timeout=REQUEST_TIMEOUT)
        except TransportError as exc:
            last = f'network error: {exc}'
            continue
        error = data.get('error') if isinstance(data, dict) else None
        code = status
        if status == 200 and isinstance(error, dict) and not data.get(answer_key):
            code = error.get('code') if isinstance(error.get('code'), int) else 502
        if code == 401:
            raise StopRun('OpenRouter rejected the API key (401). Check the key in Settings → AI keys, then start a new run.')
        if code == 402:
            raise StopRun('The OpenRouter account has no credits left (402). Add credits, then start a new run.')
        if code == 200:
            return data
        last = f'OpenRouter error {code}: {_error_text(data, code)}'
        if code in (408, 429) or code >= 500:
            continue
        raise PairFailed(last, client_error=True)
    raise PairFailed(last)


def review_one(secret, run, item):
    """{'status','decision',...} for one input; PairFailed / StopRun propagate."""
    payload = run['payload'] or {}
    if str(run.get('prompt_version', '')).startswith('jev-d'):
        return review_decision(secret, run, item)
    data = call_model(secret, run['model'], item, json_mode=payload.get('json_mode', True))
    usage = data.get('usage') or {}
    tokens_in, tokens_out = usage.get('prompt_tokens'), usage.get('completion_tokens')
    cost, estimated = usage.get('cost'), False
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        pricing = payload.get('pricing')
        cost, estimated = None, True
        if pricing:
            cost = (tokens_in or SYSTEM_TOKENS + approx_tokens(user_message(item))) * pricing['prompt'] + \
                   (tokens_out or OUTPUT_TOKENS_GUESS) * pricing['completion']
    result = {'status': 'done', 'tokens_in': tokens_in, 'tokens_out': tokens_out, 'cost': cost, 'cost_estimated': estimated, 'error': None}
    content = _content(data)
    try:
        answer = parse_answer(content, item['buyer_text'], item['listing_text'])
    except ValueError as exc:
        raw = (content or '')[:300] if isinstance(content, str) else ''
        result.update(decision='unsure', confidence=0, reasons=[], issues=[], evidence={'buyer': [], 'listing': []},
                      error=f'Invalid answer from the model: {exc}.' + (f' Raw: {raw}' if raw else ''))
        return result
    result.update(answer)
    return result


def review_decision(secret, run, item):
    """One Decisions API request: typed yes/no answers with probabilities, mapped to a review."""
    data = post_openrouter(secret, DECISIONS_URL, decision_payload(run['model'], item), 'answers')
    usage = data.get('usage') or {}
    tokens_in, tokens_out = usage.get('input_tokens'), usage.get('output_tokens')
    cost, estimated = usage.get('cost'), False
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        pricing = (run['payload'] or {}).get('pricing')
        cost, estimated = ((tokens_in or decision_tokens(item)) * pricing['prompt'] if pricing else None), True
    result = {'status': 'done', 'tokens_in': tokens_in, 'tokens_out': tokens_out, 'cost': cost, 'cost_estimated': estimated, 'error': None}
    try:
        result.update(decision_from_answers(data.get('answers')))
    except ValueError as exc:
        result.update(decision='unsure', confidence=0, reasons=[], issues=[], evidence={'buyer': [], 'listing': []},
                      error=f'Invalid answer from the model: {exc}.')
    return result


def _store(conn, run_id, item_hash, result):
    """Write one answer to every pending row of the run with the same texts (the first carries tokens and cost)."""
    rows = conn.execute("SELECT id,direction FROM xm.ai_reviews WHERE run_id=%s AND input_hash=%s AND status='pending' ORDER BY position",
                        (run_id, item_hash)).fetchall()
    for index, row in enumerate(rows):
        primary = index == 0 and not result.get('cached')
        conn.execute('''UPDATE xm.ai_reviews SET status=%s,decision=%s,confidence=%s,reasons=%s,evidence=%s,issues=%s,
            tokens_in=%s,tokens_out=%s,cost=%s,cost_estimated=%s,error=%s,cached=%s,probabilities=%s,reviewed_at=now() WHERE id=%s''',
            (result['status'], result.get('decision'), result.get('confidence'), json.dumps(result.get('reasons') or []),
             json.dumps(orient(result.get('evidence') or {}, row['direction'])), json.dumps(result.get('issues') or []),
             result.get('tokens_in') if primary else None, result.get('tokens_out') if primary else None,
             (result.get('cost') if primary else 0) if result['status'] == 'done' else None,
             bool(result.get('cost_estimated')) and primary, result.get('error'), not primary,
             json.dumps(result['probabilities']) if result.get('probabilities') is not None else None, row['id']))


def _finish(conn, run_id, status, error=None):
    conn.execute('''UPDATE xm.ai_review_runs SET status=%s,error=coalesce(%s,error),finished_at=now()
      WHERE id=%s AND status IN ('queued','running')''', (status, error, run_id))


def process_run_chunk(run_id, chunk=CHUNK):
    """Review up to ``chunk`` pending pairs of one run (inside its workspace scope)."""
    with connect() as conn:
        run = conn.execute('SELECT * FROM xm.ai_review_runs WHERE id=%s AND company_id=%s', (run_id, workspace_id())).fetchone()
        if not run or run['status'] not in ('queued', 'running'):
            return
        items = conn.execute('''SELECT id,direction,input_hash,buyer_text,listing_text,hints FROM xm.ai_reviews
          WHERE run_id=%s AND status='pending' ORDER BY position LIMIT %s''', (run_id, chunk)).fetchall()
        if not items:
            _finish(conn, run_id, 'completed')
            conn.commit()
            return
        unique = list({item['input_hash']: item for item in reversed(items)}.values())[::-1]
        # Earlier answers for the same texts, model and prompt version are reused without calling the API.
        cached = {row['input_hash']: row for row in conn.execute('''SELECT DISTINCT ON (input_hash) input_hash,direction,decision,
            confidence,reasons,evidence,issues,probabilities FROM xm.ai_reviews WHERE company_id=%s AND input_hash=ANY(%s) AND model=%s
            AND prompt_version=%s AND status='done' AND error IS NULL ORDER BY input_hash,reviewed_at DESC,id DESC''',
            (workspace_id(), [item['input_hash'] for item in unique], run['model'], run['prompt_version'])).fetchall()}
        for key, row in cached.items():
            _store(conn, run_id, key, {'status': 'done', 'decision': row['decision'], 'confidence': row['confidence'],
                                       'reasons': row['reasons'], 'issues': row['issues'], 'cached': True,
                                       'probabilities': row['probabilities'],
                                       'evidence': unorient(row['evidence'] or {}, row['direction']), 'cost': 0})
        conn.execute('UPDATE xm.ai_review_runs SET heartbeat_at=now() WHERE id=%s', (run_id,))
        conn.commit()
        calls = [item for item in unique if item['input_hash'] not in cached]
        if not calls:
            return
        if not run['key_id']:
            _finish(conn, run_id, 'failed', 'The API key of this run was deleted.')
            conn.commit()
            return
        try:
            found = key_secret(conn, run['key_id'], workspace_id())
        except SecretUnavailable as exc:
            _finish(conn, run_id, 'failed', str(exc))
            conn.commit()
            return
        if not found:
            _finish(conn, run_id, 'failed', 'The API key of this run was deleted.')
            conn.commit()
            return
        secret = found[1]
    results, stop, client_errors = {}, None, []
    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)
    futures = {executor.submit(review_one, secret, run, item): item for item in calls}
    try:
        for future, item in futures.items():
            if future.cancelled():  # never sent because the run stopped: the pair stays pending
                continue
            try:
                results[item['input_hash']] = future.result()
            except StopRun as exc:
                stop = str(exc)
                for other in futures:
                    other.cancel()
            except PairFailed as exc:
                results[item['input_hash']] = {'status': 'failed', 'error': str(exc)}
                if exc.client_error:
                    client_errors.append(str(exc))
            except Exception as exc:  # a bug must not leave the pair pending forever
                results[item['input_hash']] = {'status': 'failed', 'error': f'Unexpected error: {type(exc).__name__}'}
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    with connect() as conn:
        for key, result in results.items():
            _store(conn, run_id, key, result)
        conn.execute('UPDATE xm.ai_review_runs SET heartbeat_at=now() WHERE id=%s', (run_id,))
        conn.execute('UPDATE xm.ai_keys SET last_used_at=now() WHERE id=%s', (run['key_id'],))
        if stop:
            _finish(conn, run_id, 'failed', stop)
        elif client_errors and len(client_errors) == len(calls) and len(set(client_errors)) == 1:
            # Every request of the chunk was refused the same way (unknown model, bad request): stop instead of paying for more.
            _finish(conn, run_id, 'failed', client_errors[0])
        conn.commit()


def process_next_review(chunk=CHUNK):
    """Work on the oldest active run for one chunk. Returns False when no run is waiting."""
    with connect() as conn:
        row = conn.execute("""SELECT id,company_id FROM xm.ai_review_runs WHERE status IN ('queued','running')
          ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not row:
            return False
        conn.execute("UPDATE xm.ai_review_runs SET status='running',started_at=coalesce(started_at,now()),heartbeat_at=now() WHERE id=%s",
                     (row['id'],))
        conn.commit()
    with workspace_scope(row['company_id']):
        process_run_chunk(row['id'], chunk)
    return True


def review_loop():
    """The worker's review thread: separate from the import/maintenance loop, so a long run never delays those."""
    poll = float(os.getenv('WORKER_POLL_SECONDS', '2'))
    while True:
        try:
            if process_next_review():
                continue
        except Exception as exc:
            print(f'Jev review chunk failed: {type(exc).__name__}: {exc}', flush=True)
        time.sleep(poll)
