import pytest

from einvoice.models import Invoice
from einvoice.store import DuplicateInvoice, InMemoryStore


def inv(id, seller, number):
    return Invoice(id=id, seller_id=seller, invoice_number=number, fields={})


def test_duplicate_number_for_same_seller_is_rejected():
    store = InMemoryStore()
    store.add(inv("1", "seller-1", "INV-001"))
    with pytest.raises(DuplicateInvoice):
        store.add(inv("2", "seller-1", "INV-001"))


def test_whitespace_does_not_hide_a_duplicate():
    store = InMemoryStore()
    store.add(inv("1", "seller-1", "INV-001"))
    with pytest.raises(DuplicateInvoice):
        store.add(inv("2", "seller-1", " INV-001 "))


def test_same_number_for_different_sellers_is_allowed():
    store = InMemoryStore()
    store.add(inv("1", "seller-1", "INV-001"))
    store.add(inv("2", "seller-2", "INV-001"))
    assert store.get("2").seller_id == "seller-2"


def test_rejected_duplicate_does_not_overwrite_the_original():
    store = InMemoryStore()
    original = inv("1", "seller-1", "INV-001")
    store.add(original)
    with pytest.raises(DuplicateInvoice):
        store.add(inv("1", "seller-1", "INV-001"))
    assert store.get("1") is original


def test_lookup_by_asp_submission_id():
    store = InMemoryStore()
    invoice = inv("1", "seller-1", "INV-001")
    store.add(invoice)
    invoice.asp_id, invoice.submission_id = "asp_a", "A-0001"
    store.link_submission(invoice)
    assert store.by_submission("asp_a", "A-0001") is invoice
    assert store.by_submission("asp_b", "A-0001") is None
