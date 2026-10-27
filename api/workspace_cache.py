"""Content groups and pair counts, rebuilt atomically with matching results.

The group key is the part's content key (``entities.GROUP_KEY``: ``documents.content_key``, the
normalised part text without signature, case or punctuation; md5 of the exact part text while it is
not computed yet): a part of a multi-context bubble groups with the same part posted again, never
with its sibling parts. The card shows the group's latest posting (its contact); ``duplicate_count``
counts every copy.
"""
import re
from fastapi import HTTPException
import ownership
from entities import GROUP_KEY, assign_entities, cache_lock, ensure_custom_count, exact_public_ids, normalize_id_query, with_aliases
from search_terms import search_filter
from tenant import workspace_id
from matching_scope import document_scope_filter

# What a "group" key means when listings are grouped by sender or phone number.
GROUP_KEYS = {
    'sender': "coalesce(nullif(btrim(r.author),''),'Tanpa nama pengirim')",  # = NO_SENDER
    'phone': "coalesce(nullif(d.contact_phone,''),'')",
}
DEFAULT_TARGET_STATUSES = ('ready',)
NO_SENDER = 'Tanpa nama pengirim'  # raw group key of messages without an author; shown as "Unknown sender"


def group_title(group_by, key, contact_name=None):
    """How a sender / phone group is shown: a clean WhatsApp name, or "<contact name> · <phone>"."""
    from stock import clean_contact_name, display_phone, sender_title
    if group_by == 'phone':
        if not key:
            return 'No phone number'
        name = clean_contact_name(contact_name)
        return (f'{name} · ' if name else '') + display_phone(key)
    return 'Unknown sender' if key == NO_SENDER else (sender_title(key) or key)


def refresh_workspace_cache(conn, company_id=None):
    company_id = company_id or workspace_id()
    cache_lock(conn)
    ensure_custom_count(conn)
    assign_entities(conn, company_id)
    conn.execute('DROP TABLE IF EXISTS pg_temp.xm_group_build')
    conn.execute(f'''CREATE TEMP TABLE xm_group_build ON COMMIT DROP AS
      SELECT d.id document_id,d.document_type,d.entity_id,r.sent_at,
        first_value(d.id) OVER(PARTITION BY d.document_type,{GROUP_KEY} ORDER BY r.sent_at DESC NULLS LAST,d.id) group_id
      FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE d.company_id=%s AND d.active''',(company_id,))
    conn.execute('DELETE FROM xm.group_matches WHERE company_id=%s',(company_id,))
    conn.execute('DELETE FROM xm.document_group_members WHERE company_id=%s',(company_id,))
    conn.execute('DELETE FROM xm.document_groups WHERE company_id=%s',(company_id,))
    conn.execute('''INSERT INTO xm.document_groups(group_id,company_id,document_type,duplicate_count,last_seen_at,entity_id,public_id,status)
      SELECT b.group_id,%s,b.document_type,b.n,b.last_seen,b.entity_id,e.public_id,coalesce(e.status,'ready')
      FROM (SELECT group_id,document_type,count(*) n,max(sent_at) last_seen,(array_agg(entity_id))[1] entity_id
            FROM xm_group_build GROUP BY group_id,document_type) b
      LEFT JOIN xm.entities e ON e.entity_id=b.entity_id''',(company_id,))
    conn.execute('''INSERT INTO xm.document_group_members(document_id,group_id,company_id)
      SELECT document_id,group_id,%s FROM xm_group_build''',(company_id,))
    # The stored status travels with the best pair: at equal scores a confirmed
    # (hot/warm) pair wins over one whose strict location is unconfirmed.
    conn.execute('''INSERT INTO xm.group_matches(company_id,buyer_group_id,property_group_id,match_id,score,status)
      SELECT DISTINCT ON (b.group_id,p.group_id) m.company_id,b.group_id,p.group_id,m.id,m.score,m.status
      FROM xm.matches m JOIN xm.document_group_members b ON b.document_id=m.buyer_request_id
      JOIN xm.document_group_members p ON p.document_id=m.property_listing_id
      WHERE m.company_id=%s AND m.score>=60 ORDER BY b.group_id,p.group_id,m.score DESC,m.status='custom',m.id''',(company_id,))
    # Hot/warm/custom counters only count counterparts that are still ready.
    conn.execute('''UPDATE xm.document_groups g SET hot_count=c.hot,warm_count=c.warm,custom_count=c.custom
      FROM (SELECT group_id,count(*) FILTER(WHERE status='hot') hot,count(*) FILTER(WHERE status='warm') warm,
                   count(*) FILTER(WHERE status='custom') custom
        FROM (SELECT gm.buyer_group_id group_id,gm.status FROM xm.group_matches gm
                JOIN xm.document_groups p ON p.group_id=gm.property_group_id AND p.status='ready' WHERE gm.company_id=%s
          UNION ALL SELECT gm.property_group_id,gm.status FROM xm.group_matches gm
                JOIN xm.document_groups b ON b.group_id=gm.buyer_group_id AND b.status='ready' WHERE gm.company_id=%s) pairs
        GROUP BY group_id) c
      WHERE g.group_id=c.group_id AND g.company_id=%s''',(company_id,company_id,company_id))
    conn.execute('''INSERT INTO xm.workspace_cache_state(company_id,refreshed_at) VALUES(%s,now())
      ON CONFLICT(company_id) DO UPDATE SET refreshed_at=excluded.refreshed_at''',(company_id,))
    conn.execute('ANALYZE xm.document_groups')
    conn.execute('ANALYZE xm.document_group_members')
    conn.execute('ANALYZE xm.group_matches')


