import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from db import connect
from tenant import workspace_scope
from embedding import embed
from matcher import recompute_matches
from parser import message_hash
from qdrant import upsert
from reindex import document_parts, extracted_values, glossary_entries, parse_parts, point_id_for, primary_part, qdrant_payload
from analysis_window import message_in_window, window_start
from progress import NoProgress, Progress


def _count_messages(payload: dict) -> int:
    return sum(len(chat.get("messages", [])) for chat in payload.get("chats", {}).values())


def process_import(import_id: str) -> None:
    # Only the worker calls this function; derive scope from the queued job itself.
    with connect() as conn:
        job = conn.execute('SELECT company_id FROM xm.imports WHERE id=%s', (import_id,)).fetchone()
    if not job:
        raise ValueError('Upload not found.')
    with workspace_scope(job['company_id']):
        _process_import(import_id)


def _process_import(import_id: str) -> None:
    qdrant_batch = []
    qdrant_count = 0
    progress = NoProgress()
    try:
        with connect() as conn:
            job = conn.execute("SELECT * FROM xm.imports WHERE id=%s", (import_id,)).fetchone()
            conn.execute("UPDATE xm.imports SET status='processing', started_at=coalesce(started_at, now()), error=NULL WHERE id=%s", (import_id,))
            conn.commit()
            planned = (job.get('progress') or {}).get('items') or {}
        progress = Progress('import', import_id, job['company_id'], planned, started_at=job.get('started_at'))
        progress.report('read', 0, 0, force=True)
        payload = json.loads(Path(job["file_path"]).read_text(encoding="utf-8"))
        total = _count_messages(payload)
        with connect() as conn:
            conn.execute("UPDATE xm.imports SET total_messages=%s WHERE id=%s", (total, import_id))
            conn.commit()
        progress.plan(read=total, parse=total)
        progress.report('read', total, total, force=True)
        progress.report('parse', 0, total, force=True)
        counters = {
            "buyer_request": int(job["request_count"]), "property_listing": int(job["listing_count"]),
            "ignored": int(job["ignored_count"]), "duplicate": int(job["duplicate_count"]),
        }
        processed = int(job["processed_messages"])
        qdrant_count = int(job["qdrant_points"])
        with connect() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(current_setting('xm.workspace_id'), 9042026))")
            glossary = glossary_entries(conn)
            window = window_start(conn)
            seen = 0
            for chat_id, chat in payload.get("chats", {}).items():
                for position, message in enumerate(chat.get("messages", [])):
                    existing_position = conn.execute(
                        "SELECT id FROM xm.raw_messages WHERE import_id=%s AND chat_id=%s AND message_position=%s",
                        (import_id, chat_id, position),
                    ).fetchone()
                    seen += 1
                    if seen % 100 == 0:
                        progress.report('parse', seen, total)
                    if existing_position:
                        continue
                    timestamp = str(message[0]) if len(message) > 0 else ""
                    text = str(message[1]) if len(message) > 1 else ""
                    author = str(message[2]) if len(message) > 2 else ""
                    digest = message_hash(chat_id, timestamp, author, text)
                    sent_at = datetime.fromisoformat(timestamp) if timestamp else None
                    previous = conn.execute(
                        "SELECT id FROM xm.raw_messages WHERE company_id=current_setting('xm.workspace_id') AND agent_name=%s AND message_hash=%s LIMIT 1",
                        (job["agent_name"], digest),
                    ).fetchone()
                    # One bubble may hold several requests/listings: one document per part.
                    parts = parse_parts(text, author, glossary)
                    primary = primary_part(parts)
                    raw_id = uuid.uuid4()
                    duplicate_of = previous["id"] if previous else None
                    classification = "duplicate" if duplicate_of else primary.classification
                    conn.execute(
                        """
                        INSERT INTO xm.raw_messages(id, import_id, agent_name, chat_id, chat_name, is_group, message_position,
                          sent_at, author, raw_text, message_hash, classification, confidence, duplicate_of, extracted)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                        """,
                        (raw_id, import_id, job["agent_name"], chat_id, chat.get("name", "Untitled chat"), bool(chat.get("is_group")), position,
                         sent_at, author, text, digest, classification,
                         primary.confidence, duplicate_of, json.dumps(extracted_values(parts))),
                    )
                    documents = [] if duplicate_of else document_parts(parts)
                    # Messages older than the analysis window are stored and classified, but not embedded or matched.
                    analysed = message_in_window(sent_at, window)
                    if duplicate_of:
                        counters["duplicate"] += 1
                    elif not documents:
                        counters["ignored"] += 1
                    for part, values in documents:
                        doc_id = uuid.uuid4()
                        point_id = point_id_for(doc_id)
                        values = {**values, "id": doc_id, "raw_message_id": raw_id, "import_id": import_id,
                                  "agent_name": job["agent_name"], "qdrant_point_id": point_id}
                        # Column names are fixed document fields, never user input.
                        conn.execute("INSERT INTO xm.documents (" + ",".join(values) + ") VALUES (" + ",".join(["%s"] * len(values)) + ")",
                                     tuple(values.values()))
                        if analysed:
                            qdrant_batch.append({
                                "id": str(point_id),
                                "vector": embed(part.normalized_text),
                                "payload": qdrant_payload(part, doc_id, job["agent_name"]),
                            })
                        counters[part.classification] += 1
                    processed += 1
                    if len(qdrant_batch) >= 128:
                        qdrant_count += upsert(qdrant_batch)
                        qdrant_batch = []
                    if processed % 250 == 0:
                        conn.execute(
                            """UPDATE xm.imports SET processed_messages=%s, request_count=%s, listing_count=%s,
                               ignored_count=%s, duplicate_count=%s, qdrant_points=%s WHERE id=%s""",
                            (processed, counters["buyer_request"], counters["property_listing"], counters["ignored"],
                             counters["duplicate"], qdrant_count, import_id),
                        )
                        conn.commit()
            progress.report('parse', total, total, force=True)
            progress.report('index', 0, len(qdrant_batch), force=True)
            if qdrant_batch:
                qdrant_count += upsert(qdrant_batch)
            progress.report('index', len(qdrant_batch), len(qdrant_batch), force=True)
            conn.execute(
                """UPDATE xm.imports SET processed_messages=%s, request_count=%s, listing_count=%s,
                   ignored_count=%s, duplicate_count=%s, qdrant_points=%s WHERE id=%s""",
                (processed, counters["buyer_request"], counters["property_listing"], counters["ignored"],
                 counters["duplicate"], qdrant_count, import_id),
            )
            conn.commit()
        match_count = recompute_matches(source='import', import_id=import_id, agent_name=job['agent_name'],
                                        matching_mode=job.get('matching_mode'), progress=progress)
        progress.finish()
        with connect() as conn:
            conn.execute("UPDATE xm.imports SET status='completed', finished_at=now() WHERE id=%s", (import_id,))
            conn.execute(
                "INSERT INTO xm.audit_events(event_type, entity_type, entity_id, details) VALUES ('import_completed','import',%s,%s::jsonb)",
                (import_id, json.dumps({**counters, "qdrant_points": qdrant_count, "matches": match_count})),
            )
            conn.commit()
    except Exception as exc:
        progress.fail(exc)
        with connect() as conn:
            conn.execute("UPDATE xm.imports SET status='failed', error=%s, finished_at=now() WHERE id=%s", (str(exc), import_id))
            conn.commit()
        raise
