# Finkraft e-invoice submission core

A minimal version of how Finkraft submits invoices to a seller's UAE ASP (C2) and
tracks them through the buyer's ASP (C3) and the FTA (C5). The design note (Part 1)
and 30-day plan (Part 3) are in the separate PDF. This README is just how to run
it, what I assumed, and what's missing.

## Run it

Needs Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

No database, server or network. Everything runs in memory. ASP A is an in-process
fake (`FakeAspAApi`) that behaves the way its spec describes: 10 req/s limit,
timeouts after the invoice was already stored, and 409 on duplicate numbers.

## What's here

| File | Purpose |
|---|---|
| `src/einvoice/state_machine.py` | Finkraft's invoice states and allowed transitions; rejects anything else |
| `src/einvoice/store.py` | In-memory store; one invoice number per seller |
| `src/einvoice/adapters/base.py` | The adapter contract both ASPs implement |
| `src/einvoice/adapters/asp_a.py` | ASP A adapter and the fake ASP A API |
| `src/einvoice/service.py` | Upload, validate, submit, retry, poll, webhooks |
| `tests/` | 58 tests: transitions, duplicates, timeouts, rate limits, out-of-order and conflicting updates |

### States

```
RECEIVED            -> VALIDATED | INVALID
INVALID             -> RECEIVED              (seller corrects it)
VALIDATED           -> SUBMITTING
SUBMITTING          -> SUBMITTED             (ASP acknowledged)
                    -> REJECTED              (400, or 409 on first attempt)
                    -> VALIDATED             (not sent: 429 / connection refused)
                    -> SUBMISSION_UNKNOWN    (timeout, 5xx, anything undocumented)
SUBMISSION_UNKNOWN  -> SUBMITTING            (retry, only if the ASP rejects duplicates)
                    -> SUBMITTED             (for a future reconciliation job; unused)
SUBMITTED           -> ACCEPTED | REJECTED | DELIVERED (C3) | REPORTED (C5)
ACCEPTED            -> DELIVERED | REPORTED
DELIVERED           -> REPORTED
REJECTED, REPORTED     terminal
```

Forward skips after SUBMITTED are on purpose: ASP updates can be lost or arrive
out of order.

Status updates only move forward. Repeated or late updates get recorded in the
invoice history and ignored. A contradictory one (REJECTED after ACCEPTED) leaves
the state alone and goes to `needs_review`. It doesn't raise, because a raise would
stop a poll run or make an ASP redeliver the webhook forever.

## Assumptions

1. ASP A's 409 body includes the existing `submission_id`. This is the one the
   design depends on most: without it, a timeout followed by a 409 leaves an
   invoice we know was accepted but can't track.
2. For ASP A, a 400, a 429 or a refused connection means nothing was stored. A
   timeout, a 5xx, any undocumented status or an exception from the adapter means
   it may have been stored, so it's never resent blindly.
3. ASP A reports rejection errors on `GET` under `"error"`, in its documented shape.
4. Invoice numbers are unique per seller across all ASPs, enforced by Finkraft
   before any ASP is called. Leading and trailing whitespace is treated as an
   upload artefact and stripped. Matching is otherwise exact, so `INV-1` and `inv-1`
   are different numbers.
5. A 409 on the *first* attempt means the number was sent to the ASP outside
   Finkraft. The invoice is rejected (`DUPLICATE_AT_ASP`), not adopted.
6. One active ASP per seller at a time. Switching ASP only applies to new invoices
   and ones that never reached the old ASP. Several ASPs per seller at once (e.g.
   routed by buyer or invoice type) isn't modelled; the contract supports it, but
   `seller_asp` would need to become a routing rule.
7. REJECTED is final, and a REJECTED or INVALID invoice keeps its number. Correcting
   and reissuing a rejected invoice is out of scope.

## What I skipped

- ASP B adapter: designed in the note, not built. The service tests use a small
  webhook-style stub (`FakeWebhookAdapter`), which is enough to show the contract
  and the out-of-order handling work for it. It's a test stub, not a real adapter.
- Retry policy: no backoff, no attempt cap, no client-side rate limiter. ASP A's
  429 is the only throttle, and the `retry_after` it returns is captured but ignored.
- Polling errors: a failed or rate-limited `GET` is skipped until the next poll run.
  Polls count against ASP A's 10 req/s as well.
- ASP B timeout reconciliation: a timed-out ASP B submission raises
  `ResubmitNotSafe` and stops there.
- Transport: no HTTP client, auth or TLS. The adapter only runs against the fake.
- Bulk upload and enrichment: the service takes one invoice dict at a time. No
  CSV/Excel parsing, and no PINT AE enrichment.
- Validation: only four PINT AE fields are checked, not the full rule set.
- Audit and retention: history lives in memory, and only the raw ASP status string
  is kept, not the full request and response bodies.
- Persistence/concurrency: the duplicate check is check-then-insert, so it's only
  safe in a single process.

## Where an invoice can still get stuck

These break the "never lost" rule in this version, and each one is covered by
an item in the next section:

- Crash while an invoice is in SUBMITTING: nothing recovers it (fix: outbox, item 1).
- A timed-out ASP B submission sits in SUBMISSION_UNKNOWN until someone reconciles it (item 2).
- `needs_review` and parked webhooks for unknown submissions are only lists. Nobody
  is alerted (item 6).

## What I'd do next

1. Postgres with a unique constraint on `(seller_id, invoice_number)` and an
   outbox table, so the state change and the ASP call can't drift apart on a crash.
2. ASP B adapter with webhook signature checks, and a reconciliation job for
   timed-out submissions (match by invoice number in the ASP's reports, or ask
   the ASP for a lookup endpoint).
3. Retry worker with exponential backoff, attempt cap and a token bucket per ASP,
   shared between submits and polls.
4. Store the raw ASP request and response for every call, for audit.
5. Full PINT AE validation against the official rules before submit.
6. Alerts on invoices stuck in SUBMISSION_UNKNOWN, on `needs_review` entries, and on
   webhooks that stay unmatched.
