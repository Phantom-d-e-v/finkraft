from einvoice.models import Invoice


class DuplicateInvoice(Exception):
    def __init__(self, seller_id: str, invoice_number: str):
        super().__init__(f"seller {seller_id} already has invoice {invoice_number}")
        self.seller_id = seller_id
        self.invoice_number = invoice_number


def _key(seller_id: str, invoice_number: str) -> tuple[str, str]:
    # Bulk uploads (CSV/Excel) often carry stray whitespace around the number.
    return seller_id, invoice_number.strip()


class InMemoryStore:
    """Invoices keyed by id, unique per (seller_id, invoice_number).

    shortcut: check-then-insert is only safe in one thread; a real database needs
    a unique constraint on (seller_id, invoice_number) to make it atomic.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Invoice] = {}
        self._by_number: dict[tuple[str, str], str] = {}
        self._by_submission: dict[tuple[str, str], str] = {}

    def add(self, invoice: Invoice) -> None:
        key = _key(invoice.seller_id, invoice.invoice_number)
        if key in self._by_number:
            raise DuplicateInvoice(*key)
        self._by_number[key] = invoice.id
        self._by_id[invoice.id] = invoice

    def get(self, invoice_id: str) -> Invoice:
        return self._by_id[invoice_id]

    def link_submission(self, invoice: Invoice) -> None:
        """Index the invoice by the ASP's id so status updates can find it."""
        self._by_submission[(invoice.asp_id, invoice.submission_id)] = invoice.id

    def by_submission(self, asp_id: str, submission_id: str) -> Invoice | None:
        invoice_id = self._by_submission.get((asp_id, submission_id))
        return self._by_id[invoice_id] if invoice_id else None

    def in_state(self, *states) -> list[Invoice]:
        return [inv for inv in self._by_id.values() if inv.state in states]