def ready(conn):
    return bool(conn.execute("SELECT 1 FROM xm.workspace_cache_state WHERE company_id=current_setting('xm.workspace_id')").fetchone())


def target_window(direction, clause='', date_params=()):
    """Keep a posting within the target window, even if a later copy exists."""
    kind = 'property_listing' if direction == 'buyer' else 'buyer_request'
    if not clause:
        return '''target_eligible AS (
          SELECT g.group_id,g.group_id id,g.entity_id,g.public_id,g.status,g.duplicate_count,g.last_seen_at
          FROM xm.document_groups g WHERE g.company_id=current_setting('xm.workspace_id')
            AND g.document_type=%s AND g.status='ready'
        )''', [kind]
    sql = '''target_eligible AS (
      SELECT DISTINCT ON (g.group_id) g.group_id,d.id,g.entity_id,g.public_id,g.status,g.duplicate_count,g.last_seen_at
      FROM xm.document_groups g JOIN xm.document_group_members tm ON tm.group_id=g.group_id
      JOIN xm.documents d ON d.id=tm.document_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE g.company_id=current_setting('xm.workspace_id') AND d.company_id=g.company_id
        AND d.active AND g.document_type=%s AND g.status='ready' ''' + clause + '''
      ORDER BY g.group_id,r.sent_at DESC NULLS LAST,d.id
    )'''
    return sql, [kind, *date_params]


def unsent_clause(scope, buyer='b.entity_id', listing='p.entity_id'):
    if not scope:
        return '', []
    return f''' AND NOT EXISTS (SELECT 1 FROM xm.match_deliveries delivered
      WHERE delivered.company_id=current_setting('xm.workspace_id') AND delivered.delivery_scope=%s
        AND delivered.buyer_entity_id={buyer} AND delivered.listing_entity_id={listing})''', [scope]


