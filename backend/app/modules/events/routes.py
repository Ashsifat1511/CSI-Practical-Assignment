"""POST /api/events – thin: parse the top-level JSON, open one transaction, delegate to the service."""
import json

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.modules.events.service import SubmissionContext, process_batch
from app.shared.db import transaction

router = APIRouter()


def _submit(items: list) -> list:
    with transaction() as conn:
        return [r.to_dict() for r in process_batch(conn, items, SubmissionContext(channel="REST"))]


@router.post("/api/events")
async def post_events(request: Request):
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Request body must be valid JSON"})

    if isinstance(body, dict):
        items = [body]
    elif isinstance(body, list):
        items = body
    else:
        return JSONResponse(status_code=400, content={"error": "Body must be an event object or an array of events"})

    results = await run_in_threadpool(_submit, items)
    return {"results": results}
