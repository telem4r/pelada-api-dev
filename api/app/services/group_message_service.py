
from datetime import datetime

messages = []

def list_messages(group_id):
    return messages

def create_message(group_id, user_id, message):
    msg = {
        "id": str(len(messages)+1),
        "user_id": user_id,
        "message": message,
        "created_at": datetime.utcnow().isoformat()
    }
    messages.append(msg)
    return msg
