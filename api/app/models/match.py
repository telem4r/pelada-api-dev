from __future__ import annotations

from .common import *

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
    line_slots = Column(Integer, nullable=False, default=0)
    goalkeeper_slots = Column(Integer, nullable=False, default=0)
    modality = Column(String(50), nullable=True)
    gender_type = Column(String(30), nullable=True)

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
