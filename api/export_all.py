"""Every match as one PDF or a ZIP of separate PDFs per sender/phone.

The browser drives the work in small parts so it can show real progress:
``plan`` (what will be exported) -> ``part`` (a slice of one sender or phone
number, written to disk) -> ``download`` (merge parts into a single PDF or one PDF per group).

``combined``: both directions in one export. Every person (sender or sales contact, the same key in both
directions) gets a Listing → Buyer section, then a Buyer → Listing section, each with its own cover; a
direction without pairs is left out. Pair pages are numbered per person ("No. N", across both sections) and
the page numbers ("Hal. N dari M") restart for every person when the parts are merged.
"""
import os
import json
import zipfile
import re
import shutil
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from collections import Counter
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import workspace_cache
from company import company_row
from db import connect
from stock import clean_contact_name, display_phone, sender_title
from tenant import workspace_id
from workspace import resolve_search, stock_statuses
from periods import MatchingPeriods
from matching_scope import history_scope_filter
from matchlog import PAIR_FIELDS, status_clause, wib_bounds

router = APIRouter(prefix='/export/all')
selection_router = APIRouter(prefix='/export/selection')
EXPORT_DIR = Path(os.getenv('XM_EXPORT_DIR') or Path(tempfile.gettempdir()) / 'xm-exports')
SLICE = 25  # buyers or listings per part: keeps every request short
STALE_SECONDS = 6 * 3600
TEMPERATURES = ('hot', 'warm', 'custom', 'unmatched')
MAX_GROUP_KEYS = 500
MAX_PAGES = 15000  # beyond this the file is too heavy to merge and to open on a phone
COUNTS = ('sources', *TEMPERATURES, 'pages')
# Combined export: Listing → Buyer comes first, then Buyer → Listing; part files sort the same way.
SECTION_ORDER = ('property', 'buyer')
SECTION_FILES = {'property': 'a', 'buyer': 'b'}


class Filters(MatchingPeriods):
    """The same filters as the Match list."""
    direction: Literal['buyer', 'property'] = 'buyer'
    search: str = Field(default='', max_length=4020)
    phones: str = ''
    statuses: str = 'hot,warm,custom'
    stock_status: str = 'ready'
    public_id: str = ''
    group_by: Literal['sender', 'phone'] | None = None
    # Empty for manual export; an integration stream opts in to delivery deduplication.
    delivery_scope: str = Field(default='', max_length=200)
    # Recent exports use discovery dates and logged pairs, not posting dates.
    recent: bool = False
    found_from: str = ''
    found_to: str = ''
    recent_sources: list[uuid.UUID] | None = Field(default=None, max_length=200)
    recent_events: list[int] | None = Field(default=None, max_length=200)
    # Only these sender/phone groups (raw keys as the plan returns them); None exports every group.
    group_keys: list[Annotated[str, Field(max_length=1000)]] | None = Field(default=None, max_length=MAX_GROUP_KEYS)
    # Both directions, one person at a time (see the module docstring). Each part is still one direction.
    combined: bool = False


class Part(Filters):
    token: uuid.UUID
    index: int = Field(ge=0, le=99999)
    group_key: str
    offset: int = Field(default=0, ge=0)
    # Kept for older clients: page numbers are stamped per person when the parts are merged.
    first_page: int = Field(default=1, ge=1)
    # Number of the first pair page of this part within its person ("No. N"); the client adds up ``pairs``.
    first_pair: int = Field(default=1, ge=1)


def temperatures(filters):
    chosen = [item for item in TEMPERATURES if item in filters.statuses.split(',')]
    if not chosen:
        raise HTTPException(400, 'Choose at least one result type: Hot, Warm, Custom or Unmatched.')
    if filters.combined and filters.recent:
        raise HTTPException(400, 'Both directions can only be exported from the Match page.')
    return chosen


