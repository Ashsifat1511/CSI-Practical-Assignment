"""Events module: the single owner of COUNT / VOID business rules.

REST and MQTT both call ``process_batch``; nothing else in the codebase decides whether a COUNT counts.
All functions take an open connection so the caller decides the transaction boundary
(REST: one transaction per request, MQTT: one transaction per challenge). Each item runs in its
own SAVEPOINT so one bad item never undoes the valid ones.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import Connection

from app.modules.events import repository as repo
from app.modules.events.validation import ValidEvent, validate_event
from app.shared import domain_events
from app.shared.contracts import ItemResult, ItemStatus

log = logging.getLogger(__name__)


@dataclass
class SubmissionContext:
    channel: str  # "REST" or "MQTT"
    challenge_id: Optional[str] = None


def process_batch(conn: Connection, items: List[Any], ctx: SubmissionContext) -> List[ItemResult]:
    """Process items in submitted order; the result list keeps the same order."""
    return [process_event(conn, raw, ctx, index) for index, raw in enumerate(items)]


def process_event(conn: Connection, raw: Any, ctx: SubmissionContext, index: Optional[int] = None) -> ItemResult:
    outcome = validate_event(raw)
    if not outcome.ok:
        return _reject(conn, raw, ctx, index, outcome.error)

    event = outcome.event
    try:
        with conn.transaction():  # SAVEPOINT for this item
            result = _process_valid(conn, event, raw, ctx)
            _record_attempt(conn, raw, ctx, index, event.source_id, event.event_id, result)
            return result
    except psycopg.errors.IntegrityError:
        log.warning("constraint rejected event %s", event.event_id)
        return _reject(conn, raw, ctx, index, "Rejected by database integrity rule", event)


def _process_valid(conn: Connection, event: ValidEvent, raw: Any, ctx: SubmissionContext) -> ItemResult:
    # Serialise everything touching the same COUNT (the COUNT itself and every VOID aimed at it).
    # A single lock per item keeps lock ordering trivial (no deadlocks between items).
    repo.lock_event_key(conn, event.event_id if event.type == "COUNT" else event.target_event_id)

    existing = repo.get_event(conn, event.event_id)
    if existing is not None:
        return _duplicate_or_conflict(event, existing)

    repo.ensure_source(conn, event.source_id)
    if event.type == "COUNT":
        return process_count(conn, event, raw, ctx)
    return process_void(conn, event, raw, ctx)


def _duplicate_or_conflict(event: ValidEvent, existing: Dict[str, Any]) -> ItemResult:
    if existing["normalized"] == event.normalized():
        return ItemResult(event.event_id, ItemStatus.DUPLICATE,
                          "Identical event already received; not processed again")
    return ItemResult(event.event_id, ItemStatus.CONFLICT,
                      "Event ID already used with different data; original event preserved")


def process_count(conn: Connection, event: ValidEvent, raw: Any, ctx: SubmissionContext) -> ItemResult:
    inserted = repo.insert_event(conn, normalized=event.normalized(), raw_payload=raw,
                                 status=ItemStatus.ACCEPTED, channel=ctx.channel)
    if not inserted:  # lost a race with an identical id; classify against the stored original
        return _duplicate_or_conflict(event, repo.get_event(conn, event.event_id))
    domain_events.queue(domain_events.EVENT_ACCEPTED, {"event_id": event.event_id, "type": "COUNT",
                                                       "source_id": event.source_id, "quantity": event.quantity})
    winner = resolve_pending_voids(conn, event)
    message = "Event processed"
    if winner:
        message += f"; pending VOID {winner} resolved and applied"
    return ItemResult(event.event_id, ItemStatus.ACCEPTED, message)


def process_void(conn: Connection, event: ValidEvent, raw: Any, ctx: SubmissionContext) -> ItemResult:
    target = repo.get_event(conn, event.target_event_id, for_update=True)

    if target is None:
        if not repo.insert_event(conn, normalized=event.normalized(), raw_payload=raw,
                                 status=ItemStatus.PENDING_REFERENCE, channel=ctx.channel,
                                 reason=f"Waiting for COUNT {event.target_event_id}"):
            return _duplicate_or_conflict(event, repo.get_event(conn, event.event_id))
        domain_events.queue(domain_events.EVENT_PENDING, {"event_id": event.event_id,
                                                          "target_event_id": event.target_event_id})
        return ItemResult(event.event_id, ItemStatus.PENDING_REFERENCE,
                          f"Target COUNT {event.target_event_id} not received yet; VOID stored as pending")

    reason = _void_rejection_reason(event, target)
    if reason:
        return ItemResult(event.event_id, ItemStatus.REJECTED, reason)

    if not repo.insert_event(conn, normalized=event.normalized(), raw_payload=raw, status=ItemStatus.ACCEPTED,
                             channel=ctx.channel, auto_ack=True, resolved=True):
        return _duplicate_or_conflict(event, repo.get_event(conn, event.event_id))
    repo.set_voided_by(conn, target["event_id"], event.event_id)
    domain_events.queue(domain_events.EVENT_ACCEPTED, {"event_id": event.event_id, "type": "VOID",
                                                       "target_event_id": target["event_id"]})
    return ItemResult(event.event_id, ItemStatus.ACCEPTED,
                      f"Event processed; COUNT {target['event_id']} reversed (-{target['quantity']})")


def _void_rejection_reason(event: ValidEvent, target: Dict[str, Any]) -> Optional[str]:
    if target["type"] != "COUNT":
        return f"Target {target['event_id']} is not a COUNT"
    if target["status"] != ItemStatus.ACCEPTED:
        return f"Target COUNT {target['event_id']} was not accepted"
    if target["source_id"] != event.source_id:
        return f"VOID source {event.source_id} does not match COUNT source {target['source_id']}"
    if target["voided_by_event_id"]:
        return f"COUNT {target['event_id']} was already reversed by {target['voided_by_event_id']}"
    return None


def resolve_pending_voids(conn: Connection, count: ValidEvent) -> Optional[str]:
    """Apply the first stored valid pending VOID for this COUNT; reject the others with a reason."""
    winner: Optional[str] = None
    for void in repo.pending_voids_for(conn, count.event_id):
        if void["source_id"] != count.source_id:
            reason = f"VOID source {void['source_id']} does not match COUNT source {count.source_id}"
        elif winner is not None:
            reason = f"COUNT {count.event_id} was already reversed by earlier pending VOID {winner}"
        else:
            winner = void["event_id"]
            repo.accept_pending_void(conn, winner)
            repo.set_voided_by(conn, count.event_id, winner)
            domain_events.queue(domain_events.VOID_RESOLVED, {"event_id": winner, "target_event_id": count.event_id})
            continue
        repo.reject_pending_void(conn, void["event_id"], reason)
        domain_events.queue(domain_events.EVENT_REJECTED, {"event_id": void["event_id"], "reason": reason})
    return winner


def _reject(conn: Connection, raw: Any, ctx: SubmissionContext, index: Optional[int], reason: str,
            event: Optional[ValidEvent] = None) -> ItemResult:
    source_id = event.source_id if event else _peek_str(raw, "source_id")
    event_id = event.event_id if event else _peek_str(raw, "event_id")
    result = ItemResult(event_id, ItemStatus.REJECTED, reason)
    _record_attempt(conn, raw, ctx, index, source_id, event_id, result)
    domain_events.queue(domain_events.EVENT_REJECTED, {"event_id": event_id, "reason": reason})
    return result


def _record_attempt(conn: Connection, raw: Any, ctx: SubmissionContext, index: Optional[int],
                    source_id: Optional[str], event_id: Optional[str], result: ItemResult) -> None:
    repo.insert_attempt(
        conn, channel=ctx.channel, challenge_id=ctx.challenge_id, batch_index=index, raw_payload=raw,
        source_id=source_id, event_id=event_id, classification=result.status,
        reason=None if result.status in (ItemStatus.ACCEPTED,) else result.message,
    )


def _peek_str(raw: Any, key: str) -> Optional[str]:
    if isinstance(raw, dict) and isinstance(raw.get(key), str) and raw[key].strip():
        return raw[key].strip()
    return None