def _eligible(direction, search, phones, statuses, clause, date_params, stock_statuses, public_id, group_by, group_key, per_key=False,
              target_clause='', target_params=(), delivery_scope='', *, conn):
    """Shared CTE: one representative posting per group, after every list filter.

    ``per_key`` keeps one posting per group *and* sender/phone, so a text posted by two sales counts for both,
    exactly as it does when the list is read one sender/phone at a time with ``group_key``."""
    kind = 'buyer_request' if direction == 'buyer' else 'property_listing'
    from parser import normalize_phone
    selected = set(statuses.split(','))
    prefix, params, count_join = '', [], ''
    hot, warm, custom = 'g.hot_count', 'g.warm_count', 'g.custom_count'
    if target_clause or delivery_scope:
        targets, target_values = target_window(direction, target_clause, target_params)
        source, target = ('buyer_group_id', 'property_group_id') if direction == 'buyer' else ('property_group_id', 'buyer_group_id')
        unseen, unseen_values = unsent_clause(delivery_scope)
        prefix = targets + f''', pair_counts AS (
          SELECT m.{source} group_id,count(*) FILTER(WHERE m.status='hot') hot_count,
            count(*) FILTER(WHERE m.status='warm') warm_count,count(*) FILTER(WHERE m.status='custom') custom_count
          FROM xm.group_matches m JOIN target_eligible t ON t.group_id=m.{target}
          JOIN xm.document_groups b ON b.group_id=m.buyer_group_id
          JOIN xm.document_groups p ON p.group_id=m.property_group_id
          WHERE m.company_id=current_setting('xm.workspace_id'){unseen} GROUP BY m.{source}
        ), '''
        params += target_values + unseen_values
        count_join = ' LEFT JOIN pair_counts pc ON pc.group_id=g.group_id'
        hot, warm, custom = 'coalesce(pc.hot_count,0)', 'coalesce(pc.warm_count,0)', 'coalesce(pc.custom_count,0)'
    filters = []
    if 'hot' in selected: filters.append(f'{hot}>0')
    if 'warm' in selected: filters.append(f'{warm}>0')
    if 'custom' in selected: filters.append(f'{custom}>0')
    if 'unmatched' in selected: filters.append(f'{hot}+{warm}+{custom}=0')
    params += [kind, list(stock_statuses)]
    key_sql = GROUP_KEYS[group_by] if group_by in GROUP_KEYS else "''"
    distinct = 'g.group_id' + (f',{key_sql}' if per_key else '')
    sql = prefix + f'''eligible AS (
      SELECT DISTINCT ON ({distinct}) d.id,g.group_id,r.sent_at,r.author,d.contact_phone,d.contact_name,
        {key_sql} AS group_key,{hot} hot_count,{warm} warm_count,{custom} custom_count,
        count(*) OVER(PARTITION BY g.group_id) duplicate_count
      FROM xm.document_groups g JOIN xm.document_group_members gm ON gm.group_id=g.group_id
      JOIN xm.documents d ON d.id=gm.document_id JOIN xm.raw_messages r ON r.id=d.raw_message_id{count_join}
      WHERE g.company_id=current_setting('xm.workspace_id') AND d.active AND g.document_type=%s AND g.status=ANY(%s)
        AND (''' + (' OR '.join(filters) or 'false') + ')'
    search_clause, search_params = search_filter(search)
    sql += search_clause
    params += search_params
    scope_clause, scope_params = document_scope_filter(conn)
    sql += scope_clause
    params += scope_params
    if public_id and public_id.strip():
        needle = normalize_id_query(public_id)
        if needle:
            exact = exact_public_ids(needle)
            if exact:
                sql += ' AND g.public_id = ANY(%s)'
                params.append(with_aliases(conn, workspace_id(), exact))
            else:
                sql += " AND replace(g.public_id,'-','') LIKE %s"
                params.append('%' + needle + '%')
    if phones.strip() and direction == 'property':
        numbers = [normalize_phone(v) for v in re.split(r'[,;\n]+', phones) if v.strip()]
        if any(not n.startswith('628') or not 10 <= len(n) <= 15 for n in numbers):
            raise HTTPException(400, 'Invalid phone number. Separate several numbers with a comma or a new line.')
        sql += ' AND (d.contact_phones && %s::text[] OR d.contact_phone=ANY(%s))'
        params += [numbers, numbers]
    if group_by in GROUP_KEYS and group_key is not None:
        sql += f' AND {key_sql}=%s'
        params.append(group_key)
    sql += clause
    params += date_params
    sql += f' ORDER BY {distinct},r.sent_at DESC NULLS LAST,d.id\n    )'
    return sql, params


