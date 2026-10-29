"""ASP A: synchronous submit, polling only, rejects duplicate invoice numbers with 409.

Assumptions beyond the spec (also listed in the README):
- The 409 body includes the existing submission_id. Without it a timeout
  followed by a 409 leaves us unable to track the invoice.
- 400 means the request was refused and nothing was stored. Its body is the
  documented error shape {"code", "field"}.
- 429 means nothing was stored.
- A REJECTED status from polling carries the same error shape under "error".
"""

import time
from collections import deque
from typing import Callable, Literal

from einvoice.adapters.base import StatusUpdate, SubmitOutcome, SubmitResult
from einvoice.models import AspError, Invoice
from einvoice.state_machine import InvoiceState as S

STATUS_MAP = {
    "RECEIVED": S.SUBMITTED,
    "VALIDATED": S.ACCEPTED,
    "REJECTED": S.REJECTED,
    "DELIVERED": S.DELIVERED,  # C3
    "REPORTED": S.REPORTED,    # C5
}


def _error(body: dict) -> AspError:
    return AspError(rule=body.get("code", ""), field=body.get("field", ""),
                    message=body.get("message", ""))


class AspAAdapter:
    asp_id = "asp_a"
    status_delivery: Literal["poll", "webhook"] = "poll"

    def __init__(self, client) -> None:
        # client.request(method, path, json=None) -> (status_code, body); may raise TimeoutError
        self._client = client

    def submit(self, invoice: Invoice) -> SubmitResult:
        payload = {"seller_id": invoice.seller_id,
                   "invoice_number": invoice.invoice_number.strip(),
                   "fields": invoice.fields}
        try:
            status, body = self._client.request("POST", "/invoices", json=payload)
        except TimeoutError:
            return SubmitResult(SubmitOutcome.UNKNOWN)
        except ConnectionRefusedError:
            return SubmitResult(SubmitOutcome.NOT_SENT)

        if status == 200:
            return SubmitResult(SubmitOutcome.ACKNOWLEDGED, body["submission_id"],
                                STATUS_MAP[body["status"]])
        if status == 409:
            return SubmitResult(SubmitOutcome.ALREADY_SUBMITTED, body["submission_id"])
        if status == 429:
            return SubmitResult(SubmitOutcome.NOT_SENT, retry_after_s=body.get("retry_after", 1.0))
        if status == 400:
            return SubmitResult(SubmitOutcome.REJECTED, errors=(_error(body),))
        # 5xx or anything undocumented: assume it may have been stored, never resend blind.
        return SubmitResult(SubmitOutcome.UNKNOWN)

    def fetch_status(self, submission_id: str) -> StatusUpdate:
        status, body = self._client.request("GET", f"/invoices/{submission_id}")
        if status != 200:
            raise LookupError(f"ASP A returned {status} for {submission_id}")
        errors = (_error(body["error"]),) if body.get("error") else ()
        return StatusUpdate(submission_id, STATUS_MAP[body["status"]], body["status"], errors)

    def parse_webhook(self, body: dict) -> StatusUpdate:
        raise NotImplementedError("ASP A has no webhooks; poll with fetch_status")


class FakeAspAApi:
    """In-process stand-in for ASP A's HTTP API, with its documented misbehaviour."""

    RATE_LIMIT_PER_S = 10
    REQUIRED_FIELDS = ("BT-48",)  # buyer VAT identifier, the spec's example error field

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._recent: deque[float] = deque()
        self._invoices: dict[str, dict] = {}
        self._by_number: dict[tuple[str, str], str] = {}
        self.timeout_after_accept = False  # next accepted POST raises TimeoutError
        self.post_count = 0  # accepted POSTs, so tests can prove nothing was sent twice

    def request(self, method: str, path: str, json: dict | None = None) -> tuple[int, dict]:
        if self._rate_limited():
            return 429, {"code": "RATE_LIMIT", "retry_after": 1.0}
        if method == "POST" and path == "/invoices":
            return self._post(json)
        if method == "GET" and path.startswith("/invoices/"):
            sub = self._invoices.get(path.removeprefix("/invoices/"))
            return (200, dict(sub)) if sub else (404, {"code": "NOT_FOUND"})
        return 404, {"code": "NOT_FOUND"}

    def set_status(self, submission_id: str, status: str, error: dict | None = None) -> None:
        """Test hook: move an invoice along as the real ASP would."""
        self._invoices[submission_id].update(status=status, error=error)

    def _rate_limited(self) -> bool:
        now = self._clock()
        while self._recent and now - self._recent[0] >= 1.0:
            self._recent.popleft()
        if len(self._recent) >= self.RATE_LIMIT_PER_S:
            return True
        self._recent.append(now)
        return False

    def _post(self, body: dict) -> tuple[int, dict]:
        for bt in self.REQUIRED_FIELDS:
            if not body["fields"].get(bt):
                return 400, {"code": "E104", "field": bt}
        key = (body["seller_id"], body["invoice_number"])
        if key in self._by_number:
            return 409, {"code": "DUPLICATE", "submission_id": self._by_number[key]}
        submission_id = f"A-{len(self._invoices) + 1:04d}"
        self._invoices[submission_id] = {"submission_id": submission_id, "status": "RECEIVED"}
        self._by_number[key] = submission_id
        self.post_count += 1
        if self.timeout_after_accept:
            self.timeout_after_accept = False
            raise TimeoutError("ASP A accepted the invoice but the response was lost")
        return 200, {"submission_id": submission_id, "status": "RECEIVED"}
