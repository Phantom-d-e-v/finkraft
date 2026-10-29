# Part 3: First 30 days as CTO

**Goal:** first production release in 6 weeks (about 11 Dec) with both ASPs live,
three weeks before the 1 Jan 2027 mandate. Launch customers have already appointed
their ASPs (Phase 1 deadline was 30 Oct), so shipping one ASP would leave some of them
unable to comply. Team: BE1–3 backend, FE frontend. I write code for the first two weeks.

## Week 1: confirm the facts, start the core

- Founders and 3–5 launch customers: which ASP each uses, volumes, what their exports look like.
- Sandbox access to both ASPs. Confirm in writing what the design assumes (A's 409 returns
  the `submission_id`, B echoes a client reference, B signs webhooks). It's cheap to change now and expensive in week 4.
- **Me:** take-home core onto Postgres: state machine, adapter contract, outbox, unique
  (seller, invoice number). **BE1:** ASP A spike. **BE2:** ASP B spike and webhook receiver.
  **BE3:** CSV/Excel upload and PINT AE validation. **FE:** upload and status wireframes with two customers.
- Friday: states and contract frozen in a one-page ADR.

## Week 2: one invoice end to end

- Staging: a real customer CSV → validated → ASP A → polled to REPORTED; ASP B to ACCEPTED by webhook.
- Outbox worker, backoff, a token bucket per ASP, raw ASP request and response stored for audit.
- CI, staging, structured logs, and alerts on stuck invoices from day one. FE runs against the real API.

## Weeks 3–4: finish both ASPs, then try to break them

- BE1/BE2 take their ASP to feature complete. BE3 owns reconciliation (SUBMISSION_UNKNOWN,
  needs_review, unmatched webhooks). FE: error view, fix and resubmit, ops queue.
- Failure drills as automated tests: kill the worker mid-submit, replay and reorder
  webhooks, time out after acceptance. "Never lost, never twice" must hold in every one.
- Two design partners run real invoice files through the sandboxes. Security pass:
  tenant isolation, webhook signatures, secrets.
- **End of week 4: feature freeze, go/no-go per ASP.** If B fails the drills, release 1 ships
  with A and B follows a week later; B's customers hear it now, not on 10 Dec.

Weeks 5–6: load test at month-end volume, runbooks, on-call, release.

## What I'd cut

- **ERP connectors.** Each is its own project, and every ERP already exports CSV/Excel.
  They add convenience, not compliance.
- **AI extraction and enrichment.** Launch customers send structured data. On tax data
  I want the deterministic path proven before adding a model that can be confidently wrong.
- **Self-serve ASP switching and analytics.** Switching is a config change ops can make for
  the few customers who need it in month one. FE time goes to fixing rejected invoices instead.

## The first two things I'd measure

1. **Stuck invoices:** count and age of non-final invoices past SLA (SUBMISSION_UNKNOWN,
   needs_review, no C5 confirmation). "Never lost" as a number. Target zero, and it alerts.
2. **First-attempt acceptance rate per ASP**, rejections by rule (E104/BT-48 …). Each rejection
   is a customer fixing an invoice by hand, and the top rules show which checks to run before the ASP sees it.

Duplicate submissions must always be zero, so that's an alert, not a metric.
