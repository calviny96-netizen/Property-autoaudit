"""Company knowledge (glossary + location clusters) as CSV, and copying it between companies (platform admin).

The CSV files use the same columns the import dialogs accept, so an export of one company can be imported by
another by hand; the platform admin can also copy directly from the Companies page."""
import csv
import io
import json
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import activity
from auth import current_user
from db import connect
from location_index import key
from location_routes import EMPTY, REASON as LOCATIONS_REASON, _lock as _lock_locations
from matching_scope import mark_reprocess_pending
from reindex import glossary_entries
from tenant import workspace_scope

router = APIRouter()
GLOSSARY_COLUMNS = ('term', 'canonical', 'kind', 'parent', 'note')
CLUSTER_COLUMNS = ('Cluster', 'Alias', 'Area/Development', 'Cluster Terdekat (dalam radius 4 km)')


def _csv(rows, columns):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow(columns)
    writer.writerows(rows)
    return '﻿' + out.getvalue()


def _filename(conn, stem):
    row = conn.execute("SELECT company_name FROM xm.app_preferences WHERE company_id=current_setting('xm.workspace_id')").fetchone()
    name = re.sub(r'[^A-Za-z0-9]+', '-', (row or {}).get('company_name') or 'company').strip('-').lower() or 'company'
    return f'{stem}-{name}.csv'


