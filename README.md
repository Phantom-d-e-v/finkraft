# Finkraft e-invoice submission core

A minimal version of how Finkraft submits invoices to a seller's UAE ASP (C2) and
tracks them through the buyer's ASP (C3) and the FTA (C5). The design note in
`docs/` explains the reasoning; this README covers running it and what is missing.

## Run it

Needs Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

No database, server or network. Everything runs in memory, and ASP A is an
in-process fake (`FakeAspAApi`) that reproduces its documented behaviour: the
10 req/s limit, timeouts after the invoice was stored, and 409 on duplicate numbers.

## What's here

| File | Purpose |
|---|---|
| `src/einvoice/state_machine.py` | Finkraft's invoice states and allowed transitions; rejects anything else |
| `src/einvoice/store.py` | In-memory store; one invoice number per seller |
| `src/einvoice/adapters/base.py` | The adapter contract both ASPs implement |
| `src/einvoice/adapters/asp_a.py` | ASP A adapter and the fake ASP A API |
| `src/einvoice/service.py` | Upload, validate, submit, retry, poll, webhooks |
| `tests/` | 55 tests: transitions, duplicates, timeouts, rate limits, out-of-order updates |

### States

```
RECEIVED ──> VALIDATED ──> SUBMITTING ──> SUBMITTED ──> ACCEPTED ──> DELIVERED ──> REPORTED
   │  ^          ^             │  │          │  (forward skips allowed)        (C3)         (C5)
   v  │          └─ NOT_SENT ──┘  │          └──> REJECTED
 INVALID                          v
                        SUBMISSION_UNKNOWN ──> retry (only if the ASP rejects duplicates)
```

Status updates only move forward. A repeated or late update is recorded and
ignored; a contradictory one (REJECTED after ACCEPTED) raises so someone looks at it.

## Assumptions

1. **ASP A's 409 body includes the existing `submission_id`.** Without it, a
   timeout followed by a 409 leaves an invoice we know was accepted but cannot track.
2. ASP A's 400 and 429 mean nothing was stored. 5xx and anything undocumented
   are treated as "may have been stored".
3. ASP A reports rejection errors on `GET` under `"error"` in its documented shape.
4. Invoice numbers are unique per seller across all ASPs, enforced by Finkraft
   before any ASP is called. Surrounding whitespace is ignored.
5. A 409 on the *first* attempt means the number was sent to the ASP outside
   Finkraft. The invoice is rejected (`DUPLICATE_AT_ASP`), not adopted.
6. An invoice stays with the ASP it was sent to. Switching ASP affects new
   invoices, and invoices that never reached the old ASP.
7. REJECTED is final. Correcting a rejected invoice is out of scope.

## What I skipped

- **ASP B adapter.** It's in the design note. The service tests use a small
  webhook-style stub (`FakeWebhookAdapter`) to show the contract and the
  out-of-order handling work for it, but it is a test stub, not an adapter.
- **Retry policy.** No backoff, no attempt cap, no client-side rate limiter;
  ASP A's 429 is the only throttle.
- **Reconciliation for ASP B timeouts.** A timed-out ASP B submission raises
  `ResubmitNotSafe` and stops there.
- **Unmatched updates.** Webhooks for unknown job ids are kept in a list and
  never replayed.
- **Validation.** Four PINT AE fields are checked, not the full rule set.
- **Persistence and concurrency.** The duplicate check is check-then-insert and
  only safe in one process.

## What I'd do next

1. Postgres with a unique constraint on `(seller_id, invoice_number)` and an
   outbox table, so the state change and the ASP call can't drift apart on a crash.
2. ASP B adapter with webhook signature checks, and a reconciliation job for
   timed-out submissions (match by invoice number in the ASP's reports, or ask
   the ASP for a lookup endpoint).
3. Retry worker with exponential backoff, attempt cap and a token bucket per ASP.
4. Replay unmatched webhooks once the submission is linked.
5. Full PINT AE validation against the official rules before submit.
6. Alerts on invoices stuck in SUBMISSION_UNKNOWN or on conflicting updates.
