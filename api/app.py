import hashlib
import json
import os
import shutil
import uuid
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import connect, ensure_schema
from tenant import workspace_id, workspace_scope
from embedding import embed
from matcher import recompute_matches
from parser import parse_message
from qdrant import ensure_collection
from qdrant import query as qdrant_query
from qdrant import status as qdrant_status
from access import PUBLIC_PATHS, SESSION_PATHS, allowed, denial_message
import activity
from auth import current_user, router as auth_router, seed_admin
from integration import authenticate as integration_authenticate, is_export_path, is_import_path, router as integration_router
from internal_api import authenticate as internal_authenticate
from entities import GROUP_KEY, migrate_v4
from runtime_cache import daily_snapshot
from matching_scope import document_scope_filter, history_scope_filter
import ownership


UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class BuyerBatchRequest(BaseModel):
    buyer_ids: list[uuid.UUID]
    limit_per_buyer: int = 20


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema()
    seed_admin()
    migrate_v4()
    try:
        ensure_collection()
    except Exception as exc:
        print(f"Qdrant collection initialization deferred: {exc}", flush=True)
    yield


app = FastAPI(title="Property Auto Audit API", version="4.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:9004", "http://localhost:9004"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path.rstrip('/') or '/'
    scheme, _, bearer = request.headers.get('Authorization', '').partition(' ')
    if scheme.lower() == 'bearer' and bearer.startswith('xm_internal_'):
        try:
            internal = internal_authenticate(request)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        request.state.xm_user = internal['user']
        with workspace_scope(internal['company_id'], internal['user']['id']):
            response = await call_next(request)
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Vary'] = 'Authorization, X-XM-Company-Id'
            return response
    if is_export_path(path) or is_import_path(path):
        try:
            key = integration_authenticate(request)
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        request.state.xm_integration_key = key
        with workspace_scope(key['company_id']):
            response = await call_next(request)
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Vary'] = 'Authorization'
            return response
    if path not in PUBLIC_PATHS:
        user = current_user(request)
        if not user:
            return JSONResponse({'detail': 'Please sign in.'}, status_code=401)
        if path not in SESSION_PATHS:
            if user['is_locked']:
                return JSONResponse({'detail': 'Your account is locked. Ask your admin to unlock it.', 'code': 'account_locked'}, status_code=403)
            if user.get('must_change_password'):
                return JSONResponse({'detail': 'Please choose a new password first.', 'code': 'password_change_required'}, status_code=403)
            company_status = user.get('company_status') or 'active'
            if user['role'] != 'admin' and company_status != 'active':
                messages = {'pending': 'Your company registration is still being reviewed by the administrator.',
                            'inactive': 'Your company has been deactivated. Contact the administrator.',
                            'rejected': 'Your company registration was not approved.'}
                return JSONResponse({'detail': messages.get(company_status, 'Your company is not active.'),
                                     'code': f'company_{company_status}'}, status_code=403)
        if not allowed(user, request.method, path):
            return JSONResponse({'detail': denial_message(request.method, path)}, status_code=403)
        request.state.xm_user = user
        target_id = request.headers.get('X-XM-User-Id')
        owner = user
        if target_id:
            try:
                target_id = uuid.UUID(target_id)
            except ValueError:
                return JSONResponse({'detail': 'Invalid target account.'}, status_code=400)
            if str(target_id) != str(user['id']):
                # Only the platform administrator may act inside another account's company.
                if user['role'] != 'admin':
                    return JSONResponse({'detail': 'You can only access your own workspace.'}, status_code=403)
                with connect() as conn:
                    owner = conn.execute('SELECT id,email,workspace_id FROM xm.users WHERE id=%s', (target_id,)).fetchone()
                if not owner:
                    return JSONResponse({'detail': 'Target account not found.'}, status_code=404)
        target_company = request.headers.get('X-XM-Company-Id', '').strip()
        if target_company:
            if target_id and target_company != owner.get('workspace_id'):
                return JSONResponse({'detail': 'Company and target account do not match.'}, status_code=400)
            if target_company != owner.get('workspace_id'):
                if user['role'] != 'admin':
                    return JSONResponse({'detail': 'You can only access your own company.'}, status_code=403)
                with connect() as conn:
                    exists = conn.execute('SELECT 1 FROM xm.app_preferences WHERE company_id=%s', (target_company,)).fetchone()
                if not exists:
                    return JSONResponse({'detail': 'Company not found.'}, status_code=404)
                owner = {**user, 'workspace_id': target_company}
        if not owner.get('workspace_id'):
            return JSONResponse({'detail': 'This account\'s workspace is not ready yet.'}, status_code=503)
        started = time.monotonic()
        with workspace_scope(owner['workspace_id'], owner['id']):
            response = await call_next(request)
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Vary'] = 'Cookie, X-XM-User-Id, X-XM-Company-Id'
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and response.status_code < 400:
            action = activity.action_for(request.method, path)
            if action:
                detail = {'acting_for': owner['workspace_id']} if owner is not user else None
                activity.log(action, company_id=owner['workspace_id'], user=user, method=request.method, path=path,
                             status=response.status_code, detail=detail, duration_ms=int((time.monotonic() - started) * 1000),
                             ip=request.headers.get('X-Real-IP') or (request.client.host if request.client else None))
        return response
    return await call_next(request)


app.include_router(auth_router)
app.include_router(integration_router)


@app.get("/health")
def health():
    postgres_ok = False
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
            postgres_ok = True
    except Exception:
        pass
    return {"ok": postgres_ok, "postgres": postgres_ok, "qdrant": qdrant_status(include_counts=False)}


@app.get("/stats")
def stats(refresh: bool = False):
    return daily_snapshot(workspace_id(), 'stats', load_stats, refresh=refresh)


def load_stats():
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        row = conn.execute(f'''WITH owned AS (
          SELECT d.id,d.document_type,d.entity_id,coalesce(e.status,'ready') status,d.review_status,
                 {GROUP_KEY} part_text
          FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
          LEFT JOIN xm.entities e ON e.entity_id=d.entity_id
          WHERE d.company_id=current_setting('xm.workspace_id') AND d.active ''' + scope_sql + '''
        ) SELECT
          (SELECT count(*) FROM xm.raw_messages WHERE company_id=current_setting('xm.workspace_id')) raw_messages,
          (SELECT count(DISTINCT part_text) FROM owned WHERE document_type='buyer_request' AND status<>'deleted') buyer_requests,
          (SELECT count(DISTINCT part_text) FROM owned WHERE document_type='property_listing' AND status<>'deleted') listings,
          (SELECT count(DISTINCT part_text) FROM owned WHERE document_type='buyer_request' AND status='ready') buyers_ready,
          (SELECT count(DISTINCT part_text) FROM owned WHERE document_type='property_listing' AND status='ready') listings_ready,
          (SELECT count(*) FROM xm.matches m WHERE m.company_id=current_setting('xm.workspace_id')
            AND (m.buyer_request_id IN (SELECT id FROM owned) OR m.property_listing_id IN (SELECT id FROM owned))) matches,
          (SELECT count(*) FROM owned WHERE review_status='review') needs_review''', scope_params).fetchone()
    return {**row, "qdrant": qdrant_status()}


@app.get("/imports")
def imports():
    """Import rows are read fresh (they carry the live progress); the per-import match counts scan the match
    history through the company scope, so they are shared for a few seconds between the polls of open tabs."""
    from runtime_cache import cached_value

    def match_counts():
        with connect() as conn:
            scope_sql, scope_params = history_scope_filter(conn)
            rows = conn.execute(
                """SELECT import_id, count(*) total, count(*) FILTER (WHERE last_status='hot') hot,
                          count(*) FILTER (WHERE last_status='warm') warm, count(*) FILTER (WHERE last_status='custom') custom
                   FROM xm.match_events me WHERE me.company_id=current_setting('xm.workspace_id') AND me.source='import' AND me.active
                   """ + scope_sql + " GROUP BY import_id", scope_params).fetchall()
        return {str(row['import_id']): row for row in rows}

    counts = cached_value(workspace_id(), 'import-match-counts', 20, match_counts)
    with connect() as conn:
        rows = conn.execute("SELECT i.* FROM xm.imports i WHERE i.company_id=current_setting('xm.workspace_id') ORDER BY i.created_at DESC LIMIT 30").fetchall()
    for row in rows:
        found = counts.get(str(row['id'])) or {}
        row['new_matches'] = int(found.get('total') or 0)
        row['new_hot'] = int(found.get('hot') or 0)
        row['new_warm'] = int(found.get('warm') or 0)
        row['new_custom'] = int(found.get('custom') or 0)
    return rows


@app.post("/integration/imports", status_code=202)
@app.post("/imports", status_code=202)
async def upload_import(file: UploadFile = File(...), agent_name: str | None = Form(default=None),
                        matching_mode: str | None = Form(default=None)):
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "Please upload a JSON file.")
    from matching_scope import load_matching_scope
    with connect() as conn:
        mode = load_matching_scope(conn, workspace_id(), matching_mode).mode
    import_id = uuid.uuid4()
    destination = UPLOAD_DIR / workspace_id() / f"{import_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    try:
        with destination.open("r", encoding="utf-8") as handle:
            header = json.load(handle)
        if not isinstance(header.get("chats"), dict):
            raise ValueError("the chats field is missing")
        resolved_agent = (agent_name or header.get("salesName") or "Unknown").strip()
        if not resolved_agent or len(resolved_agent) > 100:
            raise ValueError("invalid agent name")
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"Unrecognised JSON format: {exc}") from exc
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, status, matching_mode FROM xm.imports WHERE company_id=current_setting('xm.workspace_id') AND agent_name=%s AND file_sha256=%s",
            (resolved_agent, digest.hexdigest()),
        ).fetchone()
        if existing:
            destination.unlink(missing_ok=True)
            # Do not silently ignore a new mode on an already imported file.
            # Rebuild the existing data without creating duplicate messages or
            # relabelling known pairs as new upload discoveries.
            if existing['status'] == 'completed':
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042026))")
                current_mode = conn.execute('SELECT matching_mode FROM xm.app_preferences WHERE company_id=%s',
                                            (workspace_id(),)).fetchone()['matching_mode']
                if current_mode != mode:
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_setting('xm.workspace_id'), 9042027))")
                    conn.execute('UPDATE xm.app_preferences SET matching_mode=%s, updated_at=now() WHERE company_id=%s',
                                 (mode, workspace_id()))
                    conn.execute("""INSERT INTO xm.maintenance_jobs(id)
                      SELECT %s WHERE NOT EXISTS (SELECT 1 FROM xm.maintenance_jobs
                        WHERE company_id=current_setting('xm.workspace_id') AND status IN ('queued','processing'))""", (uuid.uuid4(),))
                    conn.commit()
                    return {"id": existing['id'], "status": existing['status'], "duplicate": True, "recomputing": True, "matching_mode": mode}
            return {"id": existing["id"], "status": existing["status"], "duplicate": True, "matching_mode": existing['matching_mode']}
        from ingest import _count_messages
        from progress import queued_progress
        messages = _count_messages(header)
        planned = queued_progress(conn, workspace_id(), 'import', {'read': messages, 'parse': messages, 'index': 0, 'match': 0, 'finish': 1})
        conn.execute(
            "INSERT INTO xm.imports(id, agent_name, file_name, file_path, file_sha256, matching_mode, progress) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (import_id, resolved_agent, file.filename, str(destination), digest.hexdigest(), mode, json.dumps(planned)),
        )
        conn.commit()
    return {"id": import_id, "status": "queued", "duplicate": False, "agent_name": resolved_agent, "matching_mode": mode,
            "messages": messages, "estimate_s": planned['estimate_s']}


