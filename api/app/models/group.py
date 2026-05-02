from __future__ import annotations

from .common import *

# =====================================================
# GROUP
# =====================================================

class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    owner_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    currency = Column(String(10), nullable=False, default="BRL")
    avatar_url = Column(String(500), nullable=True)

    country = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    city = Column(String(120), nullable=True)

    modality = Column(String(50), nullable=True)
    group_type = Column(String(50), nullable=False, default="avulso")
    gender_type = Column(String(50), nullable=True)

    payment_method = Column(String(50), nullable=True)
    payment_key = Column(String(255), nullable=True)

    venue_cost = Column(Float, nullable=True)
    per_person_cost = Column(Float, nullable=True)
    monthly_cost = Column(Float, nullable=True)
    single_cost = Column(Float, nullable=True)

    single_waitlist_release_days = Column(Integer, nullable=False, default=0)
    payment_due_day = Column(Integer, nullable=True)

    fine_enabled = Column(Boolean, nullable=False, default=False)
    fine_amount = Column(Float, nullable=True)
    fine_reason = Column(String(255), nullable=True)

    is_public = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    owner = relationship("User", back_populates="groups_owned")
    members = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")
