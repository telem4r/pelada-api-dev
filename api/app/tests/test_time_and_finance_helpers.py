from datetime import timezone

from app.core.time import utc_now, utc_today
from app.services.finance_summary_service import normalize_entry_type, amount_to_cents, cents_to_amount


def test_utc_now_is_timezone_aware():
    assert utc_now().tzinfo == timezone.utc


def test_utc_today_returns_date():
    assert hasattr(utc_today(), "year")


def test_finance_type_normalization():
    assert normalize_entry_type("mensalidade") == "monthly"
    assert normalize_entry_type("valor_local") == "venue"
    assert normalize_entry_type("outra_despesa") == "extra_expense"


def test_amount_conversion_round_trip():
    assert amount_to_cents(10.55) == 1055
    assert cents_to_amount(1055) == 10.55
