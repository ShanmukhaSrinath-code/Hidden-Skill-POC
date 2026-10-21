"""Platform-neutral records emitted by the Jira, ClickUp, and Slack connectors."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


RecordKind = Literal[
    "workspace", "project", "conversation", "task", "document", "message", "comment", "event", "person"
]


@dataclass(frozen=True)
class SourceRef:
    """Stable pointer back to the record in its source platform."""

    platform: str
    account_id: str
    object_type: str
    object_id: str
    url: str | None = None


@dataclass
class UnifiedRecord:
    """A normalized event or object while retaining the platform's original payload."""

    source: SourceRef
    kind: RecordKind
    occurred_at: datetime | None = None
    actor_id: str | None = None
    parent: SourceRef | None = None
    title: str | None = None
    body: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    # (platform user id, role) pairs, e.g. ("abc123", "assignee"); recipients of activity on this record.
    participants: list[tuple[str, str]] = field(default_factory=list)
    # Platform user ids @mentioned in this record's text.
    mentions: list[str] = field(default_factory=list)
    # (reaction name, platform user id) pairs.
    reactions: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class RecordLink:
    """An explicit relationship between records from any connected platform."""

    left: SourceRef
    right: SourceRef
    relation: str
    created_by: str = "system"
    confidence: float | None = None
