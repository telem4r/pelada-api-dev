from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class MonthlyDueConfig(BaseModel):
    due_day: Optional[int] = None
    payment_method: Optional[str] = None
    payment_key: Optional[str] = None


class ExtraExpenseCreate(BaseModel):
    description: str
    amount: float
    date: Optional[date] = None
