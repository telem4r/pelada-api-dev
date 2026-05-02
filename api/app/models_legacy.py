from __future__ import annotations

"""
SQLAlchemy models - BoraFut

Baseado na última versão estável (que logava),
com ajustes mínimos para alinhar com o DB atual (groups UUID + group_members user_id)
e com migrations 0023/0024 (fine_*).
"""

from datetime import datetime
from enum import Enum
import uuid

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, synonym

from app.db import Base


# =====================================================
# ENUMS
# =====================================================

class GroupRole(str, Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class JoinStatus(str, Enum):
    pending = "pending"
    active = "active"
    rejected = "rejected"


class MatchStatus(str, Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    finished = "finished"
    cancelled = "cancelled"
    canceled = "canceled"  # compat legado


class ParticipantStatus(str, Enum):
    confirmed = "confirmed"
    waitlist = "waitlist"
    rejected = "rejected"


# =====================================================
# MIXINS
# =====================================================

class TimestampMixin:
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# =====================================================
# USER
# =====================================================

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # Basic
    name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=True, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    avatar_url = Column(String(500), nullable=True)

    # Profile fields (0022)
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
    matches = relationship("Match", back_populates="owner", cascade="all, delete-orphan")
    groups_owned = relationship("Group", back_populates="owner", cascade="all, delete-orphan")

    payments_owned = relationship(
        "Payment",
        foreign_keys="Payment.owner_id",
        back_populates="owner"
    )

    payments_confirmed = relationship(
        "Payment",
        foreign_keys="Payment.confirmed_by_user_id",
        back_populates="confirmed_by"
    )

    # ✅ DB atual: group_members.user_id
    group_memberships = relationship(
        "GroupMember",
        back_populates="user",
        cascade="all, delete-orphan"
    )


# =====================================================
# TEAM
# =====================================================

class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)

    owner = relationship("User", back_populates="teams")
    players = relationship("Player", back_populates="team")

    matches_home = relationship(
        "Match",
        foreign_keys="Match.home_team_id",
        back_populates="home_team"
    )

    matches_away = relationship(
        "Match",
        foreign_keys="Match.away_team_id",
        back_populates="away_team"
    )


# =====================================================
# PLAYER
# =====================================================

class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Compat legado: vários fluxos ainda referenciam player.user_id.
    # Mantemos um alias ORM para o campo canônico owner_id sem mudar o schema real.
    user_id = synonym("owner_id")
    name = Column(String(120), nullable=False)

    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)

    position = Column(String(80), nullable=True)
    preferred_foot = Column(String(20), nullable=True)
    rating = Column(Integer, nullable=False, default=0)

    owner = relationship("User", back_populates="players")
    team = relationship("Team", back_populates="players")

    match_participations = relationship(
        "MatchParticipant",
        back_populates="player",
        cascade="all, delete-orphan"
    )

    group_memberships = relationship("GroupMember", back_populates="player")

    payments = relationship("Payment", back_populates="player")
    achievements = relationship("PlayerAchievement", back_populates="player", cascade="all, delete-orphan")
    sports_profile = relationship("PlayerProfile", back_populates="player", uselist=False, cascade="all, delete-orphan")


# =====================================================
# GROUP
# =====================================================

class Group(Base, TimestampMixin):
    __tablename__ = "groups"

    # ✅ DB atual: varchar(36)
    id = Column(String(36), primary_key=True, index=True, default=lambda: str(uuid.uuid4()))

    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(120), nullable=False)
    currency = Column(String(10), nullable=False, default="BRL")
    avatar_url = Column(String(500), nullable=True)

    country = Column(String(100), nullable=False)
    state = Column(String(100), nullable=False)
    city = Column(String(120), nullable=False)

    modality = Column(String(50), nullable=False)
    group_type = Column(String(50), nullable=False)
    gender_type = Column(String(50), nullable=False)

    payment_method = Column(String(50), nullable=True)
    payment_key = Column(String(255), nullable=True)

    venue_cost = Column(Float, nullable=True)
    per_person_cost = Column(Float, nullable=True)
    monthly_cost = Column(Float, nullable=True)
    single_cost = Column(Float, nullable=True)

    # Grupo híbrido: dias antes da partida em que avulsos podem sair da espera
    # e serem promovidos automaticamente para a presença. 0 = apenas manual.
    single_waitlist_release_days = Column(Integer, nullable=False, default=0)

    # Financeiro (Sprint 4)
    # Dia limite de pagamento para mensalistas (1-31). Pode ser ajustado no settings do grupo.
    payment_due_day = Column(Integer, nullable=True)

    # ✅ migrations 0023/0024 garantem essas colunas
    fine_enabled = Column(Boolean, nullable=False, default=False)
    fine_amount = Column(Float, nullable=True)
    fine_reason = Column(String(255), nullable=True)

    # ✅ DB atual tem is_public NOT NULL
    is_public = Column(Boolean, nullable=False, default=False)

    owner = relationship("User", back_populates="groups_owned")

    members = relationship(
        "GroupMember",
        back_populates="group",
        cascade="all, delete-orphan"
    )

    matches = relationship("Match", back_populates="group")
    payments = relationship("Payment", back_populates="group")
    player_achievements = relationship(
        "PlayerAchievement",
        back_populates="group",
        cascade="all, delete-orphan"
    )


