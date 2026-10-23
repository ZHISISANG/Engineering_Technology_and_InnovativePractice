"""领域模型：工单、版本、交接、问题、草稿、复核记录。

模型只表达事实与状态，不承担 I/O；读写由 :mod:`expo_trace.core.store` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from expo_trace.utils.timeutil import TimeValue


class FieldState(str, Enum):
    OK = "ok"
    MISSING = "missing"
    UNCONFIRMED = "unconfirmed"
    INVALID = "invalid"


class VersionState(str, Enum):
    CURRENT = "current"
    EXPIRED = "expired"
    UNCONFIRMED = "unconfirmed"


class Disposition(str, Enum):
    """每条工单最终只能落在这三种状态之一，不允许“静默通过”。"""

    DRAFT_READY = "draft_ready"
    HUMAN_REVIEW = "human_review"
    BLOCKED = "blocked"


class Severity(str, Enum):
    ERROR = "error"
    WARN = "warn"


class ReviewDecision(str, Enum):
    CONFIRM = "confirm"
    RETURN = "return"


@dataclass
class Handoff:
    ticket_id: str
    from_role: str = ""
    to_role: str = ""
    latest_version: str = ""
    unconfirmed_items: list[str] = field(default_factory=list)
    due_at: str = ""
    note: str = ""
    source: str = ""
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def note_only(self) -> bool:
        """只写了“已处理/已转”的交接：不能算交接完成（f23）。"""
        text = (self.note or "").strip()
        return text in {"已处理", "已转", "处理完毕", "done", "ok"} or (
            not self.latest_version and not self.unconfirmed_items
        )


@dataclass
class KnowledgeVersion:
    version_id: str
    notice_id: str
    batch: str
    category: str
    effective_from: datetime
    effective_to: datetime | None
    confirmed: bool
    confirmed_by: str = ""
    confirmed_at: datetime | None = None
    void: bool = False
    void_reason: str = ""
    supersedes: str = ""
    file_name: str = ""
    statement: str = ""
    received_at: datetime | None = None
    source: str = ""
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def covers(self, moment: datetime) -> bool:
        if self.effective_from > moment:
            return False
        return self.effective_to is None or self.effective_to >= moment


@dataclass
class LedgerRow:
    ticket_id: str
    handling_minutes: float | None = None
    unit_note: str = ""
    deadline: str = ""
    status: str = ""
    source: str = ""
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ticket:
    ticket_id: str
    batch: str
    category: str
    question: str
    occurred: TimeValue
    received: TimeValue
    assignee: str = ""
    contact: str = ""
    status: str = ""
    source: str = ""
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    backfilled: bool = False
    backfill_gap: float | None = None
    handling_minutes: float | None = None
    handling_note: str = ""
    deadline: str = ""
    handoff: Handoff | None = None

    @property
    def occurred_at(self) -> datetime | None:
        return self.occurred.value if self.occurred.ok else None

    @property
    def received_at(self) -> datetime | None:
        return self.received.value if self.received.ok else None

    @property
    def occurred_state(self) -> FieldState:
        return FieldState(self.occurred.state)

    @property
    def received_state(self) -> FieldState:
        return FieldState(self.received.state)


@dataclass
class Issue:
    code: str
    ticket_id: str | None
    field_name: str
    detail: str
    source: str = ""
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    severity: str = "warn"


@dataclass
class Resolution:
    """版本判定结果：为什么是这个版本，以及不能判定时的原因。"""

    ticket_id: str
    state: VersionState
    version_id: str | None
    reason: str
    reason_code: str = ""
    basis: str = "有效期覆盖 + 确认状态 > 作废状态 > 生效起始 > 确认时间 > 接收时间；不使用文件名/修改时间"
    chain_break: bool = False
    considered: list[dict[str, Any]] = field(default_factory=list)

    @property
    def locatable(self) -> bool:
        return self.state is VersionState.CURRENT and bool(self.version_id)


@dataclass
class Draft:
    ticket_id: str
    status: Disposition
    watermark: str
    subject: str
    body: str
    version_id: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)
    requires_authorization: bool = False
    unconfirmed_items: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    statement: str = ""


@dataclass
class QueueItem:
    ticket_id: str
    reasons: list[str]
    owner: str
    due_at: str
    requires_authorization: bool = False
    note: str = ""
    created_at: datetime | None = None
    dispositions: str = Disposition.HUMAN_REVIEW.value


@dataclass
class ReviewRecord:
    review_id: str
    ticket_id: str
    reviewer: str
    decision: ReviewDecision
    note: str
    version_id: str = ""
    confirmed_items: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    draft_digest: str = ""


@dataclass
class IngestResult:
    tickets: list[Ticket] = field(default_factory=list)
    versions: list[KnowledgeVersion] = field(default_factory=list)
    handoffs: list[Handoff] = field(default_factory=list)
    ledger: list[LedgerRow] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    export_meta: dict[str, Any] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str = ""
    snapshot_at: str = ""
