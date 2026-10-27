"""Company location index: imported cluster table plus small edits (clusters, aliases, neighbours).

Saving only saves: results are marked out of date (``mark_reprocess_pending``) and a
company admin rebuilds them with POST /reprocess. Every write is serialised per
company and may carry the ``revision`` the editor was showing.
"""
import copy
import hashlib
import json
import math
from collections import Counter

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from db import connect
from location_index import parse_table, LocationIndex, key
from matching_scope import mark_reprocess_pending, reprocess_status

router = APIRouter(prefix='/location-index')
EMPTY = {'clusters': [], 'edges': []}
MAX_CLUSTERS, MAX_EDGES, MAX_ALIASES, MAX_NAME = 5000, 100000, 100, 200
REASON = 'Location index updated'


def current(conn):
    row = conn.execute("SELECT * FROM xm.location_indexes WHERE company_id=current_setting('xm.workspace_id')").fetchone()
    data = row['data'] if row else EMPTY
    revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data, revision, row


def _lock(conn):
    # Same short per-company lock as the other settings writes and job hand-offs
    # (matching_scope / worker). Never the long-running matching lock: an edit must
    # not wait for an upload or a reprocess to finish.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")


def _check_revision(expected, revision):
    if expected is not None and expected != revision:
        raise HTTPException(409, 'Your locations were changed in the meantime. Reload and try again.')


def _save(conn, data, row, event, details, source=None):
    """Store a changed index in the caller's locked transaction and mark results out of date."""
    sources = list(dict.fromkeys([*(row['sources'] if row else []), *([source] if source else [])]))
    conn.execute("""INSERT INTO xm.location_indexes(company_id,data,sources) VALUES(current_setting('xm.workspace_id'),%s::jsonb,%s::jsonb)
      ON CONFLICT(company_id) DO UPDATE SET data=excluded.data,sources=excluded.sources,updated_at=now()""", (json.dumps(data), json.dumps(sources)))
    mark_reprocess_pending(conn, REASON)
    conn.execute("INSERT INTO xm.audit_events(event_type,entity_type,details) VALUES(%s,'location_index',%s::jsonb)",
                 (event, json.dumps(details, default=str)))


def _edit(expected_revision, change, event):
    """Load → ``change(data)`` → save, in one locked transaction.

    ``change`` mutates a private copy and returns (result fields, audit details).
    An edit that changes nothing is reported as ``unchanged`` and marks nothing.
    """
    with connect() as conn:
        _lock(conn)
        data, revision, row = current(conn)
        _check_revision(expected_revision, revision)
        updated = copy.deepcopy(data)
        result, details = change(updated)
        if updated == data:
            return {**result, 'revision': revision, 'unchanged': True, 'job': None}
        _save(conn, updated, row, event, details)
        conn.commit()
        _, revision, _ = current(conn)
        status = reprocess_status(conn)
        conn.commit()
    return {**result, 'revision': revision, 'unchanged': False, 'job': None, 'reprocess': status}


# ---------------------------------------------------------------- validation helpers
def _text(value, label, required=True):
    value = ' '.join(str(value or '').split())
    if (required and not key(value)) or len(value) > MAX_NAME:
        raise HTTPException(400, f'{label} is required (max. {MAX_NAME} characters).' if required
                            else f'{label} can have at most {MAX_NAME} characters.')
    return value


def _cluster(data, value, label='Cluster'):
    """Find a cluster by id, name or explicit alias."""
    value = str(value or '').strip()
    clusters = {c['id']: c for c in data['clusters']}
    if value in clusters:
        return clusters[value]
    term = key(value)
    if term:
        found = clusters.get(term) or next((c for c in data['clusters'] if term in {key(c['name']), *map(key, c['aliases'])}), None)
        if found:
            return found
    raise HTTPException(404, f'{label} not found: {value[:100]}' if value else f'{label} not found.')


def _terms_of(cluster):
    return {key(cluster['name']), *(key(a) for a in cluster['aliases'])} - {''}


def _assert_unique(data, ident, names):
    """Names and aliases must identify one cluster (same rule as the CSV import)."""
    for other in data['clusters']:
        if other['id'] == ident:
            continue
        taken = _terms_of(other)
        for name in names:
            if key(name) in taken:
                raise HTTPException(409, f'"{name}" is already used by the cluster {other["name"]}.')


def _distance(value):
    try:
        distance = float(value)
    except (TypeError, ValueError):
        distance = math.nan
    if not math.isfinite(distance) or not 0 < distance <= 4:
        raise HTTPException(400, 'Distance must be more than 0 and at most 4 km.')
    return round(distance, 3)


def _pair(a, b):
    return tuple(sorted((a, b)))