def scope(conn, request, filters, group_key=None):
    """The filtered sources as a CTE, keyed by the company's own grouping (sender or phone)."""
    if not workspace_cache.ready(conn):
        raise HTTPException(409, 'Results are being prepared. Please try again in a moment.')
    group_by = filters.group_by or company_row(conn)['listing_group_by'] or 'sender'
    if filters.recent:
        return recent_scope(conn, filters, group_by, group_key)
    (clause, date_params), (target_clause, target_params) = filters.windows(filters.direction)
    eligible, params = workspace_cache._eligible(
        filters.direction, resolve_search(conn, request, filters.search), filters.phones, filters.statuses, clause, date_params,
        stock_statuses(filters.stock_status), filters.public_id, group_by, group_key, per_key=True,
        target_clause=target_clause, target_params=target_params, delivery_scope=filters.delivery_scope, conn=conn)
    return group_by, eligible, params


def recent_scope(conn, filters, group_by, group_key=None):
    start, end = wib_bounds(filters.found_from, filters.found_to)
    scope_sql, scope_params = history_scope_filter(conn, filters.direction)
    source, target = ('buyer_entity', 'listing_entity') if filters.direction == 'buyer' else ('listing_entity', 'buyer_entity')
    chosen = temperatures(filters)
    allowed = list(stock_statuses(filters.stock_status))
    sql = f'''recent_pairs AS (
      SELECT me.*,me.{source} source_entity,me.{target} target_entity
      FROM xm.match_events me JOIN xm.entities se ON se.entity_id=me.{source}
      JOIN xm.entities te ON te.entity_id=me.{target}
      WHERE me.company_id=current_setting('xm.workspace_id') AND me.source='import' AND me.active
        AND me.found_at>=%s AND me.found_at<%s AND se.status=ANY(%s) AND te.status=ANY(%s)''' + scope_sql
    params = [start, end, allowed, allowed, *scope_params]
    sql += status_clause(chosen)
    if filters.recent_sources is not None:
        sql += f' AND me.{source}=ANY(%s::uuid[])'
        params.append(filters.recent_sources)
    if filters.recent_events is not None:
        sql += ' AND me.id=ANY(%s::bigint[])'
        params.append(filters.recent_events)
    key = workspace_cache.GROUP_KEYS[group_by]
    sql += f'''), eligible AS (
      SELECT d.id,g.group_id,r.sent_at,r.author,d.contact_name,d.contact_phone,{key} group_key,
        c.hot_count,c.warm_count,c.custom_count
      FROM (SELECT source_entity,count(*) FILTER(WHERE last_status='hot') hot_count,
          count(*) FILTER(WHERE last_status='warm') warm_count,
          count(*) FILTER(WHERE last_status='custom') custom_count FROM recent_pairs GROUP BY source_entity) c
      JOIN xm.document_groups g ON g.entity_id=c.source_entity
      JOIN xm.documents d ON d.id=g.group_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE g.company_id=current_setting('xm.workspace_id')'''
    if group_key is not None:
        sql += f' AND {key}=%s'
        params.append(group_key)
    return group_by, sql + ')', params


def recent_pairs(conn, eligible, params, ids):
    sources = conn.execute(f'''SELECT {PAIR_FIELDS} FROM xm.document_groups g
      JOIN xm.documents d ON d.id=g.group_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE g.company_id=current_setting('xm.workspace_id') AND d.id=ANY(%s::uuid[])''', (ids,)).fetchall()
    by_entity = {row['entity_id']: row for row in sources}
    targets = conn.execute('WITH ' + eligible + f''' SELECT {PAIR_FIELDS},rp.source_entity,rp.last_score score,rp.last_status status
      FROM recent_pairs rp JOIN xm.document_groups g ON g.entity_id=rp.target_entity
      JOIN xm.documents d ON d.id=g.group_id JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE g.company_id=current_setting('xm.workspace_id') AND rp.source_entity=ANY(%s::uuid[])
      ORDER BY rp.source_entity,rp.last_score DESC,rp.found_at DESC,rp.id''', [*params, list(by_entity)]).fetchall()
    # Same order and ownership tier as Recent matches: per source own -> team -> company -> other, then status, score.
    from matchlog import rank_pairs
    return rank_pairs(conn, [(by_entity[row['source_entity']], row) for row in targets])


