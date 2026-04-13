
from sqlalchemy import Column, Integer, Float, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.db import Base

class PlayerGroupStats(Base):
    __tablename__ = "player_group_stats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)

    presence_count = Column(Integer, default=0)
    wins_count = Column(Integer, default=0)
    fair_play_avg = Column(Float)

    score = Column(Integer, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
