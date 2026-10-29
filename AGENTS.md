# AGENTS.md

Notes for coding agents (and humans) working in this repo.

## What this is

A minimal invoice submission core for a UAE e-invoicing platform. Finkraft is not an
ASP: it takes seller invoices (C1), submits them to the seller's chosen accredited
ASP (C2), and tracks status through the buyer's ASP (C3) and the FTA (C5).

Hard rule from the domain: an invoice is government tax data. It must never be lost
and never be submitted twice. Every change is judged against that rule first.

## Layout

```
src/einvoice/
  models.py         Invoice, Submission, StatusUpdate, enums
  state_machine.py  InvoiceState + allowed transitions; raises on invalid ones
  store.py          in-memory store; unique (seller_id, invoice_number)
  adapters/
    base.py         AspAdapter protocol, the one contract every ASP implements
    asp_a.py        ASP A adapter + in-process mock of its API
  service.py        submit / poll / apply status update
tests/              pytest, one file per module
docs/               design note, 30-day plan, AI usage appendix (source for the PDF)
```

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Conventions

- Python 3.11+, standard library only at runtime. pytest is the only dev dependency.
- Dataclasses and enums for data, `typing.Protocol` for the adapter contract.
- No database, web server or UI. Storage is in memory behind `store.py`.
- Time and IDs are injected (clock, id generator) so tests are deterministic.
  No `time.sleep` in tests.
- State changes go through `state_machine.transition()` only. Never assign
  `invoice.state` directly.
- ASP status strings are mapped to `InvoiceState` inside the adapter. Nothing outside
  `adapters/` knows ASP-specific names (`RECEIVED`, `QUEUED`, `BUYER_RECEIVED`, ...).
- Status updates are applied monotonically: an update that would move an invoice
  backwards is recorded in history and ignored, never raised as an error.
- Errors from ASPs are normalised to `AspError(rule, field, message)` before they
  leave the adapter.

## Tests

- Every allowed transition has a test; at least one invalid transition per state
  is rejected.
- Failure cases are tested explicitly: duplicate invoice number per seller,
  timeout after acceptance, 409 on retry, out-of-order and repeated status updates.
- A change that touches `state_machine.py` or `service.py` is not done until
  `pytest -q` passes.

## Out of scope

PINT AE schema validation beyond a few required fields, real HTTP, auth, persistence,
multi-process concurrency, the ASP B adapter (designed in docs, not built). If you
add any of these, update the README's "What I skipped" section.

## Commits

Small, one concern each, imperative subject line (`Add ASP A timeout reconciliation`).
