"""Upload, submit and track invoices. The only place that changes invoice state."""

import uuid
from datetime import UTC, datetime
from typing import Callable

from einvoice.adapters.base import AspAdapter, StatusUpdate, SubmitOutcome, SubmitResult
from einvoice.models import AspError, HistoryEntry, Invoice
from einvoice.state_machine import InvalidTransition, InvoiceState as S, is_stale, transition
from einvoice.store import InMemoryStore

# shortcut: a handful of PINT AE mandatory terms, not the full rule set;
# replace with the official PINT AE schematron before go-live.
REQUIRED_FIELDS = ("BT-2", "BT-5", "BT-48", "BT-112")  # issue date, currency, buyer VAT id, total


def validate(fields: dict[str, str]) -> list[AspError]:
    return [AspError(rule="MISSING", field=bt, message=f"{bt} is required")
            for bt in REQUIRED_FIELDS if not fields.get(bt)]


class ResubmitNotSafe(Exception):
    """A timed-out invoice at an ASP without duplicate checks: reconcile, don't resend."""


class SubmissionService:
    def __init__(self, store: InMemoryStore, adapters: dict[str, AspAdapter],
                 seller_asp: dict[str, str],
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 new_id: Callable[[], str] = lambda: uuid.uuid4().hex) -> None:
        self.store = store
        self.adapters = adapters
        self.seller_asp = seller_asp  # seller's current ASP choice; changeable at any time
        self.clock = clock
        self.new_id = new_id
        self.unmatched: list[tuple[str, StatusUpdate]] = []  # updates for submissions not linked yet
        self.needs_review: list[tuple[str, str]] = []  # (invoice id, reason) for a human to resolve

    def upload(self, seller_id: str, invoice_number: str, fields: dict[str, str]) -> Invoice:
        invoice = Invoice(id=self.new_id(), seller_id=seller_id,
                          invoice_number=invoice_number, fields=fields)
        self.store.add(invoice)  # raises DuplicateInvoice before anything else happens
        invoice.history.append(HistoryEntry(self.clock(), S.RECEIVED, "uploaded"))
        self.revalidate(invoice)
        return invoice

    def revalidate(self, invoice: Invoice) -> None:
        if invoice.state == S.INVALID:
            self._move(invoice, S.RECEIVED, "corrected by seller")
        invoice.errors = validate(invoice.fields)
        self._move(invoice, S.INVALID if invoice.errors else S.VALIDATED)

    def submit(self, invoice_id: str) -> Invoice:
        invoice = self.store.get(invoice_id)
        retrying = invoice.state == S.SUBMISSION_UNKNOWN
        # An invoice stays with the ASP it was first sent to, even if the seller switches.
        asp_id = invoice.asp_id or self.seller_asp[invoice.seller_id]
        adapter = self.adapters[asp_id]
        if retrying and not adapter.rejects_duplicates:
            raise ResubmitNotSafe(f"{invoice.id} timed out at {asp_id}; reconcile before resending")

        self._move(invoice, S.SUBMITTING, f"attempt via {asp_id}")
        invoice.asp_id = asp_id
        try:
            result = adapter.submit(invoice)
        except Exception:
            self._move(invoice, S.SUBMISSION_UNKNOWN, "adapter raised")
            raise
        self._apply_submit_result(invoice, result, retrying)
        return invoice

    def submit_pending(self) -> None:
        # shortcut: no backoff or attempt cap; a real worker schedules retries from retry_after_s.
        for invoice in self.store.in_state(S.VALIDATED, S.SUBMISSION_UNKNOWN):
            if invoice.state == S.SUBMISSION_UNKNOWN and not self.adapters[invoice.asp_id].rejects_duplicates:
                continue
            self.submit(invoice.id)

    def poll(self) -> None:
        for invoice in self.store.in_state(S.SUBMITTED, S.ACCEPTED, S.DELIVERED):
            adapter = self.adapters[invoice.asp_id]
            if adapter.status_delivery != "poll":
                continue
            try:
                update = adapter.fetch_status(invoice.submission_id)
            except (LookupError, TimeoutError):
                continue  # one failed GET must not stop the rest; next poll run retries it
            self.apply_update(invoice.asp_id, update)

    def handle_webhook(self, asp_id: str, body: dict) -> bool:
        return self.apply_update(asp_id, self.adapters[asp_id].parse_webhook(body))

    def apply_update(self, asp_id: str, update: StatusUpdate) -> bool:
        """Apply an ASP status update. Returns False if it was ignored.

        Repeated or late updates are recorded and ignored. A conflicting one
        (REJECTED after ACCEPTED) leaves the state as is and goes to needs_review.
        It doesn't raise: a raise would stop a poll run or make the ASP redeliver forever.
        """
        invoice = self.store.by_submission(asp_id, update.submission_id)
        if invoice is None:
            self.unmatched.append((asp_id, update))
            return False
        if is_stale(invoice.state, update.state):
            invoice.history.append(HistoryEntry(
                self.clock(), invoice.state, f"ignored stale {update.asp_status}"))
            return False
        try:
            self._move(invoice, update.state, f"{asp_id}: {update.asp_status}")
        except InvalidTransition:
            self.needs_review.append((invoice.id, f"{update.asp_status} conflicts with {invoice.state}"))
            invoice.history.append(HistoryEntry(
                self.clock(), invoice.state, f"conflicting {update.asp_status}, needs review"))
            return False
        if update.errors:
            invoice.errors = list(update.errors)
        return True

    def _apply_submit_result(self, invoice: Invoice, result: SubmitResult, retrying: bool) -> None:
        match result.outcome:
            case SubmitOutcome.ACKNOWLEDGED:
                self._track(invoice, result.submission_id, "acknowledged")
                if result.state and result.state != S.SUBMITTED:
                    self._move(invoice, result.state, "status in submit response")
            case SubmitOutcome.ALREADY_SUBMITTED if retrying:
                # Our earlier attempt timed out after the ASP stored it.
                self._track(invoice, result.submission_id, "found existing submission after timeout")
            case SubmitOutcome.ALREADY_SUBMITTED:
                # First attempt, yet the ASP has this number: sent outside Finkraft. Not ours to track.
                invoice.errors = [AspError(rule="DUPLICATE_AT_ASP", field="BT-1",
                                           message=f"ASP already has submission {result.submission_id}")]
                self._move(invoice, S.REJECTED, "duplicate at ASP")
            case SubmitOutcome.REJECTED:
                invoice.errors = list(result.errors)
                self._move(invoice, S.REJECTED, "rejected on submit")
            case SubmitOutcome.NOT_SENT:
                invoice.asp_id = None  # never reached the ASP, so a seller's switch still applies
                self._move(invoice, S.VALIDATED, "not sent, will retry")
            case SubmitOutcome.UNKNOWN:
                self._move(invoice, S.SUBMISSION_UNKNOWN, "no answer from ASP")

    def _track(self, invoice: Invoice, submission_id: str, note: str) -> None:
        invoice.submission_id = submission_id
        self.store.link_submission(invoice)
        self._move(invoice, S.SUBMITTED, note)
        # A webhook can beat the submit response; apply anything parked for this submission.
        key = (invoice.asp_id, submission_id)
        parked = [u for asp, u in self.unmatched if (asp, u.submission_id) == key]
        self.unmatched = [(asp, u) for asp, u in self.unmatched if (asp, u.submission_id) != key]
        for update in parked:
            self.apply_update(invoice.asp_id, update)

    def _move(self, invoice: Invoice, target: S, note: str = "") -> None:
        invoice.state = transition(invoice.state, target)
        invoice.history.append(HistoryEntry(self.clock(), target, note))
