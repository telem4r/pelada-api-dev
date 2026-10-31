"""Shared price selection for every match-finance entry point."""
from decimal import Decimal


def is_hybrid(group: dict) -> bool:
    return str(group.get('group_type') or '').strip().lower() in {'hybrid', 'hibrido', 'híbrido'}


def match_charge_amount(group: dict) -> float:
    kind = str(group.get('group_type') or '').strip().lower()
    if is_hybrid(group):
        return float(group.get('single_cost') or 0)
    if kind == 'avulso':
        return float(group.get('per_person_cost') or 0)
    return 0.0


def same_money(left: float, right: float) -> bool:
    # Amounts originate in a float-based legacy schema; tolerate binary float noise only.
    return abs(Decimal(str(left)) - Decimal(str(right))) < Decimal('0.00000001')