# ---------------------------------------------------------------- read
@router.get('')
def list_locations(search: str='', offset: int=0):
    with connect() as conn:
        data, revision, row = current(conn)
    index=LocationIndex(data)
    clusters=[{**c,'neighbor_count':len(index.neighbors[c['id']])} for c in data['clusters'] if not search.strip() or key(search) in key(' '.join([c['name'],c['area'],*c['aliases']]))]
    clusters.sort(key=lambda c:c['name'])
    start=max(0,offset)
    return {'clusters':clusters[start:start+30],'has_more':len(clusters)>start+30,'total':len(data['clusters']),
            'pairs':len(data['edges']),'filtered_total':len(clusters),'revision':revision,
            'updated_at':row['updated_at'] if row else None,'sources':row['sources'] if row else []}


@router.get('/neighbors')
def neighbors(cluster: str):
    with connect() as conn:
        data, _, _ = current(conn)
    index=LocationIndex(data)
    ident=cluster if cluster in index.clusters else index.terms.get(key(cluster))
    if ident not in index.clusters:
        raise HTTPException(404,'Cluster not found.')
    return {'cluster':index.clusters[ident], 'neighbors':sorted([
        {**index.clusters[target], 'distance_km':distance} for target,distance in index.neighbors[ident].items()
    ],key=lambda c:(c['distance_km'],c['name']))}


@router.get('/areas')
def areas():
    """Areas/developments with their cluster counts, for the cluster editor's area picker."""
    with connect() as conn:
        data, _, _ = current(conn)
    counts, names = Counter(), {}
    for cluster in data['clusters']:
        area = (cluster.get('area') or '').strip()
        if key(area):
            counts[key(area)] += 1
            names.setdefault(key(area), area)
    return {'areas': sorted(({'area': names[k], 'clusters': n} for k, n in counts.items()), key=lambda a: a['area'].casefold())}


# ---------------------------------------------------------------- CSV import
class ImportLocations(BaseModel):
    text: str = Field(min_length=1, max_length=3000000)
    source_name: str = Field(default='Location CSV', min_length=1, max_length=200)
    revision: str | None = None


def prepare(payload, existing):
    try:
        data = parse_table(payload.text, existing)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return data


def summary(data, previous):
    return {'clusters':len(data['clusters']), 'pairs':len(data['edges']),
            'new_clusters':len(data['clusters'])-len(previous['clusters']),
            'new_pairs':len(data['edges'])-len(previous['edges']),
            'new_aliases':sum(len(c['aliases']) for c in data['clusters'])-sum(len(c['aliases']) for c in previous['clusters'])}


@router.post('/preview')
def preview(payload: ImportLocations):
    with connect() as conn:
        previous, revision, _ = current(conn)
    data=prepare(payload,previous)
    return {**summary(data,previous),'revision':revision,'source_name':payload.source_name}


@router.post('/import')
def import_locations(payload: ImportLocations):
    with connect() as conn:
        _lock(conn)
        previous, revision, row = current(conn)
        if payload.revision != revision:
            raise HTTPException(409,'Your locations changed since the check. Check the table again before adding it.')
        data=prepare(payload,previous)
        result=summary(data,previous)
        if data == previous:
            return {**result,'unchanged':True,'job':None}
        _save(conn, data, row, 'locations_imported', result, source=payload.source_name)
        conn.commit()
        status = reprocess_status(conn)
        conn.commit()
    return {**result,'unchanged':False,'job':None,'reprocess':status}


# ---------------------------------------------------------------- edits
class ClusterInput(BaseModel):
    name: str = Field(max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=1000)
    area: str = Field(default='', max_length=1000)
    original_id: str | None = Field(default=None, max_length=1000)
    revision: str | None = None


class AliasInput(BaseModel):
    cluster_id: str = Field(max_length=1000)
    alias: str = Field(max_length=1000)
    revision: str | None = None


class EdgeInput(BaseModel):
    a: str = Field(max_length=1000)
    b: str = Field(max_length=1000)
    distance_km: float
    revision: str | None = None


