import json
import logging

from app.core.api_errors import with_request_id
from app.core.audit import audit_admin_action


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_with_request_id_merges_existing_details():
    payload = with_request_id({"path": "/groups"}, "req-123")
    assert payload["path"] == "/groups"
    assert payload["request_id"] == "req-123"


def test_audit_admin_action_logs_structured_payload():
    logger = logging.getLogger("test.audit")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    handler = _CaptureHandler()
    logger.addHandler(handler)

    audit_admin_action(
        logger,
        action="approve_group_join_request",
        actor_user_id=10,
        group_id="g1",
        target_request_id=5,
        outcome="success",
    )

    assert handler.messages
    payload = json.loads(handler.messages[-1])
    assert payload["event_type"] == "admin_audit"
    assert payload["action"] == "approve_group_join_request"
    assert payload["actor_user_id"] == 10
    assert payload["group_id"] == "g1"
    assert payload["target_request_id"] == 5
