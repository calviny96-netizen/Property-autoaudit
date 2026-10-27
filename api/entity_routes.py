"""Public IDs: look them up and mark buyers/listings ready, on-hold, sold or deleted."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auth import current_user
from db import connect
from entities import lookup, set_entity_status, STATUSES
from runtime_cache import invalidate_stats
from tenant import owner_id, workspace_id
from matching_scope import document_scope_filter

router = APIRouter(prefix='/entities')


class StatusChange(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: Literal['ready', 'on_hold', 'sold', 'deleted']
    note: str | None = Field(default=None, max_length=300)


@router.post('/status')
def change_status(payload: StatusChange, request: Request):
    user = current_user(request)
    with connect() as conn:
        result = set_entity_status(conn, workspace_id(), owner_id() or user['id'], payload.ids, payload.status, payload.note)
        conn.commit()
    invalidate_stats(workspace_id())
    return result


@router.get('/lookup')
def find(q: str, limit: int = 8):
    if len(q.strip()) < 2:
        raise HTTPException(400, 'Type at least 2 characters of the ID.')
    with connect() as conn:
        return lookup(conn, workspace_id(), q, limit)


@router.get('/summary')
def summary():
    """How many unique buyers and listings sit in each status."""
    with connect() as conn:
        scope_sql, scope_params = document_scope_filter(conn)
        rows = conn.execute(
            # Keep the expensive keyword/watchlist lookup independent of the
            # number of entities whose status is being counted.
            """WITH owned_entities AS MATERIALIZED (
                 SELECT DISTINCT d.entity_id FROM xm.documents d
                 JOIN xm.raw_messages r ON r.id=d.raw_message_id
                 WHERE d.company_id=current_setting('xm.workspace_id') AND d.active """ + scope_sql + ''')
               SELECT e.document_type,e.status,count(*) AS n FROM xm.entities e
               WHERE e.company_id=current_setting('xm.workspace_id')
                 AND e.entity_id IN (SELECT entity_id FROM owned_entities)
               GROUP BY 1,2''', scope_params).fetchall()
    out = {'buyer': dict.fromkeys(STATUSES, 0), 'listing': dict.fromkeys(STATUSES, 0)}
    for row in rows:
        out['buyer' if row['document_type'] == 'buyer_request' else 'listing'][row['status']] = row['n']
    return out
