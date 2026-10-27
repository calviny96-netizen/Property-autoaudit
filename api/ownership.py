"""Ownership tiers: whose stock a recommendation is, seen from the person who posted the source.

Every recommendation list (Buyer -> Listing and Listing -> Buyer) is ranked by tier first:

1. ``own``     the target belongs to the source's owner: both share a phone number (``contact_phone`` /
               ``contact_phones``, normalised 62...), or, when either side has no phone, both were sent by the
               same WhatsApp sender. A sender is its number when the author carries one (``"~ Budi, +62 811..."``,
               or a bare number), so two different "Budi"s never count as one; otherwise its name (author without
               the leading "~ " and the trailing number), so a saved contact "Ellen" is the same as
               ``"~ Ellen, +62 812..."``. A side without a phone in its text also owns a target that carries
               its sender's number, and the other way round. Lists that show one copy of a text posted several
               times (the cached Match lists, Recent matches) check every copy's sender, not only the shown one,
               and (v4.10: copies of one content may carry different signatures) every copy's phone numbers.
2. ``team``    the target's phone (any copy's, where copies are checked) is on the company's sales watchlist
               (``xm.tracked_sales``).
3. ``company`` the target's text (raw bubble incl. signature, or normalised text) contains a company keyword
               (``xm.app_preferences.search_terms``, case-insensitive).
4. ``other``   everyone else.

Inside a tier: status (hot, warm, custom), then score (high first), then the most recent posting.
Nothing is hidden and nothing is stored: the tier is computed when a list is read, so watchlist and keyword
changes reorder the lists at once, without reprocessing.

SQL: ``source_sql`` gives the source side once per source (a ``MATERIALIZED`` CTE, never once per row),
``rank_sql`` the tier rank (1-4) of a target row against it and ``copy_senders_sql`` the senders of the other
copies. ``tier`` is the same rule in Python for rows that are already loaded; it sees one copy only. Both must
stay in step (test_ownership_tier checks it).
"""
import re
from dataclasses import dataclass
from datetime import datetime

from parser import normalize_phone
from tenant import workspace_id

TIERS = ('own', 'team', 'company', 'other')
STATUS_ORDER = ('hot', 'warm', 'custom')
RANK_COLUMN = 'ownership_rank'
TIER_FIELD = 'ownership_tier'
# Columns ``source_sql`` exposes for ``rank_sql``.
SOURCE_COLUMNS = ('own_phones', 'own_has_phone', 'own_sender', 'own_number')
MIN_NUMBER_DIGITS = 8  # a sender "number" shorter than this is not a phone number

# Character classes shared by the Python rule and its SQL twin (WhatsApp exports use no-break spaces and
# Unicode hyphens inside numbers).
_WS = '\\s\u00a0\u202f'
_PHONE = '\\+?\\d[\\d\\s().\u00a0\u202f\u2010-\u2013-]{5,}'
_TILDE_RE = '^~\\s*'
_TRAILING_RE = '\\s*,\\s*' + _PHONE + '$'
# The author's number: after a comma at the end ("~ Budi, +62 811-..."), or the whole author (a bare number).
_NUMBER_RE = '(?:^|,)\\s*(?=(?:\\D*\\d){%d})(%s)$' % (MIN_NUMBER_DIGITS, _PHONE)


@dataclass(frozen=True)
class OwnershipRules:
    """The company-level inputs of the tier: sales watchlist phones and company keywords."""
    watchlist: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


def clean_keywords(values):
    return tuple(dict.fromkeys(str(value).strip() for value in values or () if str(value or '').strip()))


def load_rules(conn, company_id=None):
    """Current watchlist and company keywords of a workspace (one small query)."""
    company_id = company_id or workspace_id()
    row = conn.execute(
        '''SELECT coalesce((SELECT array_agg(phone ORDER BY phone) FROM xm.tracked_sales WHERE company_id=%s),'{}'::text[]) AS watchlist,
                  coalesce((SELECT search_terms FROM xm.app_preferences WHERE company_id=%s),'{}'::text[]) AS keywords''',
        (company_id, company_id)).fetchone()
    phones = tuple(dict.fromkeys(normalize_phone(phone) for phone in row['watchlist'] or () if phone))
    return OwnershipRules(tuple(phone for phone in phones if phone), clean_keywords(row['keywords']))


