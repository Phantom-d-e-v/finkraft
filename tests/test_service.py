from datetime import UTC, datetime
from itertools import count

import pytest

from einvoice.adapters.asp_a import AspAAdapter, FakeAspAApi
from einvoice.adapters.base import StatusUpdate, SubmitOutcome, SubmitResult
from einvoice.service import ResubmitNotSafe, SubmissionService
from einvoice.state_machine import InvalidTransition, InvoiceState as S
from einvoice.store import DuplicateInvoice, InMemoryStore

FIELDS = {"BT-2": "2026-10-29", "BT-5": "AED", "BT-48": "100000000000003", "BT-112": "1050.00"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeWebhookAdapter:
    """Shaped like ASP B: async ack, status only by webhook, no duplicate check."""
    asp_id = "asp_b"
    status_delivery = "webhook"
    rejects_duplicates = False
    STATUS_MAP = {"QUEUED": S.SUBMITTED, "ACCEPTED": S.ACCEPTED, "FAILED": S.REJECTED,
                  "BUYER_RECEIVED": S.DELIVERED, "TAX_CONFIRMED": S.REPORTED}

    def __init__(self):
        self.next_result = None
        self.sent = 0

    def submit(self, invoice):
        self.sent += 1
        return self.next_result or SubmitResult(SubmitOutcome.ACKNOWLEDGED, f"job-{self.sent}")

    def parse_webhook(self, body):
        return StatusUpdate(body["job_id"], self.STATUS_MAP[body["status"]], body["status"])


@pytest.fixture
def api():
    return FakeAspAApi(clock=Clock())


@pytest.fixture
def asp_b():
    return FakeWebhookAdapter()


@pytest.fixture
def service(api, asp_b):
    ids = count(1)
    return SubmissionService(
        InMemoryStore(),
        {"asp_a": AspAAdapter(api), "asp_b": asp_b},
        seller_asp={"seller-1": "asp_a"},
        clock=lambda: datetime(2026, 10, 29, tzinfo=UTC),
        new_id=lambda: f"inv-{next(ids)}",
    )


def upload(service, number="INV-001", **overrides):
    return service.upload("seller-1", number, {**FIELDS, **overrides})


# --- happy path -----------------------------------------------------------

def test_invoice_moves_from_upload_to_reported_via_polling(service, api):
    invoice = upload(service)
    service.submit(invoice.id)
    assert invoice.state == S.SUBMITTED

    for asp_status, state in [("VALIDATED", S.ACCEPTED), ("DELIVERED", S.DELIVERED),
                              ("REPORTED", S.REPORTED)]:
        api.set_status(invoice.submission_id, asp_status)
        service.poll()
        assert invoice.state == state

    assert [h.state for h in invoice.history] == [
        S.RECEIVED, S.VALIDATED, S.SUBMITTING, S.SUBMITTED, S.ACCEPTED, S.DELIVERED, S.REPORTED]


# --- validation and duplicates --------------------------------------------

def test_invalid_invoice_is_never_sent(service, api):
    invoice = upload(service, **{"BT-48": ""})
    assert invoice.state == S.INVALID
    assert invoice.errors[0].field == "BT-48"
    with pytest.raises(InvalidTransition):
        service.submit(invoice.id)
    assert api.post_count == 0


def test_corrected_invoice_keeps_its_number(service):
    invoice = upload(service, **{"BT-48": ""})
    invoice.fields["BT-48"] = "100000000000003"
    service.revalidate(invoice)
    assert invoice.state == S.VALIDATED


def test_duplicate_upload_is_rejected_before_reaching_asp(service, api):
    service.submit(upload(service).id)
    with pytest.raises(DuplicateInvoice):
        upload(service)
    assert api.post_count == 1


# --- submit failures --------------------------------------------------------

def test_timeout_after_acceptance_is_recovered_without_double_submit(service, api):
    invoice = upload(service)
    api.timeout_after_accept = True
    service.submit(invoice.id)
    assert invoice.state == S.SUBMISSION_UNKNOWN

    service.submit_pending()  # retry gets 409 with the existing id
    assert invoice.state == S.SUBMITTED
    assert invoice.submission_id == "A-0001"
    assert api.post_count == 1


def test_number_already_at_asp_on_first_attempt_is_rejected_not_adopted(service, api):
    api.request("POST", "/invoices", json={"seller_id": "seller-1", "invoice_number": "INV-001",
                                           "fields": FIELDS})  # sent outside Finkraft
    invoice = upload(service)
    service.submit(invoice.id)
    assert invoice.state == S.REJECTED
    assert invoice.errors[0].rule == "DUPLICATE_AT_ASP"


def test_asp_rejection_on_submit_keeps_errors(service, api):
    api.REQUIRED_FIELDS = ("BT-48", "BT-110")  # ASP checks a field we don't
    invoice = upload(service)
    service.submit(invoice.id)
    assert invoice.state == S.REJECTED
    assert invoice.errors[0].rule == "E104"


def test_rate_limited_invoices_wait_and_go_later(service, api):
    invoices = [upload(service, f"INV-{i:03d}") for i in range(12)]
    service.submit_pending()
    assert sum(i.state == S.VALIDATED for i in invoices) == 2

    api._clock.now = 1.0
    service.submit_pending()
    assert all(i.state == S.SUBMITTED for i in invoices)
    assert api.post_count == 12


def test_timed_out_invoice_is_not_resent_to_asp_without_duplicate_check(service, asp_b):
    service.seller_asp["seller-1"] = "asp_b"
    invoice = upload(service)
    asp_b.next_result = SubmitResult(SubmitOutcome.UNKNOWN)
    service.submit(invoice.id)

    service.submit_pending()
    with pytest.raises(ResubmitNotSafe):
        service.submit(invoice.id)
    assert asp_b.sent == 1


# --- switching ASPs ---------------------------------------------------------

def test_switching_asp_affects_new_invoices_only(service):
    first = upload(service, "INV-001")
    service.submit(first.id)
    service.seller_asp["seller-1"] = "asp_b"
    second = upload(service, "INV-002")
    service.submit(second.id)
    assert (first.asp_id, second.asp_id) == ("asp_a", "asp_b")


# --- out-of-order, late and repeated webhooks -------------------------------

def submitted_to_b(service):
    service.seller_asp["seller-1"] = "asp_b"
    invoice = upload(service)
    service.submit(invoice.id)
    return invoice


def test_webhooks_out_of_order_end_in_latest_state(service):
    invoice = submitted_to_b(service)
    for status in ["TAX_CONFIRMED", "ACCEPTED", "BUYER_RECEIVED", "TAX_CONFIRMED"]:
        service.handle_webhook("asp_b", {"job_id": invoice.submission_id, "status": status})
    assert invoice.state == S.REPORTED
    assert sum("ignored stale" in h.note for h in invoice.history) == 3


def test_conflicting_webhook_is_flagged_not_applied(service):
    invoice = submitted_to_b(service)
    service.handle_webhook("asp_b", {"job_id": invoice.submission_id, "status": "ACCEPTED"})
    assert not service.handle_webhook("asp_b", {"job_id": invoice.submission_id, "status": "FAILED"})
    assert invoice.state == S.ACCEPTED
    assert service.needs_review == [(invoice.id, "FAILED conflicts with ACCEPTED")]


def test_conflict_during_poll_does_not_stop_other_invoices(service, api):
    first, second = upload(service, "INV-001"), upload(service, "INV-002")
    service.submit_pending()
    api.set_status(first.submission_id, "VALIDATED")
    service.poll()
    api.set_status(first.submission_id, "REJECTED")  # conflicts with ACCEPTED
    api.set_status(second.submission_id, "VALIDATED")
    service.poll()
    assert (first.state, second.state) == (S.ACCEPTED, S.ACCEPTED)
    assert service.needs_review[0][0] == first.id


def test_failed_status_lookup_does_not_stop_other_invoices(service, api):
    first, second = upload(service, "INV-001"), upload(service, "INV-002")
    service.submit_pending()
    first.submission_id = "A-9999"  # ASP returns 404 for this one
    api.set_status(second.submission_id, "VALIDATED")
    service.poll()
    assert second.state == S.ACCEPTED


def test_webhook_before_submit_response_is_applied_once_linked(service, asp_b):
    service.handle_webhook("asp_b", {"job_id": "job-1", "status": "ACCEPTED"})
    invoice = submitted_to_b(service)  # FakeWebhookAdapter returns job-1
    assert invoice.state == S.ACCEPTED
    assert service.unmatched == []


def test_webhook_for_unknown_job_is_parked(service):
    assert not service.handle_webhook("asp_b", {"job_id": "job-404", "status": "ACCEPTED"})
    assert service.unmatched[0][1].submission_id == "job-404"
