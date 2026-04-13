from __future__ import annotations

from .common import *

# =====================================================
# USER
# =====================================================

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)

    # Basic
    name = Column(String(120), nullable=True)
    email = Column(String(255), nullable=True, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    privacy_accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Profile fields
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    birth_date = Column(Date, nullable=True)
    favorite_team = Column(String(120), nullable=True)

    birth_country = Column(String(100), nullable=True)
    birth_state = Column(String(100), nullable=True)
    birth_city = Column(String(120), nullable=True)

    current_country = Column(String(100), nullable=True)
    current_state = Column(String(100), nullable=True)
    current_city = Column(String(120), nullable=True)

    position = Column(String(80), nullable=True)
    preferred_foot = Column(String(20), nullable=True)
    language = Column(String(10), nullable=True)

    # Refresh tokens
    refresh_token = Column(String(255), nullable=True)
    refresh_token_hash = Column(String(255), nullable=True)
    refresh_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    teams = relationship("Team", back_populates="owner", cascade="all, delete-orphan")
    players = relationship("Player", back_populates="owner", cascade="all, delete-orphan")
    groups_owned = relationship("Group", back_populates="owner", cascade="all, delete-orphan")
    group_memberships = relationship("GroupMember", back_populates="user", cascade="all, delete-orphan")