def group_stats(conn, eligible, params, group_by, chosen, group_keys=None):
    """Per sender / phone group: sources and pages per status. ``group_keys`` keeps only the chosen groups."""
    having, values = '', list(params)
    if group_keys is not None:
        having = ' HAVING e.group_key = ANY(%s::text[])'
        values.append(list(dict.fromkeys(group_keys)))
    rows = conn.execute('WITH ' + eligible + '''
      SELECT e.group_key AS key, count(*) AS sources, coalesce(sum(e.hot_count),0) AS hot, coalesce(sum(e.warm_count),0) AS warm,
             coalesce(sum(e.custom_count),0) AS custom,
             count(*) FILTER (WHERE e.hot_count+e.warm_count+e.custom_count=0) AS unmatched,
             mode() WITHIN GROUP (ORDER BY e.contact_name) AS contact_name,
             mode() WITHIN GROUP (ORDER BY e.contact_phone) AS contact_phone
      FROM eligible e JOIN xm.document_groups g ON g.group_id=e.group_id
      GROUP BY e.group_key''' + having + ' ORDER BY count(*) DESC,e.group_key', values).fetchall()
    groups = []
    for row in rows:
        counts = {key: int(row[key]) if key in chosen else 0 for key in TEMPERATURES}
        groups.append({'key': row['key'], 'title': workspace_cache.group_title(group_by, row['key'], row['contact_name']),
                       'sources': row['sources'], 'contact_name': clean_contact_name(row['contact_name']) or '',
                       'phone': row['contact_phone'] or '', **counts, 'pages': sum(counts.values())})
    return [group for group in groups if group['pages']]


def direction_filters(filters, direction, group_by):
    return filters.model_copy(update={'direction': direction, 'group_by': group_by})


def person_sections(conn, request, filters, group_by, chosen, group_key=None):
    """Combined export: {group key: [stats per direction that has pages, in SECTION_ORDER]}."""
    people = {}
    for direction in SECTION_ORDER:
        _, eligible, params = scope(conn, request, direction_filters(filters, direction, group_by), group_key)
        for group in group_stats(conn, eligible, params, group_by, chosen, filters.group_keys if group_key is None else None):
            people.setdefault(group['key'], []).append({**group, 'direction': direction})
    return people


def combined_groups(conn, request, filters, group_by, chosen):
    """One plan group per person with both directions: totals, a cover per section and the sections to export."""
    groups = []
    for key, sections in person_sections(conn, request, filters, group_by, chosen).items():
        first = sections[0]
        groups.append({'key': key, 'title': first['title'],
                       'contact_name': next((s['contact_name'] for s in sections if s['contact_name']), ''),
                       'phone': next((s['phone'] for s in sections if s['phone']), ''),
                       **{name: sum(s[name] for s in sections) for name in COUNTS}, 'covers': len(sections),
                       'sections': [{'direction': s['direction'], **{name: s[name] for name in COUNTS}} for s in sections]})
    return sorted(groups, key=lambda group: (-group['sources'], group['key']))


def pdf_title(group_by, group):
    """Section name printed in the (Indonesian) PDF: the display title, with Indonesian fallbacks."""
    if group_by == 'phone' and not group['key']:
        return 'Tanpa nomor telepon'
    if group_by == 'sender' and group['key'] == workspace_cache.NO_SENDER:
        return workspace_cache.NO_SENDER
    return group['title']


def folder(token):
    return EXPORT_DIR / re.sub(r'[^A-Za-z0-9_-]', '_', workspace_id()) / str(token)


