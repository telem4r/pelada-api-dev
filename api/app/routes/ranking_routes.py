
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.player_group_stats import PlayerGroupStats

router = APIRouter(prefix="/groups/{group_id}/ranking")

@router.get("")
def get_ranking(group_id: str, db: Session = Depends(get_db)):
    players = db.query(PlayerGroupStats)        .filter(PlayerGroupStats.group_id == group_id)        .order_by(PlayerGroupStats.score.desc())        .all()

    return [
        {
            "position": idx + 1,
            "player_id": str(p.player_id),
            "presence": p.presence_count,
            "wins": p.wins_count,
            "fair_play": p.fair_play_avg,
            "score": p.score
        }
        for idx, p in enumerate(players)
    ]
