import pytest

from einvoice.state_machine import (
    ALLOWED,
    TERMINAL,
    InvalidTransition,
    InvoiceState as S,
    is_stale,
    transition,
)


def walk(*states):
    current = states[0]
    for target in states[1:]:
        current = transition(current, target)
    return current


def test_happy_path_reaches_reported():
    end = walk(S.RECEIVED, S.VALIDATED, S.SUBMITTING, S.SUBMITTED,
               S.ACCEPTED, S.DELIVERED, S.REPORTED)
    assert end == S.REPORTED


def test_timeout_then_retry_then_acknowledged():
    end = walk(S.VALIDATED, S.SUBMITTING, S.SUBMISSION_UNKNOWN, S.SUBMITTING, S.SUBMITTED)
    assert end == S.SUBMITTED


def test_timeout_resolved_by_lookup_without_resending():
    assert walk(S.SUBMITTING, S.SUBMISSION_UNKNOWN, S.SUBMITTED) == S.SUBMITTED


def test_invalid_invoice_can_be_corrected_and_revalidated():
    assert walk(S.RECEIVED, S.INVALID, S.RECEIVED, S.VALIDATED) == S.VALIDATED


def test_rate_limited_submission_goes_back_to_validated():
    assert walk(S.VALIDATED, S.SUBMITTING, S.VALIDATED) == S.VALIDATED


def test_asp_can_skip_ahead_when_updates_are_lost():
    assert transition(S.SUBMITTED, S.REPORTED) == S.REPORTED


@pytest.mark.parametrize("current, target", [
    (S.RECEIVED, S.SUBMITTING),         # must validate first
    (S.INVALID, S.SUBMITTING),          # never send an invalid invoice
    (S.VALIDATED, S.SUBMITTED),         # no acknowledgement without a request
    (S.SUBMISSION_UNKNOWN, S.VALIDATED),  # ASP may have it; can't treat as unsent
    (S.ACCEPTED, S.REJECTED),           # conflicting ASP outcome
    (S.DELIVERED, S.ACCEPTED),          # backwards
    (S.REPORTED, S.SUBMITTING),         # terminal
    (S.REJECTED, S.SUBMITTING),         # terminal
])
def test_invalid_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        transition(current, target)


def test_every_state_has_a_rule():
    assert set(ALLOWED) == set(S)
    assert TERMINAL == {S.REJECTED, S.REPORTED}


@pytest.mark.parametrize("current, update", [
    (S.ACCEPTED, S.ACCEPTED),    # duplicate webhook
    (S.DELIVERED, S.ACCEPTED),   # late webhook
    (S.REPORTED, S.DELIVERED),   # C3 confirmation after C5
    (S.REJECTED, S.SUBMITTED),
])
def test_stale_updates(current, update):
    assert is_stale(current, update)


@pytest.mark.parametrize("current, update", [
    (S.SUBMITTED, S.ACCEPTED),   # normal progress
    (S.ACCEPTED, S.REJECTED),    # conflict: must raise, not be ignored
    (S.DELIVERED, S.REJECTED),   # conflict
    (S.REJECTED, S.ACCEPTED),    # conflict
    (S.SUBMITTING, S.SUBMITTED),  # pre-ack states are never stale
])
def test_not_stale(current, update):
    assert not is_stale(current, update)