def _attachment(text, filename):
    return Response(text.encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def glossary_rows(entries):
    return [(e['term'], e['canonical'], e.get('kind') or 'synonym', e.get('parent') or '', e.get('note') or '') for e in entries]


def cluster_rows(data):
    """One row per cluster; neighbours as "Name (km); Name (km)" exactly as the import reads them."""
    names = {c['id']: c['name'] for c in data.get('clusters') or []}
    nearby = {}
    for edge in data.get('edges') or []:
        distance = edge['distance_km']
        text = f'{distance:g}'
        nearby.setdefault(edge['a'], []).append(f"{names.get(edge['b'], edge['b'])} ({text})")
        nearby.setdefault(edge['b'], []).append(f"{names.get(edge['a'], edge['a'])} ({text})")
    rows = []
    for cluster in sorted(data.get('clusters') or [], key=lambda c: c['name'].lower()):
        rows.append((cluster['name'], '; '.join(cluster.get('aliases') or []), cluster.get('area') or '',
                     '; '.join(sorted(nearby.get(cluster['id'], [])))))
    return rows


@router.get('/glossary/export.csv')
def export_glossary():
    with connect() as conn:
        entries = glossary_entries(conn)
        filename = _filename(conn, 'glossary')
    return _attachment(_csv(glossary_rows(entries), GLOSSARY_COLUMNS), filename)


@router.get('/location-index/export.csv')
def export_clusters():
    with connect() as conn:
        row = conn.execute("SELECT data FROM xm.location_indexes WHERE company_id=current_setting('xm.workspace_id')").fetchone()
        filename = _filename(conn, 'clusters')
    return _attachment(_csv(cluster_rows(row['data'] if row else EMPTY), CLUSTER_COLUMNS), filename)


# ---------------------------------------------------------------- platform admin: copy between companies

class CopyKnowledge(BaseModel):
    source_company_id: str
    glossary: bool = True
    locations: bool = True
    mode: Literal['merge', 'replace'] = 'merge'


def _merge_index(target, source):
    """Clusters the target lacks are added; shared clusters gain the source's aliases (the target keeps its area);
    links are added where the target has none. Returns (data, added clusters, added links)."""
    clusters = {c['id']: {**c, 'aliases': list(c.get('aliases') or [])} for c in target.get('clusters') or []}
    terms = {}
    for cluster in clusters.values():
        for alias in [cluster['name'], *cluster['aliases']]:
            terms.setdefault(key(alias), cluster['id'])
    added = 0
    for cluster in source.get('clusters') or []:
        if cluster['id'] in clusters:
            own = clusters[cluster['id']]
            for alias in cluster.get('aliases') or []:
                term = key(alias)
                if term and term not in terms:
                    own['aliases'].append(alias)
                    terms[term] = own['id']
            own['area'] = own.get('area') or cluster.get('area') or ''
            continue
        if any(key(a) in terms for a in [cluster['name'], *(cluster.get('aliases') or [])]):
            continue  # a name the target already uses for another cluster: never make a term ambiguous
        clusters[cluster['id']] = {**cluster, 'aliases': list(cluster.get('aliases') or [])}
        for alias in [cluster['name'], *clusters[cluster['id']]['aliases']]:
            terms.setdefault(key(alias), cluster['id'])
        added += 1
    edges = {tuple(sorted((e['a'], e['b']))): e['distance_km'] for e in target.get('edges') or []}
    links = 0
    for edge in source.get('edges') or []:
        pair = tuple(sorted((edge['a'], edge['b'])))
        if pair in edges or pair[0] not in clusters or pair[1] not in clusters:
            continue
        edges[pair] = edge['distance_km']
        links += 1
    data = {'clusters': list(clusters.values()), 'edges': [{'a': a, 'b': b, 'distance_km': d} for (a, b), d in sorted(edges.items())]}
    return data, added, links


@router.post('/admin/companies/{company_id}/copy-knowledge')
def copy_knowledge(company_id: str, payload: CopyKnowledge, request: Request):
    """Copy the glossary and/or the cluster table of one company into another. ``merge`` adds what the target
    lacks; ``replace`` makes the target an exact copy. The target's results are marked out of date."""
    me = current_user(request)
    source_id = payload.source_company_id
    if source_id == company_id:
        raise HTTPException(400, 'Choose a different company to copy from.')
    if not payload.glossary and not payload.locations:
        raise HTTPException(400, 'Choose the glossary, the clusters, or both.')
    with connect() as conn:
        known = {r['company_id'] for r in conn.execute('SELECT company_id FROM xm.app_preferences WHERE company_id = ANY(%s)',
                                                       ([source_id, company_id],)).fetchall()}
        if company_id not in known or source_id not in known:
            raise HTTPException(404, 'Company not found.')
        source_glossary = conn.execute('SELECT alias, canonical, kind, parent, note FROM xm.glossary WHERE company_id=%s ORDER BY alias',
                                       (source_id,)).fetchall()
        source_row = conn.execute('SELECT data, sources FROM xm.location_indexes WHERE company_id=%s', (source_id,)).fetchone()
    source_index = source_row['data'] if source_row else EMPTY
    result = {'glossary_added': 0, 'glossary_total': None, 'clusters_added': 0, 'links_added': 0, 'clusters_total': None, 'mode': payload.mode}
    with workspace_scope(company_id):
        with connect() as conn:
            _lock_locations(conn)
            if payload.glossary:
                existing = {r['alias'].lower() for r in conn.execute("SELECT alias FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id')").fetchall()}
                if payload.mode == 'replace':
                    conn.execute("DELETE FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id')")
                    existing = set()
                rows = [(r['alias'], r['canonical'], r['kind'] or 'synonym', r['parent'], r['note']) for r in source_glossary
                        if r['alias'].lower() not in existing]
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany('INSERT INTO xm.glossary(alias, canonical, kind, parent, note) VALUES (%s,%s,%s,%s,%s)', rows)
                result['glossary_added'] = len(rows)
                result['glossary_total'] = conn.execute("SELECT count(*) AS n FROM xm.glossary WHERE company_id=current_setting('xm.workspace_id')").fetchone()['n']
                if rows or payload.mode == 'replace':
                    mark_reprocess_pending(conn, 'Glossary copied from another company')
            if payload.locations:
                row = conn.execute("SELECT data, sources FROM xm.location_indexes WHERE company_id=current_setting('xm.workspace_id')").fetchone()
                target = row['data'] if row else EMPTY
                if payload.mode == 'replace':
                    data = json.loads(json.dumps(source_index))
                    added, links = len(data.get('clusters') or []), len(data.get('edges') or [])
                else:
                    data, added, links = _merge_index(target, source_index)
                if added or links or payload.mode == 'replace':
                    sources = list(dict.fromkeys([*(row['sources'] if row and payload.mode == 'merge' else []), f'copied from {source_id}']))
                    conn.execute("""INSERT INTO xm.location_indexes(company_id,data,sources) VALUES(current_setting('xm.workspace_id'),%s::jsonb,%s::jsonb)
                      ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,sources=excluded.sources,updated_at=now()""",
                                 (json.dumps(data), json.dumps(sources)))
                    mark_reprocess_pending(conn, LOCATIONS_REASON)
                result['clusters_added'], result['links_added'] = added, links
                result['clusters_total'] = len(data.get('clusters') or [])
            activity.record(conn, 'knowledge_copied', company_id=company_id, user=me,
                            detail={'source_company_id': source_id, **{k: v for k, v in result.items() if v is not None}})
            conn.commit()
    return result