@router.post('/clusters')
def save_cluster(payload: ClusterInput):
    """Create a cluster, or update/rename the one named by ``original_id``."""
    name = _text(payload.name, 'Cluster name')
    area = _text(payload.area, 'Area/development', required=False)
    aliases = {}
    for alias in payload.aliases:
        alias = _text(alias, 'Alias', required=False)
        if key(alias) and key(alias) != key(name):
            aliases.setdefault(key(alias), alias)
    if len(aliases) > MAX_ALIASES:
        raise HTTPException(400, f'A cluster can have at most {MAX_ALIASES} aliases.')
    ident = key(name)

    def change(data):
        original = _cluster(data, payload.original_id) if payload.original_id is not None else None
        clash = next((c for c in data['clusters'] if c['id'] == ident and c is not original), None)
        if clash:
            raise HTTPException(409, f'A cluster named {clash["name"]} already exists. Edit that cluster instead.')
        _assert_unique(data, original['id'] if original else ident, [name, *aliases.values()])
        cluster = {'id': ident, 'name': name, 'aliases': list(aliases.values()), 'area': area}
        if original is None:
            if len(data['clusters']) >= MAX_CLUSTERS:
                raise HTTPException(400, f'At most {MAX_CLUSTERS:,} clusters are allowed.')
            data['clusters'].append(cluster)
            return {'cluster': cluster}, {'action': 'cluster_created', 'cluster': ident}
        old = original['id']
        data['clusters'][data['clusters'].index(original)] = cluster
        if old != ident:
            # Renaming changes the id: move its neighbour links along.
            edges = {}
            for edge in data['edges']:
                a, b = (ident if edge['a'] == old else edge['a']), (ident if edge['b'] == old else edge['b'])
                edges[_pair(a, b)] = edge['distance_km']
            data['edges'] = [{'a': a, 'b': b, 'distance_km': d} for (a, b), d in sorted(edges.items())]
        return {'cluster': cluster, 'previous_id': old}, {'action': 'cluster_updated', 'cluster': ident, 'previous_id': old}
    return _edit(payload.revision, change, 'location_index_edited')


@router.delete('/clusters/{cluster_id}')
def delete_cluster(cluster_id: str, revision: str | None = None):
    def change(data):
        cluster = _cluster(data, cluster_id)
        data['clusters'] = [c for c in data['clusters'] if c['id'] != cluster['id']]
        before = len(data['edges'])
        data['edges'] = [e for e in data['edges'] if cluster['id'] not in (e['a'], e['b'])]
        removed = before - len(data['edges'])
        return ({'deleted': cluster['id'], 'removed_pairs': removed},
                {'action': 'cluster_deleted', 'cluster': cluster['id'], 'removed_pairs': removed})
    return _edit(revision, change, 'location_index_edited')


@router.post('/aliases')
def add_alias(payload: AliasInput):
    alias = _text(payload.alias, 'Alias')

    def change(data):
        cluster = _cluster(data, payload.cluster_id)
        if key(alias) not in _terms_of(cluster):
            _assert_unique(data, cluster['id'], [alias])
            cluster['aliases'].append(alias)
            if len(cluster['aliases']) > MAX_ALIASES:
                raise HTTPException(400, f'A cluster can have at most {MAX_ALIASES} aliases.')
        return {'cluster': cluster}, {'action': 'alias_added', 'cluster': cluster['id'], 'alias': alias}
    return _edit(payload.revision, change, 'location_index_edited')


@router.delete('/aliases')
def delete_alias(cluster_id: str, alias: str, revision: str | None = None):
    def change(data):
        cluster = _cluster(data, cluster_id)
        kept = [a for a in cluster['aliases'] if key(a) != key(alias)]
        if len(kept) == len(cluster['aliases']):
            raise HTTPException(404, f'Alias not found for {cluster["name"]}: {alias[:100]}')
        cluster['aliases'] = kept
        return {'cluster': cluster}, {'action': 'alias_deleted', 'cluster': cluster['id'], 'alias': alias}
    return _edit(revision, change, 'location_index_edited')


@router.post('/edges')
def save_edge(payload: EdgeInput):
    """Add or update the distance between two clusters (both directions)."""
    distance = _distance(payload.distance_km)

    def change(data):
        a, b = _cluster(data, payload.a)['id'], _cluster(data, payload.b)['id']
        if a == b:
            raise HTTPException(400, 'A cluster cannot be its own neighbor.')
        pair = _pair(a, b)
        edges = {_pair(e['a'], e['b']): e['distance_km'] for e in data['edges']}
        if pair not in edges and len(edges) >= MAX_EDGES:
            raise HTTPException(400, f'At most {MAX_EDGES:,} neighbor links are allowed.')
        edges[pair] = distance
        data['edges'] = [{'a': x, 'b': y, 'distance_km': d} for (x, y), d in sorted(edges.items())]
        edge = {'a': pair[0], 'b': pair[1], 'distance_km': distance}
        return {'edge': edge}, {'action': 'edge_saved', **edge}
    return _edit(payload.revision, change, 'location_index_edited')


@router.delete('/edges')
def delete_edge(a: str, b: str, revision: str | None = None):
    def change(data):
        pair = _pair(_cluster(data, a)['id'], _cluster(data, b)['id'])
        kept = [e for e in data['edges'] if _pair(e['a'], e['b']) != pair]
        if len(kept) == len(data['edges']):
            raise HTTPException(404, 'These clusters are not linked as neighbors.')
        data['edges'] = kept
        return {'deleted': {'a': pair[0], 'b': pair[1]}}, {'action': 'edge_deleted', 'a': pair[0], 'b': pair[1]}
    return _edit(revision, change, 'location_index_edited')