# --------------------------------------------------------------------------- Python rule
_SPACE = re.compile('[' + _WS + ']+')
_TILDE = re.compile(_TILDE_RE)
_TRAILING_PHONE = re.compile(_TRAILING_RE)
_NUMBER = re.compile(_NUMBER_RE)


def _clean_author(author):
    return _TILDE.sub('', _SPACE.sub(' ', str(author or '')).strip(' '))


def sender_key(author):
    """``"~ Ellen, +62 812-3456-7890"`` -> ``"ellen"``; ``""`` when there is no sender."""
    return _TRAILING_PHONE.sub('', _clean_author(author)).strip(' ,').lower()


def sender_number(author):
    """``"~ Ellen, +62 812-3456-7890"`` or ``"+62 812-3456-7890"`` -> ``"6281234567890"``; ``""`` without a number."""
    found = _NUMBER.search(_clean_author(author))
    return normalize_phone(found.group(1)) if found else ''


def phones(row):
    """Every contact number of a document row, normalised (``contact_phone`` plus ``contact_phones``)."""
    values = list(row.get('contact_phones') or [])
    if row.get('contact_phone'):
        values.append(row['contact_phone'])
    return {normalize_phone(str(value)) for value in values if value} - {''}


def same_sender(source_author, target_author):
    """Numbers decide when both authors carry one; otherwise the names do."""
    source_number, target_number = sender_number(source_author), sender_number(target_author)
    if source_number and target_number:
        return source_number == target_number
    key = sender_key(source_author)
    return bool(key) and key == sender_key(target_author)


def is_own(source, target):
    source_phones, target_phones = phones(source), phones(target)
    if source_phones and target_phones:
        return bool(source_phones & target_phones)
    source_number, target_number = sender_number(source.get('author')), sender_number(target.get('author'))
    # A side without a phone in its text is still identified by its sender's number.
    if not source_phones and source_number in target_phones:
        return True
    if not target_phones and target_number in source_phones:
        return True
    return same_sender(source.get('author'), target.get('author'))


def tier(source, target, watchlist=(), keywords=()):
    """The ownership tier of ``target`` seen from ``source`` (rows with phones, author, raw/normalised text).

    ``watchlist`` / ``keywords`` may also be passed as one ``OwnershipRules`` in ``watchlist``."""
    if isinstance(watchlist, OwnershipRules):
        watchlist, keywords = watchlist.watchlist, watchlist.keywords
    if is_own(source, target):
        return 'own'
    team = {normalize_phone(str(phone)) for phone in watchlist or () if phone}
    if phones(target) & team:
        return 'team'
    raw, flat = str(target.get('raw_text') or '').lower(), str(target.get('normalized_text') or '')
    if any(term.lower() in raw or term.lower() in flat for term in clean_keywords(keywords)):
        return 'company'
    return 'other'


def tier_rank(name):
    return TIERS.index(name) + 1 if name in TIERS else len(TIERS)


def status_rank(status):
    return STATUS_ORDER.index(status) + 1 if status in STATUS_ORDER else len(STATUS_ORDER) + 1


def sort_key(row, status_key='status', score_key='score', time_key='sent_at'):
    """Python twin of the SQL order for rows that already carry ``ownership_tier``:
    tier, status, score (high first), most recent first."""
    stamp = row.get(time_key)
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp)
        except ValueError:
            stamp = None
    return (tier_rank(row.get(TIER_FIELD)), status_rank(row.get(status_key)), -float(row.get(score_key) or 0),
            stamp is None, -(stamp.timestamp() if hasattr(stamp, 'timestamp') else 0))


# --------------------------------------------------------------------------- SQL rule
def _clean_author_sql(raw):
    return f"regexp_replace(btrim(regexp_replace(coalesce({raw}.author,''),'[{_WS}]+',' ','g')),'{_TILDE_RE}','')"


