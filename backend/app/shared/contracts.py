"""Data contracts shared between modules (the stable boundary if modules become services)."""
from dataclasses import asdict, dataclass
from typing import Optional


class ItemStatus:
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    PENDING_REFERENCE = "PENDING_REFERENCE"
    REJECTED = "REJECTED"


class AckStatus:
    ACKED = "ACKED"
    ALREADY_ACKED = "ALREADY_ACKED"
    NOT_READY = "NOT_READY"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class ItemResult:
    event_id: Optional[str]
    status: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Summary:
    net_total: int
    processed_events: int
    pending_ack: int
    unresolved: int
    duplicates: int
    conflicts: int
    rejected_submissions: int

    def to_dict(self) -> dict:
        return asdict(self)
