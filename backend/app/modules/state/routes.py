"""GET /api/state?view=summary|pending|exceptions&source_id=..."""
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.modules.state import queries
from app.shared.db import transaction

router = APIRouter()

VIEWS = ("summary", "pending", "exceptions")


@router.get("/api/state")
def get_state(view: str = "summary", source_id: Optional[str] = None):
    if view not in VIEWS:
        return JSONResponse(status_code=400, content={"error": f"view must be one of {', '.join(VIEWS)}"})
    source_id = source_id.strip() if source_id and source_id.strip() else None

    with transaction() as conn:
        if view == "summary":
            return {"view": view, "source_id": source_id, **queries.get_summary(conn, source_id).to_dict()}
        rows = queries.get_pending(conn, source_id) if view == "pending" else queries.get_exceptions(conn, source_id)
    return {"view": view, "source_id": source_id, "items": rows}
