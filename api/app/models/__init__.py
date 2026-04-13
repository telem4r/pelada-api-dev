"""ORM models - BoraFut (UUID-native schema)

Exports all model classes with lazy loading to avoid circular imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Dict, Tuple

_EXPORTS: Dict[str, Tuple[str, str]] = {
    # common
    "GroupRole": ("app.models.common", "GroupRole"),
    "JoinStatus": ("app.models.common", "JoinStatus"),
    "MembershipStatus": ("app.models.common", "MembershipStatus"),
    "MatchStatus": ("app.models.common", "MatchStatus"),
    "ParticipantStatus": ("app.models.common", "ParticipantStatus"),
    "TimestampMixin": ("app.models.common", "TimestampMixin"),
    "utcnow": ("app.models.common", "utcnow"),
    "new_uuid": ("app.models.common", "new_uuid"),
    # core entities
    "User": ("app.models.user", "User"),
    "Team": ("app.models.team", "Team"),
    "Player": ("app.models.player", "Player"),
    "Group": ("app.models.group", "Group"),
    "GroupMember": ("app.models.group_member", "GroupMember"),
    "GroupJoinRequest": ("app.models.group_join", "GroupJoinRequest"),
}

__all__ = sorted(_EXPORTS.keys())


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'app.models' has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
