from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship, synonym

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid4())


class GroupRole(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class JoinStatus(str, Enum):
    pending = "pending"
    active = "active"
    rejected = "rejected"


class MembershipStatus(str, Enum):
    pending = "pending"
    active = "active"
    rejected = "rejected"
    removed = "removed"


class MatchStatus(str, Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    finished = "finished"
    cancelled = "cancelled"
    canceled = "canceled"


class ParticipantStatus(str, Enum):
    confirmed = "confirmed"
    waitlist = "waitlist"
    rejected = "rejected"


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