def sources(conn, direction, search, phones, statuses, clause, date_params, offset,
            stock_statuses=DEFAULT_TARGET_STATUSES, public_id='', group_by=None, group_key=None,
            target_clause='', target_params=()):
    eligible, params = _eligible(direction, search, phones, statuses, clause, date_params, stock_statuses, public_id, group_by, group_key,
                                 target_clause=target_clause, target_params=target_params, conn=conn)
    # No text equality or matching aggregation on the interactive read path.
    query = 'WITH ' + eligible + ''', page AS (SELECT * FROM eligible ORDER BY sent_at DESC NULLS LAST,id LIMIT 201 OFFSET %s)
      SELECT d.*,r.raw_text,r.chat_name,r.sent_at,r.author,p.duplicate_count,g.last_seen_at,g.public_id,g.status AS entity_status,
        p.hot_count,p.warm_count,p.custom_count,p.hot_count+p.warm_count+p.custom_count match_count
      FROM page p JOIN xm.documents d ON d.id=p.id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      JOIN xm.document_groups g ON g.group_id=p.group_id ORDER BY p.sent_at DESC NULLS LAST,p.id'''
    params.append(max(0, offset))
    rows = conn.execute(query, params).fetchall()
    return {'rows': rows[:200], 'has_more': len(rows) > 200}


def group_summary(conn, direction, search, phones, statuses, clause, date_params,
                  stock_statuses=DEFAULT_TARGET_STATUSES, public_id='', group_by='sender', group_search='', limit=200, offset=0,
                  target_clause='', target_params=()):
    """Listings per sender or per phone number, with the same filters as the card list."""
    if group_by not in GROUP_KEYS:
        raise HTTPException(400, 'Unknown grouping.')
    eligible, params = _eligible(direction, search, phones, statuses, clause, date_params, stock_statuses, public_id, group_by, None,
                                 target_clause=target_clause, target_params=target_params, conn=conn)
    having = ''
    if group_search.strip():
        having = " HAVING e.group_key ILIKE %s OR coalesce(mode() WITHIN GROUP (ORDER BY e.contact_name),'') ILIKE %s"
        pattern = '%' + group_search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        params += [pattern, pattern]
    limit = min(max(limit, 1), 300)
    query = 'WITH ' + eligible + f'''
      SELECT e.group_key AS key,count(*) AS count,
             count(*) FILTER (WHERE e.hot_count>0) AS hot,
             count(*) FILTER (WHERE e.warm_count>0 AND e.hot_count=0) AS warm,
             count(*) FILTER (WHERE e.custom_count>0 AND e.hot_count=0 AND e.warm_count=0) AS custom,
             max(e.sent_at) AS latest_at,
             mode() WITHIN GROUP (ORDER BY e.contact_name) AS contact_name,
             count(DISTINCT e.author) AS sender_count
      FROM eligible e JOIN xm.document_groups g ON g.group_id=e.group_id
      GROUP BY e.group_key{having}
      ORDER BY count(*) DESC,e.group_key LIMIT %s OFFSET %s'''
    params += [limit + 1, max(0, offset)]
    rows = conn.execute(query, params).fetchall()
    for row in rows:
        row['title'] = group_title(group_by, row['key'], row['contact_name'])
    return {'group_by': group_by, 'groups': rows[:limit], 'has_more': len(rows) > limit}


