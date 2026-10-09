"""validate_event(): pure function, no database access. Decides whether an item is a well-formed event."""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.shared.timeutil import parse_iso_with_tz, to_iso_utc

EVENT_TYPES = ("COUNT", "VOID")


@dataclass
class ValidEvent:
    source_id: str
    event_id: str
    type: str
    quantity: Optional[int]
    target_event_id: Optional[str]
    event_time: str  # normalized ISO 8601 UTC

    def normalized(self) -> Dict[str, Any]:
        """Canonical form used to tell DUPLICATE (same data) from CONFLICT (different data)."""
        return {
            "source_id": self.source_id,
            "event_id": self.event_id,
            "type": self.type,
            "quantity": self.quantity,
            "target_event_id": self.target_event_id,
            "event_time": self.event_time,
        }


@dataclass
class ValidationOutcome:
    event: Optional[ValidEvent] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.event is not None


def _non_empty_str(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def validate_event(raw: Any) -> ValidationOutcome:
    if not isinstance(raw, dict):
        return ValidationOutcome(error="Event must be a JSON object")

    source_id = _non_empty_str(raw.get("source_id"))
    if source_id is None:
        return ValidationOutcome(error="source_id is required and must be a non-empty string")

    event_id = _non_empty_str(raw.get("event_id"))
    if event_id is None:
        return ValidationOutcome(error="event_id is required and must be a non-empty string")

    event_type = raw.get("type")
    if event_type not in EVENT_TYPES:
        return ValidationOutcome(error="type must be exactly COUNT or VOID")

    quantity = raw.get("quantity")
    target = raw.get("target_event_id")

    if event_type == "COUNT":
        # bool is a subclass of int in Python, so exclude it explicitly
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            return ValidationOutcome(error="COUNT quantity must be a positive integer")
        if target is not None:
            return ValidationOutcome(error="COUNT must not have a target_event_id")
        target_id = None
    else:
        if quantity is not None:
            return ValidationOutcome(error="VOID quantity must be null or omitted")
        target_id = _non_empty_str(target)
        if target_id is None:
            return ValidationOutcome(error="VOID requires target_event_id of the COUNT being reversed")
        if target_id == event_id:
            return ValidationOutcome(error="VOID cannot target itself")

    event_time = parse_iso_with_tz(raw.get("event_time"))
    if event_time is None:
        return ValidationOutcome(error="event_time must be an ISO 8601 timestamp with timezone")

    return ValidationOutcome(
        event=ValidEvent(
            source_id=source_id,
            event_id=event_id,
            type=event_type,
            quantity=quantity if event_type == "COUNT" else None,
            target_event_id=target_id,
            event_time=to_iso_utc(event_time),
        )
    )
