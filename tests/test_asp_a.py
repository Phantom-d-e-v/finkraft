import pytest

from einvoice.adapters.asp_a import AspAAdapter, FakeAspAApi
from einvoice.adapters.base import SubmitOutcome
from einvoice.models import AspError, Invoice
from einvoice.state_machine import InvoiceState as S


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def invoice(number="INV-001", **fields):
    return Invoice(id=number, seller_id="seller-1", invoice_number=number,
                   fields={"BT-48": "100000000000003", **fields})


@pytest.fixture
def api():
    return FakeAspAApi(clock=Clock())


@pytest.fixture
def adapter(api):
    return AspAAdapter(api)


def test_submit_is_acknowledged_with_submission_id(adapter):
    result = adapter.submit(invoice())
    assert result.outcome == SubmitOutcome.ACKNOWLEDGED
    assert result.submission_id == "A-0001"
    assert result.state == S.SUBMITTED


def test_missing_field_is_rejected_with_normalised_error(adapter):
    result = adapter.submit(invoice(**{"BT-48": ""}))
    assert result.outcome == SubmitOutcome.REJECTED
    assert result.errors == (AspError(rule="E104", field="BT-48"),)


def test_timeout_after_acceptance_is_unknown_not_failed(api, adapter):
    api.timeout_after_accept = True
    result = adapter.submit(invoice())
    assert result.outcome == SubmitOutcome.UNKNOWN
    assert api.post_count == 1  # the ASP did store it


def test_retry_after_timeout_returns_existing_submission_id(api, adapter):
    api.timeout_after_accept = True
    adapter.submit(invoice())
    result = adapter.submit(invoice())
    assert result.outcome == SubmitOutcome.ALREADY_SUBMITTED
    assert result.submission_id == "A-0001"
    assert api.post_count == 1


def test_eleventh_request_in_a_second_is_not_sent(api, adapter):
    results = [adapter.submit(invoice(f"INV-{i}")) for i in range(11)]
    assert results[-1].outcome == SubmitOutcome.NOT_SENT
    assert api.post_count == 10


def test_rate_limit_window_resets(api, adapter):
    for i in range(10):
        adapter.submit(invoice(f"INV-{i}"))
    api._clock.now = 1.0
    assert adapter.submit(invoice("INV-10")).outcome == SubmitOutcome.ACKNOWLEDGED


def test_server_error_is_unknown():
    class Broken:
        def request(self, *args, **kwargs):
            return 503, {}
    assert AspAAdapter(Broken()).submit(invoice()).outcome == SubmitOutcome.UNKNOWN


@pytest.mark.parametrize("asp_status, state", [
    ("RECEIVED", S.SUBMITTED),
    ("VALIDATED", S.ACCEPTED),
    ("DELIVERED", S.DELIVERED),
    ("REPORTED", S.REPORTED),
])
def test_polled_status_is_mapped(api, adapter, asp_status, state):
    sub_id = adapter.submit(invoice()).submission_id
    api.set_status(sub_id, asp_status)
    update = adapter.fetch_status(sub_id)
    assert (update.state, update.asp_status) == (state, asp_status)


def test_polled_rejection_carries_error(api, adapter):
    sub_id = adapter.submit(invoice()).submission_id
    api.set_status(sub_id, "REJECTED", {"code": "E210", "field": "BT-110"})
    update = adapter.fetch_status(sub_id)
    assert update.state == S.REJECTED
    assert update.errors == (AspError(rule="E210", field="BT-110"),)


def test_unknown_submission_raises(adapter):
    with pytest.raises(LookupError):
        adapter.fetch_status("A-9999")
