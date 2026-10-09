"""POST /api/ack {"event_ids": [...], "acknowledged_by": "optional name"}"""
import json

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.modules.ack.service import acknowledge_events
from app.shared.db import transaction

router = APIRouter()


def _ack(event_ids: list, by):
    with transaction() as conn:
        return acknowledge_events(conn, event_ids, by)


@router.post("/api/ack")
async def post_ack(request: Request):
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})
    if not isinstance(body, dict) or not isinstance(body.get("event_ids"), list):
        return JSONResponse(status_code=400, content={"error": 'Body must be {"event_ids": ["EV-101", ...]}'})
    by = body.get("acknowledged_by") if isinstance(body.get("acknowledged_by"), str) else "supervisor"
    return {"results": await run_in_threadpool(_ack, body["event_ids"], by)}