def sweep():
    """Parts of exports nobody downloaded are removed after a few hours."""
    limit = time.time() - STALE_SECONDS
    for path in EXPORT_DIR.glob('*/*'):
        if path.is_dir() and path.stat().st_mtime < limit:
            shutil.rmtree(path, ignore_errors=True)


@router.post('/plan')
def plan(filters: Filters, request: Request):
    """Groups to export, each with its ``sections`` (directions, in export order) and ``covers`` (cover pages)."""
    chosen = temperatures(filters)
    with connect() as conn:
        if not workspace_cache.ready(conn):  # nothing uploaded yet: an empty plan, like the empty list on screen
            group_by, groups = filters.group_by or company_row(conn)['listing_group_by'] or 'sender', []
        elif filters.combined:
            group_by = filters.group_by or company_row(conn)['listing_group_by'] or 'sender'
            groups = combined_groups(conn, request, filters, group_by, chosen)
        else:
            group_by, eligible, params = scope(conn, request, filters)
            groups = [{**group, 'covers': 1, 'sections': [{'direction': filters.direction, **{name: group[name] for name in COUNTS}}]}
                      for group in group_stats(conn, eligible, params, group_by, chosen, filters.group_keys)]
    sweep()
    totals = {key: sum(group[key] for group in groups) for key in (*COUNTS, 'covers')}
    return {'token': uuid.uuid4(), 'group_by': group_by, 'combined': filters.combined, 'groups': groups, 'totals': totals,
            'max_pages': MAX_PAGES}


@router.post('/part')
def part(payload: Part, request: Request):
    from report import build_report, pair_status
    chosen = temperatures(payload)
    with connect() as conn:
        group_by, eligible, params = scope(conn, request, payload, payload.group_key)
        stats = group_stats(conn, eligible, params, group_by, chosen)
        rows = conn.execute('WITH ' + eligible + ' SELECT id FROM eligible ORDER BY sent_at DESC NULLS LAST,id LIMIT %s OFFSET %s',
                            params + [SLICE + 1, payload.offset]).fetchall()
        ids = [row['id'] for row in rows[:SLICE]]
        _, (target_clause, target_params) = payload.windows(payload.direction)
        found = {group['source']['id']: group for group in workspace_cache.recommendations(
            conn, payload.direction, ids, target_clause=target_clause, target_params=target_params,
            delivery_scope=payload.delivery_scope)['groups']} if ids and not payload.recent else {}
        historical = recent_pairs(conn, eligible, params, ids) if ids and payload.recent else []
        if historical:
            # Logged pairs carry no reasons of their own: the current reasons of the same entities.
            from matchlog import pair_explanations
            sides = [(source, target) if payload.direction == 'buyer' else (target, source) for source, target in historical]
            reasons = pair_explanations(conn, [(buyer['entity_id'], listing['entity_id']) for buyer, listing in sides])
            for (_, target), (buyer, listing) in zip(historical, sides):
                target['explanation'] = reasons.get((str(buyer['entity_id']), str(listing['entity_id'])), [])
        from company import company_row
        company_name = company_row(conn)['company_name']
        sections = person_sections(conn, request, payload, group_by, chosen, payload.group_key).get(payload.group_key, []) \
            if payload.combined and payload.offset == 0 else []
    pairs = []
    pairs += historical
    for source_id in ids:
        group = found.get(source_id)
        if not group:
            continue
        if not group['recommendations'] and 'unmatched' in chosen:
            pairs.append((group['source'], None))
        pairs += [(group['source'], target) for target in group['recommendations'] if pair_status(target) in chosen]
    section = pdf_title(group_by, stats[0]) if stats else ''
    cover = None
    if payload.offset == 0 and stats:
        # The PDF is a document for other people and stays Indonesian.
        cover = {**stats[0], 'title': section, 'kind': 'Nomor telepon' if group_by == 'phone' else 'Pengirim',
                 'direction': payload.direction}
        if payload.combined:
            present = [s['direction'] for s in sections]
            present = [d for d in SECTION_ORDER if d in present or d == payload.direction]
            cover.update(combined=True, part=present.index(payload.direction) + 1, parts=len(present),
                         phone=stats[0]['phone'] or next((s['phone'] for s in sections if s['phone']), ''))
    pages = len(pairs) + (1 if cover else 0)
    if pages:
        target = folder(payload.token)
        target.mkdir(parents=True, exist_ok=True)
        pdf = build_report(pairs, payload.direction, section=section, cover=cover, first_pair=payload.first_pair,
                           combined=payload.combined, company_name=company_name)
        name = SECTION_FILES[payload.direction] if payload.combined else ''
        (target / f'{payload.index:05d}-{name}{payload.offset:08d}.pdf').write_bytes(pdf)
        if payload.offset == 0 and stats:
            metadata = {**stats[0], 'group_by': group_by, 'direction': payload.direction, 'first_page': 1}
            if payload.combined:
                counts = {s['direction']: s['sources'] for s in sections} or {payload.direction: stats[0]['sources']}
                metadata.update(direction='both', combined=True, sections=counts,
                                contact_name=stats[0]['contact_name'] or next((s['contact_name'] for s in sections if s['contact_name']), ''),
                                phone=cover['phone'])
            (target / f'{payload.index:05d}.json').write_text(json.dumps(metadata))
    entity_pairs = []
    for source, target in pairs:
        if target and source.get('entity_id') and target.get('entity_id'):
            buyer, listing = (source, target) if payload.direction == 'buyer' else (target, source)
            entity_pairs.append([str(buyer['entity_id']), str(listing['entity_id'])])
    return {'pages': pages, 'pairs': len(pairs), 'next_offset': payload.offset + SLICE if len(rows) > SLICE else None,
            'entity_pairs': entity_pairs}


