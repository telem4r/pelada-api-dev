
from sqlalchemy import Column, DateTime, Text, Boolean, String, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
import uuid
from app.db import Base

class GroupMessage(Base):
    __tablename__ = "group_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    content = Column(Text, nullable=False)
    type = Column(String, default="message")
    is_pinned = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now())