# =====================================================
# GROUP MEMBER
# =====================================================

class GroupMember(Base, TimestampMixin):
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True)

    # ✅ groups.id agora é string uuid
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)

    # ✅ DB atual: user_id
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # ✅ canonical membership: player_id (0025)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    role = Column(String(20), nullable=False, default=GroupRole.member.value)
    status = Column(String(20), nullable=False, default=JoinStatus.pending.value)

    # Plano de pagamento dentro do grupo: single (avulso) | monthly (mensalista)
    billing_type = Column(String(20), nullable=False, default="single")

    # Avaliação de habilidade (1-5) para sorteio/balanceamento (visível apenas para ADM/Owner)
    skill_rating = Column(Integer, nullable=False, default=3)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member_user"),
        UniqueConstraint("group_id", "player_id", name="uq_group_member_player"),
    )

    group = relationship("Group", back_populates="members")
    user = relationship("User", back_populates="group_memberships")
    player = relationship("Player", back_populates="group_memberships")


# =====================================================
# GROUP FINANCIAL ENTRIES (controle interno)
# =====================================================


class GroupFinancialEntry(Base, TimestampMixin):
    __tablename__ = "group_financial_entries"

    id = Column(Integer, primary_key=True)

    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)

    # para lançamentos automáticos (mensalidade)
    period_year = Column(Integer, nullable=True, index=True)
    period_month = Column(Integer, nullable=True, index=True)

    # monthly | single | fine | manual | venue
    entry_type = Column(String(20), nullable=False, default="manual")

    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="BRL")

    # pending | paid
    status = Column(String(20), nullable=False, default="pending")
    due_date = Column(Date, nullable=True)

    description = Column(Text, nullable=True)

    paid = Column(Boolean, nullable=False, default=False)

    # Falta (no-show) - marcado pelo ADM/Owner
    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    paid_amount_cents = Column(Integer, nullable=False, default=0)
    payment_method = Column(String(30), nullable=True)
    notes = Column(Text, nullable=True)
    confirmed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        # 1 mensalidade por user por mês
        UniqueConstraint(
            "group_id",
            "user_id",
            "entry_type",
            "period_year",
            "period_month",
            name="uq_fin_entry_monthly_user_period",
        ),
        # 1 cobrança single/fine por user por partida (quando match_id existe)
        UniqueConstraint(
            "group_id",
            "user_id",
            "entry_type",
            "match_id",
            name="uq_fin_entry_user_match_type",
        ),
        # 1 despesa de quadra por partida (user_id NULL)
        UniqueConstraint(
            "group_id",
            "entry_type",
            "match_id",
            name="uq_fin_entry_group_match_type",
        ),
    )

    group = relationship("Group")
    user = relationship("User", foreign_keys=[user_id])
    match = relationship("Match")
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id])


# =====================================================
# GROUP JOIN REQUEST (pedido para entrar no grupo)
# =====================================================


class GroupJoinRequest(Base, TimestampMixin):
    __tablename__ = "group_join_requests"

    id = Column(Integer, primary_key=True)

    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    # pending | active (aprovado) | rejected
    status = Column(String(20), nullable=False, default=JoinStatus.pending.value)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", name="uq_group_join_req_group_player"),
    )

    group = relationship("Group")
    user = relationship("User")
    player = relationship("Player")


# =====================================================
# MATCH
# =====================================================

