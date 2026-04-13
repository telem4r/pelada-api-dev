from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.security import get_current_user  # ✅ retorna user_id (int)
from app.models import Team

router = APIRouter(prefix="/teams", tags=["teams"])


class TeamIn(BaseModel):
    name: str


class TeamOut(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[TeamOut])
def list_teams(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    return (
        db.query(Team)
        .filter(Team.owner_id == current_user_id)
        .order_by(Team.id.asc())
        .all()
    )


@router.post("", response_model=TeamOut, status_code=201)
def create_team(
    payload: TeamIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name não pode ser vazio")

    team = Team(owner_id=current_user_id, name=name)
    db.add(team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Conflito ao criar o time."
        )
    db.refresh(team)
    return team


@router.get("/{team_id}", response_model=TeamOut)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.owner_id == current_user_id)
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.put("/{team_id}", response_model=TeamOut)
def update_team(
    team_id: int,
    payload: TeamIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.owner_id == current_user_id)
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name não pode ser vazio")

    team.name = name
    db.add(team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Conflito ao atualizar o time."
        )
    db.refresh(team)
    return team


@router.delete("/{team_id}")
def delete_team(
    team_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    team = (
        db.query(Team)
        .filter(Team.id == team_id, Team.owner_id == current_user_id)
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    db.delete(team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Não é possível apagar este time porque existem matches associados."
        )
    return {"ok": True}
