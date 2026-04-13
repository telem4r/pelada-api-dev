from app.matches_routes import _saved_draw_matches_current_state


class _Row:
    def __init__(self, team_number, players):
        self.team_number = team_number
        self.players = players


def test_saved_draw_state_requires_same_total_players_and_team_capacity():
    pool = [
        {"kind": "member", "player_id": 1},
        {"kind": "member", "player_id": 2},
        {"kind": "member", "player_id": 3},
        {"kind": "member", "player_id": 4},
    ]
    saved_rows = [
        _Row(1, [{"kind": "member", "player_id": 1}, {"kind": "member", "player_id": 2}, {"kind": "member", "player_id": 3}]),
        _Row(2, [{"kind": "member", "player_id": 4}]),
    ]

    assert _saved_draw_matches_current_state(saved_rows, pool, players_per_team=2) is False
