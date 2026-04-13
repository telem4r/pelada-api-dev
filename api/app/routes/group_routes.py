
from fastapi import APIRouter
from uuid import UUID
from services.group_ranking_service import get_group_ranking
from services.group_message_service import list_messages, create_message

router = APIRouter()

@router.get("/groups/{group_id}/ranking")
def ranking(group_id: UUID):
    return get_group_ranking(group_id)

@router.get("/groups/{group_id}/messages")
def get_messages(group_id: UUID):
    return list_messages(group_id)

@router.post("/groups/{group_id}/messages")
def post_message(group_id: UUID, payload: dict):
    return create_message(group_id, payload.get("user_id"), payload.get("message"))
