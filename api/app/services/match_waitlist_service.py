from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import MatchParticipant, ParticipantStatus
from app.repositories.matches import capacity_ok, total_confirmed_count
from app.services.match_presence_service import build_presence, _confirmed_position_counts, _position_capacity_ok, _lock_match_row


def promote_waitlist_entries(
    db: Session,
    *,
    match,
    current_user_id: int,
    limit: int,
) -> dict:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="count deve ser maior que zero")
    match = _lock_match_row(db, match.id)
    promoted = 0
    while promoted < limit and capacity_ok(match, total_confirmed_count(db, match.id)):
        counts = _confirmed_position_counts(db, match.id)
        queue = (
            db.query(MatchParticipant)
            .filter(MatchParticipant.match_id == match.id)
            .filter(MatchParticipant.status == ParticipantStatus.waitlist.value)
            .filter(MatchParticipant.requires_approval.is_(False))
            .order_by(
                MatchParticipant.waitlist_tier.asc(),
                MatchParticipant.queue_position.asc().nullslast(),
                MatchParticipant.id.asc(),
            )
            .all()
        )
        candidate = None
        for part in queue:
            if _position_capacity_ok(match, counts, getattr(part, 'position', None)):
                candidate = part
                break
        if candidate is None:
            break
        candidate.status = ParticipantStatus.confirmed.value
        candidate.queue_position = None
        db.add(candidate)
        db.flush()
        promoted += 1
    db.commit()
    return build_presence(db, match=match, current_user_id=current_user_id)
