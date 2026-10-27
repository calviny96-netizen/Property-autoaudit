import json
import uuid

from db import connect
from tenant import workspace_id, workspace_scope
from embedding import embed
from qdrant import query as qdrant_query
from matching_rules import assess_pair, location_radius, prepare_document
from workspace_cache import refresh_workspace_cache
from matchlog import log_matches
from entities import backfill_content_keys
from location_index import load_index
from matching_scope import load_matching_scope
from analysis_window import in_window_sql, window_start
import progress as job_progress


def part_text(row) -> str:
    """What one document says: its own part of a multi-context bubble, else the whole bubble."""
    return row.get('segment_text') or row['raw_text']


def content_group(row) -> str:
    """Copies evaluated once: the content key (v4.10: same content with another signature or casing), else the
    part text. The same key ``entities.group_key`` / the group cache use."""
    return row.get('content_key') or part_text(row)


PURPOSE_OFFERS = {'sale': ('sale', 'both'), 'rent': ('rent', 'both')}


def purpose_fits(request, listing) -> bool:
    """Purpose first (R6): a buy request never meets a rent-only listing and vice versa.

    The same gate assess_pair applies; 'both' and 'unknown' pass. assess_pair then makes a listing that states no
    purpose Custom when the buyer states one (a hard requirement), and Warm at most when neither side states it."""
    want, offer = request.get('transaction_type') or 'unknown', listing.get('transaction_type') or 'unknown'
    return want not in PURPOSE_OFFERS or offer == 'unknown' or offer in PURPOSE_OFFERS[want]


# Storage rule: Hot/Warm pairs are kept from 55 (the views show 60+); a Custom pair only when its score before
# the cap at 79 (assess_pair's pre_cap_score) reached 60 and no later cap took it below 60.
STORED_STATUSES = ('hot', 'warm', 'custom')
MIN_STORED_SCORE = 55.0
MIN_CUSTOM_SCORE = 60.0


def stored(result) -> bool:
    """Whether an assess_pair result becomes a row of xm.matches; its status is stored unchanged."""
    if result is None or result.get('status') not in STORED_STATUSES:
        return False
    score = float(result['score'])
    if result['status'] == 'custom':
        return min(score, float(result.get('pre_cap_score', score))) >= MIN_CUSTOM_SCORE
    return score >= MIN_STORED_SCORE


def _near_duplicate(left_tokens: set[str], right_tokens: set[str]) -> bool:
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.70


def recompute_matches(company_id: str | None = None, limit_per_request: int | None = None, *,
                      source: str = 'recompute', import_id=None, agent_name: str | None = None,
                      matching_mode: str | None = None, progress=None) -> int:
    """Rebuild every match of a workspace.

    ``source='import'`` marks a rebuild caused by new chat data: pairs that were
    never seen before are then recorded as *new matches* for the history view.
    Settings-driven rebuilds only refresh the baseline.
    """
    company_id = company_id or workspace_id()
    with workspace_scope(company_id):
        return _recompute_matches(company_id, limit_per_request, source, import_id, agent_name, matching_mode, progress)