def merge_pdf_parts(*groups):
    """One PDF from part files, given as one list per person: each person's pages are numbered from 1
    ("Hal. 1 dari M"), so every file of a ZIP and every person of a combined PDF starts at page 1."""
    from pypdf import PdfWriter
    from report import stamp_page_numbers
    writer = PdfWriter()
    for items in groups:
        start = len(writer.pages)
        for item in items:
            writer.append(item if isinstance(item, BytesIO) else str(item))
        stamp_page_numbers(writer, start, len(writer.pages) - start)
    writer.compress_identical_objects()
    output = BytesIO()
    writer.write(output)
    count = len(writer.pages)
    writer.close()
    return output.getvalue(), count


def group_filename(index, metadata, count):
    """Names for manual downloads and workflow attachments follow the same contract. Every file starts at page 1."""
    name = sender_title(metadata['key']) if metadata['group_by'] == 'sender' else metadata['contact_name']
    name = re.sub(r'[^\w-]+', '-', name, flags=re.UNICODE).strip('-')[:100] or 'Tanpa-nama'
    phone = re.sub(r'[^0-9]', '', metadata['phone']) or 'Tanpa-nomor'
    if metadata.get('combined'):
        sections = metadata.get('sections') or {}
        sources = '_'.join(f"{sections[d]}{'listing' if d == 'property' else 'buyer'}" for d in SECTION_ORDER if sections.get(d))
    else:
        sources = f"{metadata['sources']}{'buyer' if metadata['direction'] == 'buyer' else 'listing'}"
    return f"{index + 1:02d}_{name}_{phone}_{sources}_hal1-{count}.pdf"


