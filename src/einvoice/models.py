from dataclasses import dataclass, field
from datetime import datetime

from einvoice.state_machine import InvoiceState


@dataclass(frozen=True)
class AspError:
    """One validation error, normalised from either ASP's error format."""
    rule: str          # ASP A "code" (E104) or ASP B "rule"
    message: str = ""
    field: str = ""    # PINT AE business term, e.g. BT-48


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    state: InvoiceState
    note: str = ""


@dataclass
class Invoice:
    id: str
    seller_id: str
    invoice_number: str
    fields: dict[str, str]  # PINT AE business terms keyed by BT id
    state: InvoiceState = InvoiceState.RECEIVED
    asp_id: str | None = None         # fixed at first submission; never changes after
    submission_id: str | None = None  # ASP A submission_id or ASP B job_id
    errors: list[AspError] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)
