"""The one contract every ASP adapter implements.

Adapters translate; they do not decide. They never touch the store or the
state machine. They turn one HTTP exchange into a SubmitResult or StatusUpdate
in Finkraft's vocabulary, and the service decides what that means.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from einvoice.models import AspError, Invoice
from einvoice.state_machine import InvoiceState


class SubmitOutcome(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"            # ASP took it; we have its id
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"  # ASP already has it (e.g. A's 409); we have its id
    REJECTED = "REJECTED"                    # ASP refused the content; do not retry as is
    NOT_SENT = "NOT_SENT"                    # ASP definitely did not take it (429, refused); safe to retry
    UNKNOWN = "UNKNOWN"                      # timeout or 5xx: the ASP may or may not have it


@dataclass(frozen=True)
class SubmitResult:
    outcome: SubmitOutcome
    submission_id: str | None = None
    state: InvoiceState | None = None  # status the ASP reported with the ack, if any
    errors: tuple[AspError, ...] = ()
    retry_after_s: float | None = None


@dataclass(frozen=True)
class StatusUpdate:
    submission_id: str
    state: InvoiceState
    asp_status: str  # raw ASP value, kept for the audit trail
    errors: tuple[AspError, ...] = ()


class AspAdapter(Protocol):
    asp_id: str
    status_delivery: Literal["poll", "webhook"]
    # True if resending an invoice the ASP may already have is safe (ASP A's 409).
    # False means a timed-out submission must be reconciled, never resent (ASP B).
    rejects_duplicates: bool

    def submit(self, invoice: Invoice) -> SubmitResult: ...

    def fetch_status(self, submission_id: str) -> StatusUpdate:
        """Poll-based ASPs only (ASP A)."""
        ...

    def parse_webhook(self, body: dict) -> StatusUpdate:
        """Webhook-based ASPs only (ASP B)."""
        ...