class Match(Base, TimestampMixin):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Compatibilidade com schema legado em produção
    date_time = Column(DateTime(timezone=True), nullable=False)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=True)

    home_team_id = Column(Integer, ForeignKey("teams.id", ondelete="RESTRICT"), nullable=True, index=True)
    away_team_id = Column(Integer, ForeignKey("teams.id", ondelete="RESTRICT"), nullable=True, index=True)

    # ✅ groups.id agora é string uuid
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)

    title = Column(String(120), nullable=True)
    status = Column(String(20), nullable=False, default=MatchStatus.scheduled.value)
    mvp_player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    mvp_guest_id = Column(Integer, ForeignKey("match_guests.id", ondelete="SET NULL"), nullable=True, index=True)

    player_limit = Column(Integer, nullable=False, default=0)

    # Grupo híbrido: dias antes da partida em que avulsos podem sair da waitlist automaticamente (0 = manual)
    single_waitlist_release_days = Column(Integer, nullable=False, default=0)
    value_per_player = Column(Float, nullable=False, default=0.0)
    price_cents = Column(Integer, nullable=True)
    currency = Column(String(10), nullable=True)

    city = Column(String(120), nullable=True)
    venue_name = Column(String(120), nullable=False)
    location_name = Column(String(255), nullable=True)
    location_lat = Column(Float, nullable=True)
    location_lng = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)

    is_public = Column(Boolean, nullable=False, default=False)

    payment_method = Column(String(20), nullable=True)
    payment_key = Column(String(255), nullable=True)

    owner = relationship("User", back_populates="matches")
    home_team = relationship("Team", foreign_keys=[home_team_id], back_populates="matches_home")
    away_team = relationship("Team", foreign_keys=[away_team_id], back_populates="matches_away")
    group = relationship("Group", back_populates="matches")

    participants = relationship(
        "MatchParticipant",
        back_populates="match",
        cascade="all, delete-orphan"
    )

    draw_teams = relationship(
        "MatchDrawTeam",
        back_populates="match",
        cascade="all, delete-orphan"
    )

    payments = relationship("Payment", back_populates="match")
    events = relationship("MatchEvent", back_populates="match", cascade="all, delete-orphan")
    guests = relationship(
        "MatchGuestPlayer",
        foreign_keys="MatchGuestPlayer.match_id",
        back_populates="match",
        cascade="all, delete-orphan",
    )
    mvp_player = relationship("Player", foreign_keys=[mvp_player_id])
    mvp_guest = relationship("MatchGuestPlayer", foreign_keys=[mvp_guest_id])


# =====================================================
# MATCH PARTICIPANT
# =====================================================

class MatchParticipant(Base, TimestampMixin):
    __tablename__ = "match_participants"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(20), nullable=False, default=ParticipantStatus.confirmed.value)
    arrived = Column(Boolean, nullable=False, default=False)
    paid = Column(Boolean, nullable=False, default=False)
    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)

    # ✅ Fase 2: ordenação e regras de lista
    # - queue_position: ordem de chegada por match (principalmente na waitlist)
    # - waitlist_tier: 0 = normal, 1 = fim (inadimplentes)
    # - requires_approval: se True, não pode ser promovido automaticamente
    queue_position = Column(Integer, nullable=True)
    waitlist_tier = Column(Integer, nullable=False, default=0)
    requires_approval = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_match_participant"),
    )

    match = relationship("Match", back_populates="participants")
    player = relationship("Player", back_populates="match_participations")


# =====================================================
# MATCH GUEST PLAYER (sem app)
# =====================================================

class MatchGuestPlayer(Base, TimestampMixin):
    __tablename__ = "match_guests"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(120), nullable=False)
    position = Column(String(50), nullable=True)
    skill_rating = Column(Integer, nullable=False, default=3)

    status = Column(String(20), nullable=False, default=ParticipantStatus.confirmed.value)
    arrived = Column(Boolean, nullable=False, default=False)

    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)

    match = relationship(
        "Match",
        foreign_keys=[match_id],
        back_populates="guests",
    )


# =====================================================
# MATCH JOIN REQUEST (jogadores fora do grupo)
# =====================================================

class MatchJoinRequest(Base, TimestampMixin):
    __tablename__ = "match_join_requests"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    # Redundante (facilita queries por grupo e reforça hierarquia /groups/{group_id}/matches/...)
    group_id = Column(String(36), nullable=True, index=True)

    message = Column(Text, nullable=True)

    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), nullable=False, default=JoinStatus.pending.value)

    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_match_join_request_player"),
    )

    match = relationship("Match")
    player = relationship("Player")


# =====================================================
# MATCH DRAW TEAM
# =====================================================