def _sender_sql(raw):
    """SQL twin of ``sender_key``."""
    return f"lower(btrim(regexp_replace({_clean_author_sql(raw)},'{_TRAILING_RE}',''),' ,'))"


def _number_sql(raw):
    """SQL twin of ``sender_number``."""
    return (f"regexp_replace(regexp_replace(coalesce(substring({_clean_author_sql(raw)} from '{_NUMBER_RE}'),''),"
            f"'\\D','','g'),'^0','62')")


def source_sql(doc, raw=None):
    """Select-list of the source side (``SOURCE_COLUMNS``) for a ``MATERIALIZED`` CTE over the selected sources,
    so the phones and sender are worked out once per source instead of once per recommendation row."""
    raw = raw or doc
    source_phones = f"array_remove(array_remove(coalesce({doc}.contact_phones,'{{}}'::text[]) || {doc}.contact_phone,NULL),'')"
    return (f"{source_phones} AS own_phones, cardinality({source_phones})>0 AS own_has_phone, "
            f"{_sender_sql(raw)} AS own_sender, {_number_sql(raw)} AS own_number")


def like_patterns(keywords):
    return ['%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for term in clean_keywords(keywords)]


def _sender_match_sql(source, sender, target_has_phone):
    """Same sender (numbers decide when both have one, else names), or the sender's number is the source's phone
    while the target text has none. ``sender`` exposes ``n`` (number) and ``k`` (name key)."""
    return (f"(CASE WHEN {source}.own_number<>'' AND {sender}.n<>'' THEN {source}.own_number={sender}.n "
            f"ELSE {source}.own_sender<>'' AND {source}.own_sender={sender}.k END "
            f"OR (NOT {target_has_phone} AND {sender}.n<>'' AND {sender}.n=ANY({source}.own_phones)))")


def _copies_match_sql(source, copies, target_has_phone):
    """``_sender_match_sql`` against every copy at once (the arrays of ``copy_senders_sql``)."""
    return (f"(coalesce({source}.own_number=ANY({copies}.numbers),false) "
            f"OR ({source}.own_sender<>'' AND (coalesce({source}.own_sender=ANY({copies}.names_plain),false) "
            f"OR ({source}.own_number='' AND coalesce({source}.own_sender=ANY({copies}.names_numbered),false)))) "
            f"OR (NOT {target_has_phone} AND coalesce({copies}.numbers && {source}.own_phones,false)))")


def target_has_phone_sql(doc):
    return f"(concat({doc}.contact_phone,array_to_string({doc}.contact_phones,''))<>'')"


def copy_senders_sql(key, from_where, phones=None):
    """Body of a ``MATERIALIZED`` CTE for ``rank_sql(copies=...)``: per ``key`` (a group or entity id), the senders of
    every copy of a text: ``numbers``, ``names_plain`` (copies whose author has no number) and ``names_numbered``.
    ``from_where`` (``FROM ... WHERE ...``) lists the copies' raw messages as ``copy_raw``; keep it to texts posted
    more than once, the only ones where another copy can change the answer. A copy whose ``author`` is NULL adds no
    sender.

    ``phones`` (v4.10), optional: SQL ``text[]`` of one copy's contact numbers. Copies of one content may carry
    different signatures, so ``phones`` collects every copy's numbers: a copy signed with the source's number makes
    the target own, one signed with a watchlist number makes it team."""
    phone_list = (f"string_to_array(string_agg(DISTINCT nullif(array_to_string(p, ','), ''), ','), ',')"
                  if phones else 'NULL::text[]')
    return (f"SELECT key, array_agg(DISTINCT n) FILTER (WHERE n<>'') AS numbers, "
            f"array_agg(DISTINCT k) FILTER (WHERE n='' AND k<>'') AS names_plain, "
            f"array_agg(DISTINCT k) FILTER (WHERE n<>'' AND k<>'') AS names_numbered, {phone_list} AS phones "
            f"FROM (SELECT {key} AS key, {_number_sql('copy_raw')} AS n, {_sender_sql('copy_raw')} AS k, "
            f"{phones or 'NULL::text[]'} AS p {from_where}) c "
            f"GROUP BY key")


def rank_sql(rules, source, target, target_raw=None, copies=None, sender='target_sender'):
    """``(sql, params, join)``: the tier rank (1 own, 2 team, 3 company, 4 other) of a target row seen from a source.

    ``source`` is an alias exposing the ``source_sql`` columns. ``target`` / ``target_raw`` expose
    ``contact_phone``, ``contact_phones``, ``normalized_text`` / ``author``, ``raw_text`` (``target_raw`` defaults
    to ``target``). ``join`` goes into the FROM clause after the source and target: a LATERAL (alias ``sender``)
    that reads the target's sender once per row, and only where a side has no phone. ``copies``, optional: alias
    of a joined ``copy_senders_sql`` relation; a copy sent by the source's sender makes the target own as well.
    No sub-plans per row: they would make the planner's estimate (and so JIT compilation) explode.
    ``params`` belong where the expression is placed; ``join`` has none."""
    target_raw = target_raw or target
    target_phones = f"({target}.contact_phones || {target}.contact_phone)"
    has_phone = target_has_phone_sql(target)
    fallback = f"NOT ({source}.own_has_phone AND {has_phone})"
    join = (f"LEFT JOIN LATERAL (SELECT CASE WHEN {fallback} THEN {_number_sql(target_raw)} END AS n, "
            f"CASE WHEN {fallback} THEN {_sender_sql(target_raw)} END AS k OFFSET 0) {sender} ON true")
    same = _sender_match_sql(source, sender, has_phone)
    copy_own, copy_team = '', ''
    if copies:
        same += ' OR ' + _copies_match_sql(source, copies, has_phone)
        # Another copy's signature (``copy_senders_sql(phones=...)``): the same phone rule as the shown copy.
        copy_own = (f" OR coalesce({copies}.phones && {source}.own_phones,false) "
                    f"OR (NOT {source}.own_has_phone AND {source}.own_number<>'' "
                    f"AND coalesce({source}.own_number=ANY({copies}.phones),false))")
        copy_team = f' OR coalesce({copies}.phones && %s::text[],false)'
    sql = (f"CASE WHEN CASE WHEN {source}.own_has_phone AND {has_phone} "
           f"THEN coalesce({target_phones} && {source}.own_phones,false) "
           f"WHEN NOT {source}.own_has_phone AND {source}.own_number<>'' "
           f"AND coalesce({target_phones} @> ARRAY[{source}.own_number],false) THEN true "
           f"ELSE coalesce({same},false) END{copy_own} THEN 1")
    params = []
    if rules.watchlist:
        sql += f' WHEN {target_phones} && %s::text[]{copy_team} THEN 2'
        params += [list(rules.watchlist)] * (2 if copy_team else 1)
    patterns = like_patterns(rules.keywords)
    if patterns:
        # The keywords are lower-cased once per query (an InitPlan); normalized_text is stored lower-case.
        lowered = '(ARRAY(SELECT lower(p) FROM unnest(%s::text[]) p))'
        sql += f' WHEN lower({target_raw}.raw_text) LIKE ANY{lowered} OR {target}.normalized_text LIKE ANY{lowered} THEN 3'
        params += [patterns, patterns]
    return sql + ' ELSE 4 END', params, join


def status_rank_sql(column):
    """hot 1, warm 2, custom 3, anything else last."""
    return f"CASE {column} WHEN 'hot' THEN 1 WHEN 'warm' THEN 2 WHEN 'custom' THEN 3 ELSE 4 END"


def label(rows, column=RANK_COLUMN):
    """Turn the SQL rank column into the public ``ownership_tier`` label (in place)."""
    for row in rows:
        rank = row.pop(column, None)
        row[TIER_FIELD] = TIERS[rank - 1] if isinstance(rank, int) and 1 <= rank <= len(TIERS) else 'other'
    return rows