@app.get("/imports/estimate")
def import_estimate(messages: int = 0):
    """How long an upload of ``messages`` messages usually takes here (seconds), from the last processed upload."""
    from progress import estimate_seconds
    messages = max(0, min(int(messages), 5_000_000))
    with connect() as conn:
        return {"messages": messages, "estimate_s": estimate_seconds(conn, workspace_id(), 'import',
                                                                      {'read': messages, 'parse': messages, 'index': 0, 'match': 0, 'finish': 1})}


@app.get("/integration/imports/{import_id}")
def integration_import_status(import_id: uuid.UUID):
    with connect() as conn:
        row = conn.execute("""SELECT id,company_id,agent_name,file_name,status,matching_mode,
          created_at,started_at,finished_at,error,total_messages,processed_messages,
          request_count,listing_count,ignored_count,duplicate_count FROM xm.imports
          WHERE id=%s AND company_id=current_setting('xm.workspace_id')""", (import_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Import not found.")
    return row


@app.get("/documents")
def documents(document_type: str | None = None, limit: int = 50, offset: int = 0):
    limit = min(max(limit, 1), 200)
    query = """
      SELECT d.*, r.chat_name, r.sent_at, r.raw_text, r.author
      FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.active
    """
    params = []
    if document_type in {"buyer_request", "property_listing"}:
        query += " AND d.document_type=%s"
        params.append(document_type)
    query += " ORDER BY d.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with connect() as conn:
        return conn.execute(query, params).fetchall()


def match_pair_scope(conn):
    """Legacy pair APIs have no direction, so require an owned side of each pair."""
    clause, params = document_scope_filter(conn)
    if not clause or clause == ' AND false':
        return clause, params
    owned = '''SELECT d.id FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.active ''' + clause
    return f' AND (m.buyer_request_id IN ({owned}) OR m.property_listing_id IN ({owned}))', params * 2


@app.get("/matches")
def matches(limit: int = 50):
    with connect() as conn:
        scope_sql, scope_params = match_pair_scope(conn)
        return conn.execute(
            """
            SELECT m.*, br.normalized_text buyer_text, pl.normalized_text listing_text,
                   br.categories buyer_categories, pl.categories listing_categories,
                   br.locations buyer_locations, pl.locations listing_locations,
                   br.contact_name buyer_contact, pl.contact_name listing_contact
            FROM xm.matches m
            JOIN xm.documents br ON br.id=m.buyer_request_id
            JOIN xm.documents pl ON pl.id=m.property_listing_id
            WHERE m.company_id=current_setting('xm.workspace_id') AND br.active AND pl.active
            """ + scope_sql + ' ORDER BY m.score DESC LIMIT %s', [*scope_params,min(max(limit, 1), 200)]
        ).fetchall()


@app.get("/buyers")
def buyers(
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    agent_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    query = """
      SELECT d.id, d.agent_name, d.transaction_type, d.categories, d.locations,
             d.land_area_min, d.land_area_max, d.building_area_min, d.building_area_max,
             d.price_min, d.price_max, d.price_basis, d.facing, d.exclusions,
             d.contact_name, d.contact_phone, d.normalized_text, d.extraction_confidence,
             d.segment_index, d.segment_count, d.segment_text,
             r.chat_name, r.sent_at, r.author, r.raw_text,
             count(m.id) FILTER (WHERE m.score >= 60) AS match_count,
             count(m.id) FILTER (WHERE m.status = 'hot') AS hot_count,
             count(m.id) FILTER (WHERE m.status = 'warm' AND m.score >= 60) AS warm_count,
             count(m.id) FILTER (WHERE m.status = 'custom') AS custom_count,
             max(m.score) AS best_score
      FROM xm.documents d
      JOIN xm.raw_messages r ON r.id=d.raw_message_id
      LEFT JOIN xm.matches m ON m.buyer_request_id=d.id
      WHERE d.company_id=current_setting('xm.workspace_id') AND d.document_type='buyer_request' AND d.active
    """
    params = []
    if search:
        query += """ AND (d.normalized_text ILIKE %s OR coalesce(d.contact_name,'') ILIKE %s
                       OR coalesce(r.chat_name,'') ILIKE %s OR array_to_string(d.locations, ' ') ILIKE %s
                       OR array_to_string(d.categories, ' ') ILIKE %s)"""
        term = f"%{search.strip()}%"
        params.extend([term, term, term, term, term])
    if date_from:
        query += " AND r.sent_at::date >= %s"
        params.append(date_from)
    if date_to:
        query += " AND r.sent_at::date <= %s"
        params.append(date_to)
    if agent_name:
        query += " AND d.agent_name=%s"
        params.append(agent_name)
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        query += scope_sql
        params += scope_params
    query += """ GROUP BY d.id, r.id
                 ORDER BY r.sent_at DESC NULLS LAST
                 LIMIT %s OFFSET %s"""
    params.extend([min(max(limit, 1), 300), max(offset, 0)])
    with connect() as conn:
        return conn.execute(query, params).fetchall()


@app.get("/buyers/{buyer_id}/recommendations")
def buyer_recommendations(buyer_id: uuid.UUID, limit: int = 50):
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        buyer = conn.execute(
            """SELECT d.*, r.chat_name, r.sent_at, r.author, r.raw_text
               FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
               WHERE d.id=%s AND d.company_id=current_setting('xm.workspace_id') AND d.active AND d.document_type='buyer_request'""" + scope_sql,
            [buyer_id,*scope_params],
        ).fetchone()
        if not buyer:
            raise HTTPException(404, "Buyer request not found.")
        # The buyer's own listings first, then the sales watchlist, company-keyword listings, everyone else.
        tier_sql, tier_params, sender_join = ownership.rank_sql(ownership.load_rules(conn), 'src', 'pl', 'raw')
        rows = conn.execute(
            f"""WITH src AS MATERIALIZED (
                 SELECT br.id, {ownership.source_sql('br', 'brr')}
                 FROM xm.documents br JOIN xm.raw_messages brr ON brr.id=br.raw_message_id WHERE br.id=%s
               )
               SELECT m.id, m.score, m.status, m.location_score, m.land_score, m.building_score,
                      m.price_score, m.semantic_score, m.explanation,
                      pl.id property_id, pl.agent_name, pl.transaction_type, pl.categories,
                      pl.locations, pl.land_area_min, pl.land_area_max,
                      pl.building_area_min, pl.building_area_max, pl.price_min, pl.price_max,
                      pl.price_basis, pl.negotiable, pl.facing, pl.requirements,
                      pl.contact_name, pl.contact_phone, raw.raw_text property_text,
                      pl.segment_index, pl.segment_count, pl.segment_text,
                      raw.chat_name, raw.sent_at, raw.author, {tier_sql} AS {ownership.RANK_COLUMN}
               FROM xm.matches m JOIN src ON src.id=m.buyer_request_id
               JOIN xm.documents pl ON pl.id=m.property_listing_id
               JOIN xm.raw_messages raw ON raw.id=pl.raw_message_id {sender_join}
               WHERE m.company_id=current_setting('xm.workspace_id') AND m.buyer_request_id=%s AND m.score >= 60
               ORDER BY {ownership.RANK_COLUMN}, {ownership.status_rank_sql('m.status')}, m.score DESC,
                        raw.sent_at DESC NULLS LAST, pl.id LIMIT %s""",
            [buyer_id, *tier_params, buyer_id, min(max(limit, 1), 100)],
        ).fetchall()
    ownership.label(rows)
    for row in rows:
        row["temperature"] = row["status"]
    return {"buyer": buyer, "recommendations": rows}


@app.post("/buyers/recommendations/batch")
def batch_buyer_recommendations(payload: BuyerBatchRequest):
    buyer_ids = list(dict.fromkeys(payload.buyer_ids))
    if not buyer_ids:
        return {"groups": []}
    if len(buyer_ids) > 50:
        raise HTTPException(400, "At most 50 buyers per request.")
    limit_per_buyer = min(max(payload.limit_per_buyer, 1), 50)
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        buyers = conn.execute(
            """SELECT d.id, d.agent_name, d.categories, d.locations, d.contact_name, d.contact_phone,
                      d.normalized_text, d.segment_index, d.segment_count, d.segment_text,
                      r.raw_text, r.chat_name, r.sent_at
               FROM xm.documents d JOIN xm.raw_messages r ON r.id=d.raw_message_id
               WHERE d.id = ANY(%s::uuid[]) AND d.company_id=current_setting('xm.workspace_id')
                 AND d.active AND d.document_type='buyer_request'""" + scope_sql,
            [[str(item) for item in buyer_ids],*scope_params],
        ).fetchall()
        buyer_ids = [row['id'] for row in buyers]
        # The top N per buyer are taken after the ownership order (own, sales watchlist, company keyword, other).
        tier_sql, tier_params, sender_join = ownership.rank_sql(ownership.load_rules(conn), 'src', 'pl', 'raw')
        matches = conn.execute(
            f"""WITH src AS MATERIALIZED (
                 SELECT br.id, {ownership.source_sql('br', 'brr')}
                 FROM xm.documents br JOIN xm.raw_messages brr ON brr.id=br.raw_message_id WHERE br.id = ANY(%s::uuid[])
               ), scored AS (
                 SELECT m.buyer_request_id, m.id, m.score, m.status, m.location_score,
                        m.land_score, m.building_score, m.price_score,
                        m.semantic_score, m.explanation,
                        pl.id property_id, pl.agent_name, pl.categories, pl.locations,
                        pl.land_area_min, pl.land_area_max, pl.building_area_min, pl.building_area_max,
                        pl.price_min, pl.price_max, pl.price_basis, pl.negotiable, pl.facing,
                        pl.contact_name, pl.contact_phone, raw.raw_text property_text,
                        pl.segment_index, pl.segment_count, pl.segment_text,
                        raw.chat_name, raw.sent_at, {tier_sql} AS {ownership.RANK_COLUMN}
                 FROM xm.matches m JOIN src ON src.id=m.buyer_request_id
                 JOIN xm.documents pl ON pl.id=m.property_listing_id
                 JOIN xm.raw_messages raw ON raw.id=pl.raw_message_id {sender_join}
                 WHERE m.company_id=current_setting('xm.workspace_id') AND m.buyer_request_id = ANY(%s::uuid[]) AND m.score >= 60
               ), ranked AS (
                 SELECT scored.*, row_number() OVER (PARTITION BY buyer_request_id
                   ORDER BY {ownership.RANK_COLUMN}, {ownership.status_rank_sql('status')}, score DESC,
                            sent_at DESC NULLS LAST, property_id) AS rank
                 FROM scored
               )
               SELECT * FROM ranked WHERE rank <= %s ORDER BY buyer_request_id, rank""",
            [[str(item) for item in buyer_ids], *tier_params, [str(item) for item in buyer_ids], limit_per_buyer],
        ).fetchall()
    ownership.label(matches)
    by_buyer = {str(item): [] for item in buyer_ids}
    for row in matches:
        row.pop("rank", None)
        row["temperature"] = row["status"]
        by_buyer[str(row["buyer_request_id"])].append(row)
    buyer_map = {str(row["id"]): row for row in buyers}
    groups = [
        {"buyer": buyer_map[str(item)], "recommendations": by_buyer[str(item)]}
        for item in buyer_ids if str(item) in buyer_map
    ]
    return {"groups": groups}


@app.get("/settings")
def get_settings():
    from workspace import SERVICE_CITIES
    from parser import CATEGORY_LABELS
    with connect() as conn:
        row = conn.execute("SELECT * FROM xm.match_settings WHERE company_id=current_setting('xm.workspace_id')").fetchone() or {}
    row = dict(row)
    row['category_areas'] = row.get('category_areas') or {}
    row['service_cities'] = list(SERVICE_CITIES)
    row['categories'] = [{'id': key, 'label': label} for key, label in CATEGORY_LABELS.items()]
    return row


@app.post("/matches/recompute")
def recompute():
    return {"matches": recompute_matches()}


@app.get("/agent/search")
def agent_search(q: str, document_type: str | None = None, limit: int = 10):
    if len(q.strip()) < 3:
        raise HTTPException(400, "The question is too short.")
    from reindex import glossary_entries
    with connect() as conn:
        parsed = parse_message(q, glossary=glossary_entries(conn))
    category = parsed.categories[0] if len(parsed.categories) == 1 else None
    try:
        hits = qdrant_query(embed(q), document_type=document_type, category=category, limit=limit)
        ids = [hit.get("payload", {}).get("postgres_id") for hit in hits]
        score_map = {hit.get("payload", {}).get("postgres_id"): hit.get("score", 0) for hit in hits}
    except Exception:
        ids, score_map = [], {}
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        if ids:
            rows = conn.execute(
                """SELECT d.*, r.chat_name, r.sent_at, r.raw_text, r.author, e.public_id, e.status AS entity_status FROM xm.documents d
                   JOIN xm.raw_messages r ON r.id=d.raw_message_id LEFT JOIN xm.entities e ON e.entity_id=d.entity_id
                   WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND d.id = ANY(%s::uuid[])
                     AND coalesce(e.status,'ready')='ready'""" + scope_sql, [ids,*scope_params]
            ).fetchall()
        else:
            term = f"%{q.strip()}%"
            rows = conn.execute(
                """SELECT d.*, r.chat_name, r.sent_at, r.raw_text, r.author, e.public_id, e.status AS entity_status FROM xm.documents d
                   JOIN xm.raw_messages r ON r.id=d.raw_message_id LEFT JOIN xm.entities e ON e.entity_id=d.entity_id
                   WHERE d.company_id=current_setting('xm.workspace_id') AND d.active AND (%s::text IS NULL OR d.document_type=%s)
                     AND coalesce(e.status,'ready')='ready'
                     AND (d.normalized_text ILIKE %s OR %s = ANY(d.categories))""" + scope_sql + ' ORDER BY d.created_at DESC LIMIT %s',
                [document_type, document_type, term, category or "", *scope_params,min(limit, 50)],
            ).fetchall()
    for row in rows:
        row["semantic_score"] = round(float(score_map.get(str(row["id"]), 0)) * 100, 2)
    rows.sort(key=lambda row: row["semantic_score"], reverse=True)
    return {"query": q, "parsed": parsed.dict(), "results": rows[:limit]}


@app.get("/agent/matches")
def agent_matches(contact: str | None = None, min_score: float = 60, limit: int = 20):
    with connect() as conn:
        scope_sql, scope_params = match_pair_scope(conn)
        base = """SELECT m.score, m.status, m.explanation, br.contact_name buyer_contact, br.normalized_text buyer_request,
                         pl.contact_name listing_contact, pl.normalized_text property_listing,
                         be.public_id buyer_id, le.public_id listing_id
                  FROM xm.matches m JOIN xm.documents br ON br.id=m.buyer_request_id
                  JOIN xm.documents pl ON pl.id=m.property_listing_id
                  LEFT JOIN xm.entities be ON be.entity_id=br.entity_id LEFT JOIN xm.entities le ON le.entity_id=pl.entity_id
                  WHERE m.company_id=current_setting('xm.workspace_id') AND m.score >= %s
                    AND br.active AND pl.active
                    AND coalesce(be.status,'ready')='ready' AND coalesce(le.status,'ready')='ready'""" + scope_sql
        if contact:
            term = f"%{contact}%"
            rows = conn.execute(
                base + " AND (br.contact_name ILIKE %s OR pl.contact_name ILIKE %s) ORDER BY m.score DESC LIMIT %s",
                [min_score,*scope_params,term,term,min(max(limit, 1), 100)],
            ).fetchall()
        else:
            rows = conn.execute(
                base + " ORDER BY m.score DESC LIMIT %s",
                [min_score,*scope_params,min(max(limit, 1), 100)],
            ).fetchall()
    return {"results": rows}

from workspace import router as workspace_router
app.include_router(workspace_router)
from location_routes import router as location_router
app.include_router(location_router)
from glossary_routes import router as glossary_router
app.include_router(glossary_router)
from entity_routes import router as entity_router
app.include_router(entity_router)
from buyer_list import router as buyer_list_router
app.include_router(buyer_list_router)
from matchlog import router as matchlog_router
app.include_router(matchlog_router)
from stock import router as stock_router
app.include_router(stock_router)
from dashboard import router as dashboard_router
app.include_router(dashboard_router)
from dashboard_pdf import router as dashboard_pdf_router
app.include_router(dashboard_pdf_router)
from team import router as team_router
app.include_router(team_router)
from company import router as company_router
app.include_router(company_router)
from export_all import router as export_all_router, selection_router as export_selection_router
app.include_router(export_all_router)
app.include_router(export_selection_router)
from deploy import router as deploy_router
app.include_router(deploy_router)
from ai_keys import router as ai_keys_router
app.include_router(ai_keys_router)
from ai_review import router as ai_review_router
app.include_router(ai_review_router)
from platform_admin import router as platform_router
app.include_router(platform_router)
from knowledge import router as knowledge_router
app.include_router(knowledge_router)