def _recompute_matches(company_id, limit_per_request, source='recompute', import_id=None, agent_name=None, matching_mode=None, progress=None):
    progress = progress or job_progress.current()
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042026))")
        # Documents stored before v4.10 get their content key first, so this run already groups by content.
        backfill_content_keys(conn, company_id)
        scope = load_matching_scope(conn, company_id, matching_mode)
        settings = conn.execute(
            "SELECT * FROM xm.match_settings WHERE company_id = %s", (company_id,)
        ).fetchone()
        # Only messages inside the analysis window take part; older documents keep their rows but get no matches.
        window = window_start(conn, company_id)
        requests = conn.execute(
            "SELECT d.*,r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE d.company_id=%s AND d.document_type='buyer_request' AND d.active"
            + in_window_sql('r') + " ORDER BY d.id",
            (company_id, window),
        ).fetchall()
        listings = conn.execute(
            "SELECT d.*,r.raw_text FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id WHERE d.company_id=%s AND d.document_type='property_listing' AND d.active AND d.review_status='auto'"
            + in_window_sql('r') + " ORDER BY d.id",
            (company_id, window),
        ).fetchall()
        location_index = load_index(conn, company_id)
        radius = location_radius(settings)
        # Copies of one content share one evaluation (a part of a multi-context bubble is
        # its own text); membership preserves every posting/contact and both
        # directions resolve through the group cache.
        unique_listings = {}
        canonical_by_id = {}
        monitored_ids = set()
        for row in listings:
            canonical = unique_listings.setdefault(content_group(row),row)
            canonical_by_id[str(row['id'])] = str(canonical['id'])
            if scope.includes(row):
                monitored_ids.add(str(canonical['id']))
        listings = list(unique_listings.values())
        unique_requests = {}
        for row in requests:
            canonical = unique_requests.setdefault(content_group(row), row)
            if scope.includes(row):
                monitored_ids.add(str(canonical['id']))
        requests = list(unique_requests.values())
        progress.plan(match=len(requests))
        progress.report('match', 0, len(requests))
        for row in requests + listings:
            # matching_rules reads the part's own text (segment_text) for a split bubble.
            prepare_document(row,location_index)
        listings_by_id = {str(row['id']):row for row in listings}
        listing_tokens = {str(row["id"]): set(row["normalized_text"].split()) for row in listings}
        listings_by_category = {}
        monitored_by_category = {}
        listings_by_category_location = {}
        listings_by_category_broad = {}
        listings_by_cluster = {}
        unlocated_by_category = {}
        places = location_index.places
        for listing in listings:
            for cluster in listing['_geo_ids']:
                for category in listing['categories'] or []:
                    listings_by_cluster.setdefault((category, cluster), []).append(listing)
            # A place also retrieves for every area that contains it ("Surabaya Barat" finds Citraland);
            # so does a cluster the location index placed the listing in (its names and development).
            names = list(listing["locations"] or [])
            for cluster in listing['_geo_ids']:
                if cluster in location_index.clusters:
                    names += location_index.cluster_places(cluster)
            location_keys = set()
            for location in names:
                location_keys.update((location, places.canonical(location)))
                location_keys.update(places.ancestors(location))
            # A listing that only names a broad area ("Surabaya", "Surabaya Barat") is retrieved for
            # buyers asking for any place inside it; assess_pair decides warm or custom.
            named = {places.canonical(location) for location in listing["locations"] or []}
            broad = set() if listing['_geo_ids'] else \
                {p for p in named if places.located(p) and not any(p in places.ancestors(o) for o in named if o != p)}
            located = listing['_geo_ids'] or any(places.located(location) for location in listing["locations"] or [])
            for category in listing["categories"] or []:
                listings_by_category.setdefault(category, []).append(listing)
                if str(listing['id']) in monitored_ids:
                    monitored_by_category.setdefault(category, []).append(listing)
                for location in location_keys:
                    listings_by_category_location.setdefault((category, location), []).append(listing)
                for location in broad:
                    listings_by_category_broad.setdefault((category, location), []).append(listing)
                if not located:
                    unlocated_by_category.setdefault(category, []).append(listing)
        conn.execute("DELETE FROM xm.matches WHERE company_id=%s", (company_id,))
        inserted = 0
        pending = []
        insert_sql = """INSERT INTO xm.matches(id,company_id,buyer_request_id,property_listing_id,score,
            location_score,land_score,building_score,price_score,semantic_score,explanation,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)"""
        for request_number, request in enumerate(requests,1):
            request_categories = set(request["categories"] or [])
            request_tokens = set(request["normalized_text"].split())
            request_vector = embed(request["normalized_text"])
            candidate_rows = {}
            try:
                categories_to_query = list(request_categories) or [None]
                for category in categories_to_query:
                    purposes = PURPOSE_OFFERS.get(request.get('transaction_type') or 'unknown')
                    for hit in qdrant_query(request_vector, document_type="property_listing", category=category, limit=150,
                                            transaction_types=[*purposes, 'unknown'] if purposes else None):
                        postgres_id = canonical_by_id.get(hit.get("payload", {}).get("postgres_id"))
                        if postgres_id in listings_by_id:
                            candidate_rows[postgres_id] = (listings_by_id[postgres_id], float(hit.get("score", 0)))
            except Exception:
                for category in request_categories:
                    for listing in listings_by_category.get(category, [])[:250]:
                        candidate_rows[str(listing["id"])] = (listing, None)
            # Structured retrieval guarantees that exact-location candidates
            # are considered even when they are outside Qdrant's semantic top-K.
            for category in request_categories:
                # Company/watchlist evidence must not disappear behind semantic
                # top-K or an unindexed location. Preserve legacy retrieval for
                # companies that explicitly use the unfiltered default.
                if scope.mode == 'sales' or scope.terms:
                    scoped_candidates = (listings_by_category if str(request['id']) in monitored_ids else monitored_by_category)
                    for listing in scoped_candidates.get(category, []):
                        candidate_rows.setdefault(str(listing['id']), (listing, None))
                structured_candidates = {}
                for location in request["locations"] or []:
                    for listing in listings_by_category_location.get((category, places.canonical(location)), []):
                        structured_candidates[str(listing["id"])] = listing
                    for area in places.ancestors(location):
                        for listing in listings_by_category_broad.get((category, area), []):
                            structured_candidates[str(listing["id"])] = listing
                if request.get('location_strict'):
                    # Unconfirmed locations are what the CUSTOM status is for.
                    for listing in unlocated_by_category.get(category, []):
                        structured_candidates[str(listing["id"])] = listing
                for listing_id, listing in structured_candidates.items():
                    candidate_rows.setdefault(listing_id, (listing, None))
            candidates = []
            # Direct imported edges also retrieve candidates outside semantic top-K, within
            # the company's nearby radius (assess_pair rates those alternatives Custom).
            # Strict-location buyers get no nearby alternatives.
            for cluster in request['_geo_ids']:
                nearby = {cluster} if request.get('location_strict') else \
                    {cluster, *(other for other, km in location_index.neighbors.get(cluster, {}).items() if km <= radius)}
                for category in request_categories:
                    for target in nearby:
                        for listing in listings_by_cluster.get((category, target), []):
                            candidate_rows.setdefault(str(listing['id']), (listing, None))
            for listing, qdrant_semantic in candidate_rows.values():
                if str(request['id']) not in monitored_ids and str(listing['id']) not in monitored_ids:
                    continue
                if not purpose_fits(request, listing):
                    continue
                # Forwarded request posts can appear in multiple WhatsApp
                # groups. Never recommend the same text back as a property.
                if _near_duplicate(listing_tokens[str(listing["id"])], request_tokens):
                    continue
                semantic = max(0.0, qdrant_semantic) * 100 if qdrant_semantic is not None else 50.0
                # Hard attributes (unit type, tower, legal status, floor, financing) are decided by assess_pair;
                # a nearby-cluster alternative comes back as Custom.
                result = assess_pair(request, listing, settings, semantic, location_index)
                if not stored(result):
                    continue
                candidates.append((result['score'], listing, result['location'], result['land'],
                                   result['building'], result['price'], result['semantic'], result['explanation'],
                                   result['status']))
            candidates.sort(key=lambda item: item[0], reverse=True)
            unique_candidates = candidates if limit_per_request is None else candidates[:limit_per_request]
            for score, listing, loc, land, building, price, semantic, explanation, status in unique_candidates:
                pending.append((uuid.uuid4(),company_id,request['id'],listing['id'],round(score,2),round(loc,2),
                                round(land,2),round(building,2),round(price,2),round(semantic,2),json.dumps(explanation),status))
                inserted += 1
            if len(pending)>=1000:
                with conn.cursor() as cur: cur.executemany(insert_sql,pending)
                pending=[]
            if request_number % 100 == 0:
                print(f'Matching {request_number}/{len(requests)} unique buyers; {inserted} pairs',flush=True)
            if request_number % 25 == 0:
                progress.report('match', request_number, len(requests))
        if pending:
            with conn.cursor() as cur: cur.executemany(insert_sql,pending)
        progress.report('match', len(requests), len(requests), force=True)
        progress.report('finish', 0, 0, force=True)
        refresh_workspace_cache(conn,company_id)
        # The mode applied by this upload also governs later maintenance runs.
        if matching_mode is not None:
            conn.execute('UPDATE xm.app_preferences SET matching_mode=%s, updated_at=now() WHERE company_id=%s',
                         (scope.mode, company_id))
        found = log_matches(conn, company_id, source, import_id, agent_name)
        if source == 'import':
            import stock
            stock.snapshot_stock(conn, company_id, 'import', import_id=import_id, agent_name=agent_name)
        conn.execute(
            "INSERT INTO xm.audit_events(event_type, entity_type, details) VALUES ('matching_completed','match',%s::jsonb)",
            (json.dumps({"matches": inserted, "requests": len(requests), "listings": len(listings), "new_matches": found['new'], "source": source, "matching_mode": scope.mode}),),
        )
        conn.commit()
        return inserted