@router.get('/{token}/download')
def download(token: uuid.UUID, output: Literal['single', 'grouped'] = 'single'):
    target = folder(token)
    parts = sorted(target.glob('*.pdf')) if target.is_dir() else []
    if not parts:
        raise HTTPException(404, 'Export file not found. Please start the export again.')
    indexes = sorted({item.name.split('-')[0] for item in parts})
    groups = [[item for item in parts if item.name.startswith(index + '-')] for index in indexes]
    combined = any(json.loads(path.read_text()).get('combined') for path in target.glob('*.json'))
    base = 'Semua-Pencocokan-Dua-Arah' if combined else 'Semua-Pencocokan'
    if output == 'single':
        content, _ = merge_pdf_parts(*groups)
        media_type, filename = 'application/pdf', base + '.pdf'
    else:
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for index, items in zip(indexes, groups):
                metadata_path = target / f'{index}.json'
                if not metadata_path.exists():
                    raise HTTPException(409, 'Group data is incomplete. Please start the export again.')
                metadata = json.loads(metadata_path.read_text())
                content, count = merge_pdf_parts(items)
                filename = group_filename(int(index), metadata, count)
                bundle.writestr(filename, content)
        content = archive.getvalue()
        media_type, filename = 'application/zip', base + '.zip'
    return Response(content, media_type=media_type,
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'},
                    background=BackgroundTask(shutil.rmtree, target, True))


class Cancel(BaseModel):
    token: uuid.UUID


@router.post('/cancel')
def cancel(payload: Cancel):
    shutil.rmtree(folder(payload.token), ignore_errors=True)
    return {'ok': True}


# ---------------------------------------------------------------- hand-picked pairs from the Match page
class SelectedPair(BaseModel):
    direction: Literal['buyer', 'property']
    source_id: uuid.UUID
    target_id: uuid.UUID | None = None


class Selection(BaseModel):
    """Pairs ticked on the Match page across many buyers and listings, in both directions."""
    pairs: list[SelectedPair] = Field(min_length=1, max_length=200)
    output: Literal['grouped', 'single'] = 'grouped'
    # Ticks survive filter changes, so the check uses no posting window; on-hold targets stay valid.
    target_status: list[Literal['ready', 'on_hold', 'sold']] = ['ready', 'on_hold']


def selected_pairs(payload):
    """``[(direction, source, target or None, position)]`` checked the way /export/pdf checks them:
    a source outside the workspace (or the company's scope) is 404, a pair that no longer matches is 409."""
    from workspace import Batch, recommendations
    pairs = list({(p.direction, p.source_id, p.target_id): p for p in payload.pairs}.values())
    found = {}
    for direction in SECTION_ORDER:
        ids = list(dict.fromkeys(p.source_id for p in pairs if p.direction == direction))
        for start in range(0, len(ids), 50):
            batch = Batch(direction=direction, ids=ids[start:start + 50], target_status=payload.target_status)
            for group in recommendations(batch)['groups']:
                found[(direction, group['source']['id'])] = group
    selected = []
    for p in pairs:
        group = found.get((p.direction, p.source_id))
        if group is None:
            raise HTTPException(404, 'The selected item was not found.')
        position = next((i for i, r in enumerate(group['recommendations']) if r['id'] == p.target_id), None)
        name = group['source'].get('public_id') or ('a selected buyer' if p.direction == 'buyer' else 'a selected listing')
        if p.target_id and position is None:
            raise HTTPException(409, f'A match of {name} has changed. Remove that pair from the selection and try again.')
        if not p.target_id and group['recommendations']:
            raise HTTPException(409, f'{name} now has matches. Remove its "no match" pair and select again.')
        target = group['recommendations'][position] if position is not None else None
        selected.append((p.direction, group['source'], target, -1 if position is None else position))
    return selected


def person_key(group_by, source):
    """The source's sender / phone group, the same key as workspace_cache.GROUP_KEYS."""
    if group_by == 'phone':
        return source.get('contact_phone') or ''
    return (source.get('author') or '').strip(' ') or workspace_cache.NO_SENDER


