def test_service_modules_import():
    import app.services.auth_service  # noqa: F401
    import app.services.communication_service  # noqa: F401
    import app.services.finance_entries_service  # noqa: F401
    import app.services.finance_summary_service  # noqa: F401
    import app.services.match_guest_service  # noqa: F401
    import app.services.match_presence_service  # noqa: F401
    import app.services.match_waitlist_service  # noqa: F401
    import app.services.membership_service  # noqa: F401
    import app.services.profile_service  # noqa: F401
    import app.services.social_service  # noqa: F401


def test_route_modules_import():
    import app.auth_routes  # noqa: F401
    import app.communication_routes  # noqa: F401
    import app.finance_routes  # noqa: F401
    import app.groups_routes  # noqa: F401
    import app.health_routes  # noqa: F401
    import app.matches_routes  # noqa: F401
    import app.players_routes  # noqa: F401
    import app.profile_routes  # noqa: F401
    import app.social_routes  # noqa: F401
    import app.teams_routes  # noqa: F401
