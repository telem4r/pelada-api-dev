from datetime import date, timedelta

from app.finance_routes import _display_status_for_entry


class DummyEntry:
    def __init__(self, status='pending', due_date=None):
        self.status = status
        self.due_date = due_date


def test_display_status_overdue_for_past_due_pending():
    entry = DummyEntry(status='pending', due_date=date.today() - timedelta(days=1))
    assert _display_status_for_entry(entry) == 'overdue'


def test_display_status_paid_preserved():
    entry = DummyEntry(status='paid', due_date=date.today() - timedelta(days=10))
    assert _display_status_for_entry(entry) == 'paid'