class MatchDrawTeam(Base):
    __tablename__ = "match_draw_teams"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    team_number = Column(Integer, nullable=False)

    players = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("match_id", "team_number", name="uq_match_team_number"),
    )

    match = relationship("Match", back_populates="draw_teams")


# =====================================================
# MATCH EVENT
# =====================================================

class MatchEvent(Base, TimestampMixin):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    team_number = Column(Integer, nullable=False)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    guest_id = Column(Integer, ForeignKey("match_guests.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(30), nullable=False, default="goal")
    minute = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="events")
    player = relationship("Player")
    guest = relationship("MatchGuestPlayer")


# =====================================================
# PAYMENT
# =====================================================

class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)

    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # ✅ groups.id agora é string uuid
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)

    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)

    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="BRL")
    status = Column(String(20), nullable=False, default="pending")

    kind = Column(String(30), nullable=False, default="group")
    description = Column(Text, nullable=True)

    paid = Column(Boolean, nullable=False, default=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    confirmed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    group = relationship("Group", back_populates="payments")
    match = relationship("Match", back_populates="payments")

    owner = relationship("User", foreign_keys=[owner_id], back_populates="payments_owned")
    player = relationship("Player", back_populates="payments")
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id], back_populates="payments_confirmed")


# =====================================================
# PLAYER ACHIEVEMENTS
# =====================================================

class PlayerAchievement(Base, TimestampMixin):
    __tablename__ = "player_achievements"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(80), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    unlocked_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    payload = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", "code", name="uq_player_achievement_code"),
    )

    group = relationship("Group", back_populates="player_achievements")
    player = relationship("Player", back_populates="achievements")


# =====================================================
# FASE 11 - SOCIAL / GEOLOCATION
# =====================================================

class PlayerProfile(Base, TimestampMixin):
    __tablename__ = "player_profiles"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    bio = Column(Text, nullable=True)
    city = Column(String(120), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    main_position = Column(String(80), nullable=True)
    skill_level = Column(Integer, nullable=False, default=3)

    player = relationship("Player", back_populates="sports_profile")


class PlayerNetwork(Base, TimestampMixin):
    __tablename__ = "player_network"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    connected_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    shared_matches_count = Column(Integer, nullable=False, default=0)
    invited_count = Column(Integer, nullable=False, default=0)
    last_played_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("player_id", "connected_player_id", name="uq_player_network_pair"),
    )

    player = relationship("Player", foreign_keys=[player_id])
    connected_player = relationship("Player", foreign_keys=[connected_player_id])


# =====================================================
# FASE 10 - COMMUNICATION / NOTIFICATIONS
# =====================================================

class GroupAnnouncement(Base, TimestampMixin):
    __tablename__ = "group_announcements"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(140), nullable=False)
    message = Column(Text, nullable=False)
    is_pinned = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    group = relationship("Group")
    author = relationship("User")


class MatchComment(Base, TimestampMixin):
    __tablename__ = "match_comments"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    message = Column(Text, nullable=False)

    group = relationship("Group")
    match = relationship("Match")
    user = relationship("User")
    player = relationship("Player")


class NotificationSetting(Base, TimestampMixin):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    matches_enabled = Column(Boolean, nullable=False, default=True)
    finance_enabled = Column(Boolean, nullable=False, default=True)
    announcements_enabled = Column(Boolean, nullable=False, default=True)
    comments_enabled = Column(Boolean, nullable=False, default=True)
    invites_enabled = Column(Boolean, nullable=False, default=True)
    fines_enabled = Column(Boolean, nullable=False, default=True)

    user = relationship("User")


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(40), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    message = Column(Text, nullable=False)
    read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    external_key = Column(String(200), nullable=True, unique=True, index=True)
    payload = Column(JSONB, nullable=True)

    user = relationship("User")


class GroupInvite(Base, TimestampMixin):
    __tablename__ = "group_invites"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    invited_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    username = Column(String(120), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "invited_user_id", "status", name="uq_group_invite_group_user_status"),
    )

    group = relationship("Group")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    invited_user = relationship("User", foreign_keys=[invited_user_id])


class GroupActivityLog(Base):
    __tablename__ = "group_activity_log"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    activity_type = Column(String(40), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    description = Column(Text, nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    group = relationship("Group")
    actor_user = relationship("User", foreign_keys=[actor_user_id])
    actor_player = relationship("Player", foreign_keys=[actor_player_id])
    match = relationship("Match")
    target_user = relationship("User", foreign_keys=[target_user_id])