def recommendations(conn, direction, ids, target_statuses=DEFAULT_TARGET_STATUSES, target_clause='', target_params=(), delivery_scope='',
                    source_clause='', source_params=()):
    kind='buyer_request' if direction=='buyer' else 'property_listing'
    relation,other=('buyer_group_id','property_group_id') if direction=='buyer' else ('property_group_id','buyer_group_id')
    scope_clause, scope_params = document_scope_filter(conn)
    sources=conn.execute('''SELECT d.*,r.raw_text,r.chat_name,r.sent_at,r.author,g.public_id,g.status AS entity_status
      FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
      LEFT JOIN xm.document_group_members sm ON sm.document_id=d.id
      LEFT JOIN xm.document_groups g ON g.group_id=sm.group_id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.id=ANY(%s) AND d.document_type=%s''' + source_clause + scope_clause,
      [ids,kind,*source_params,*scope_params]).fetchall()
    ids = [source['id'] for source in sources]
    # Target status may include on-hold for an explicitly requested detail view.
    targets, values = target_window(direction, target_clause, target_params)
    targets = targets.replace("g.status='ready'", 'g.status=ANY(%s)')
    values.insert(1, list(target_statuses))
    unseen, unseen_values = unsent_clause(delivery_scope)
    # Per source: own stock first, then the sales watchlist, then company-keyword messages, then everyone else
    # (ownership.py); inside a tier hot, warm, custom, then score and the latest posting. Read-time only.
    # The card shows the group's latest copy, but any copy (inside the target window) sent by the source's
    # sender makes it own stock, as in the uncached path, which ranks every copy.
    tier_sql, tier_values, sender_join = ownership.rank_sql(ownership.load_rules(conn), 'src', 'd', 'r', copies='cs')
    # Copies are read only for matched targets posted more than once: one indexed lookup per matched group.
    # v4.10: copies of one content may be signed by different sales, so every copy's phones count as well.
    copies = ownership.copy_senders_sql('mg.group_id', f'''FROM matched_groups mg CROSS JOIN LATERAL (
            SELECT CASE WHEN EXISTS (SELECT 1 FROM src WHERE NOT src.own_has_phone)
                          OR NOT {ownership.target_has_phone_sql('cd')} THEN copy_raw.author END AS author,
                   cd.contact_phones || cd.contact_phone AS phones
            FROM xm.document_groups cg
            JOIN xm.document_group_members cm ON cm.group_id=cg.group_id
            JOIN xm.documents cd ON cd.id=cm.document_id AND cd.active
            JOIN xm.raw_messages copy_raw ON copy_raw.id=cd.raw_message_id
            WHERE cg.group_id=mg.group_id AND cg.duplicate_count>1'''
        + target_clause.replace('r.sent_at', 'copy_raw.sent_at') + ' OFFSET 0) copy_raw', phones='copy_raw.phones')
    rows=conn.execute(f'''WITH {targets}, src AS MATERIALIZED (
        SELECT sd.id,{ownership.source_sql('sd', 'sr')}
        FROM xm.documents sd JOIN xm.raw_messages sr ON sr.id=sd.raw_message_id WHERE sd.id=ANY(%s)
      ), matched_groups AS MATERIALIZED (
        SELECT DISTINCT gm.{other} AS group_id FROM xm.document_group_members sm
        JOIN xm.group_matches gm ON gm.{relation}=sm.group_id AND gm.company_id=current_setting('xm.workspace_id')
        WHERE sm.document_id=ANY(%s)
      ), cs AS MATERIALIZED ({copies})
      SELECT s.document_id source_id,m.score,m.status,m.explanation,m.id match_id,d.*,r.raw_text,r.chat_name,r.sent_at,r.author,
        g.duplicate_count,g.last_seen_at,g.public_id,g.status AS entity_status,{tier_sql} AS {ownership.RANK_COLUMN}
      FROM xm.document_group_members s JOIN src ON src.id=s.document_id
      JOIN xm.group_matches gm ON gm.{relation}=s.group_id
      JOIN xm.matches m ON m.id=gm.match_id JOIN target_eligible g ON g.group_id=gm.{other}
      JOIN xm.document_groups b ON b.group_id=gm.buyer_group_id
      JOIN xm.document_groups p ON p.group_id=gm.property_group_id
      JOIN xm.documents d ON d.id=g.id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      LEFT JOIN cs ON cs.key=g.group_id {sender_join}
      WHERE s.company_id=current_setting('xm.workspace_id') AND gm.company_id=s.company_id
        AND s.document_id=ANY(%s) AND d.active{unseen}
      ORDER BY {ownership.RANK_COLUMN},{ownership.status_rank_sql('m.status')},m.score DESC,r.sent_at DESC NULLS LAST,d.id''',
      values + [ids] + [ids, *target_params] + tier_values + [ids] + unseen_values).fetchall()
    ownership.label(rows)
    return {'groups':[{'source':s,'recommendations':[r for r in rows if r['source_id']==s['id']]} for s in sources]}