def selection_people(selected, group_by):
    """One entry per person (owner of the source side): title, phone and the pairs per direction in app order."""
    people = {}
    for direction, source, target, position in selected:
        person = people.setdefault(person_key(group_by, source), {'sections': {}, 'names': Counter(), 'phones': []})
        person['sections'].setdefault(direction, []).append((source, target, position))
        if source.get('contact_name'):
            person['names'][source['contact_name']] += 1
        if source.get('contact_phone'):
            person['phones'].append(source['contact_phone'])
    result = []
    for key, person in people.items():
        contact_name = person['names'].most_common(1)[0][0] if person['names'] else None
        title = workspace_cache.group_title(group_by, key, contact_name)
        sections = {}
        for direction in SECTION_ORDER:
            items = person['sections'].get(direction)
            if not items:
                continue
            # Sources newest first (as the export of every match), each source's pairs in the list order:
            # ownership tier, then hot, warm, custom, score (the order recommendations() returns).
            items.sort(key=lambda item: (str(item[0]['id']), item[2]))
            items.sort(key=lambda item: item[0].get('sent_at').timestamp() if item[0].get('sent_at') else float('-inf'), reverse=True)
            sections[direction] = items
        result.append({'key': key, 'title': title, 'contact_name': clean_contact_name(contact_name) or '',
                       'phone': (key if group_by == 'phone' else '') or next(iter(person['phones']), ''),
                       'sources': sum(len({str(s['id']) for s, _, _ in items}) for items in sections.values()),
                       'sections': sections})
    return sorted(result, key=lambda person: (-person['sources'], person['key']))


def section_counts(items):
    from report import pair_status
    counts = Counter(pair_status(target) if target else 'unmatched' for _, target, _ in items)
    return {'sources': len({str(source['id']) for source, _, _ in items}), **{name: counts[name] for name in TEMPERATURES},
            'pages': len(items)}


def person_pdf_parts(person, group_by, company_name):
    """The person's sections as in "Export both directions": a coloured cover per direction, "No. N" across both."""
    from report import build_report
    section = pdf_title(group_by, person)
    present = list(person['sections'])
    parts, number = [], 1
    for index, direction in enumerate(present, 1):
        items = person['sections'][direction]
        cover = {**section_counts(items), 'key': person['key'], 'title': section,
                 'kind': 'Nomor telepon' if group_by == 'phone' else 'Pengirim', 'direction': direction,
                 'combined': True, 'part': index, 'parts': len(present), 'phone': person['phone']}
        parts.append(BytesIO(build_report([(source, target) for source, target, _ in items], direction, section=section, cover=cover,
                                          first_pair=number, combined=True, company_name=company_name)))
        number += len(items)
    return parts


@selection_router.post('')
def export_selection(payload: Selection):
    """Hand-picked pairs grouped by the person behind each source (the company's sender / sales contact grouping):
    one PDF per person in a ZIP (``grouped``) or everything in one PDF, each person numbered from page 1."""
    selected = selected_pairs(payload)
    with connect() as conn:
        company = company_row(conn)
    group_by = company['listing_group_by'] or 'sender'
    return people_file(selection_people(selected, group_by), group_by, company['company_name'], payload.output, 'Pilihan-Pencocokan')


def people_file(people, group_by, company_name, output, base):
    """``selection_people`` as one PDF per person in a ZIP (``grouped``) or one PDF, each person from page 1.
    Also used by the Jev-approved export (ai_review.py)."""
    files = [person_pdf_parts(person, group_by, company_name) for person in people]
    if output == 'single':
        content, _ = merge_pdf_parts(*files)
        media_type, filename = 'application/pdf', base + '.pdf'
    else:
        archive = BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for index, (person, parts) in enumerate(zip(people, files)):
                content, count = merge_pdf_parts(parts)
                metadata = {'key': person['key'], 'group_by': group_by, 'contact_name': person['contact_name'],
                            'phone': person['phone'], 'combined': True,
                            'sections': {d: section_counts(items)['sources'] for d, items in person['sections'].items()}}
                bundle.writestr(group_filename(index, metadata, count), content)
        content = archive.getvalue()
        media_type, filename = 'application/zip', base + '.zip'
    return Response(content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
