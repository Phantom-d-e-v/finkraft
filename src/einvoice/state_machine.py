"""Finkraft's own invoice lifecycle, independent of any ASP's status names."""

from enum import StrEnum


class InvoiceState(StrEnum):
    RECEIVED = "RECEIVED"                      # uploaded by the seller (C1)
    INVALID = "INVALID"                        # failed our PINT AE checks, never sent
    VALIDATED = "VALIDATED"                    # ready to submit
    SUBMITTING = "SUBMITTING"                  # request to the ASP is in flight
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"  # sent, but no answer (timeout)
    SUBMITTED = "SUBMITTED"                    # ASP (C2) has acknowledged it
    ACCEPTED = "ACCEPTED"                      # ASP validated it
    REJECTED = "REJECTED"                      # ASP rejected it
    DELIVERED = "DELIVERED"                    # buyer's ASP (C3) received it
    REPORTED = "REPORTED"                      # FTA (C5) has the tax data


S = InvoiceState

ALLOWED: dict[InvoiceState, frozenset[InvoiceState]] = {
    S.RECEIVED: frozenset({S.VALIDATED, S.INVALID}),
    S.INVALID: frozenset({S.RECEIVED}),  # seller corrects it and we re-validate
    S.VALIDATED: frozenset({S.SUBMITTING}),
    # Back to VALIDATED only when the ASP definitely did not take it (429, refused connection).
    S.SUBMITTING: frozenset({S.SUBMITTED, S.REJECTED, S.SUBMISSION_UNKNOWN, S.VALIDATED}),
    S.SUBMISSION_UNKNOWN: frozenset({S.SUBMITTING, S.SUBMITTED}),
    # Forward skips are allowed: ASP updates can be lost or arrive out of order.
    S.SUBMITTED: frozenset({S.ACCEPTED, S.REJECTED, S.DELIVERED, S.REPORTED}),
    S.ACCEPTED: frozenset({S.DELIVERED, S.REPORTED}),
    S.DELIVERED: frozenset({S.REPORTED}),
    S.REJECTED: frozenset(),
    S.REPORTED: frozenset(),
}

TERMINAL = frozenset(s for s, targets in ALLOWED.items() if not targets)

# How far an invoice has progressed after the ASP acknowledged it.
# ACCEPTED and REJECTED are siblings: one can never follow the other.
_PROGRESS = {S.SUBMITTED: 0, S.ACCEPTED: 1, S.REJECTED: 1, S.DELIVERED: 2, S.REPORTED: 3}


class InvalidTransition(Exception):
    def __init__(self, current: InvoiceState, target: InvoiceState):
        super().__init__(f"cannot move invoice from {current} to {target}")
        self.current = current
        self.target = target


def transition(current: InvoiceState, target: InvoiceState) -> InvoiceState:
    if target not in ALLOWED[current]:
        raise InvalidTransition(current, target)
    return target


def is_stale(current: InvoiceState, update: InvoiceState) -> bool:
    """True if an ASP status update is a repeat or older than what we already know.

    Stale updates are expected (late or duplicate webhooks) and are ignored.
    A conflicting update, e.g. REJECTED after ACCEPTED, is not stale and must
    go through transition() so it raises.
    """
    if current not in _PROGRESS or update not in _PROGRESS:
        return False
    if update == current:
        return True
    if S.REJECTED in (current, update):
        # REJECTED is on its own branch, so "lower progress" does not mean "older".
        return update == S.SUBMITTED
    return _PROGRESS[update] < _PROGRESS[current]
