from app.routes.stats_routes import _leaderboard_sort_key, _performance_tier, _ranking_points


def test_ranking_points_reward_performance():
    player = {"goals": 4, "mvp": 1, "wins": 3, "assists": 2, "games": 6, "win_balance": 2}
    assert _ranking_points(player) == 39


def test_leaderboard_sort_key_prefers_highest_points():
    a = {"name": "A", "goals": 3, "mvp": 1, "wins": 2, "assists": 0, "games": 5, "win_balance": 1}
    b = {"name": "B", "goals": 1, "mvp": 0, "wins": 1, "assists": 0, "games": 3, "win_balance": 0}
    ordered = sorted([b, a], key=_leaderboard_sort_key)
    assert ordered[0]["name"] == "A"


def test_performance_tier_progression():
    assert _performance_tier({"games": 2, "wins": 0, "goals": 0, "mvp": 0, "assists": 0, "win_balance": 0}) == "em evolução"
    assert _performance_tier({"games": 4, "wins": 1, "goals": 0, "mvp": 0, "assists": 0, "win_balance": 0}) == "ativo"
    assert _performance_tier({"games": 7, "wins": 3, "goals": 4, "mvp": 1, "assists": 0, "win_balance": 1}) == "destaque"
    assert _performance_tier({"games": 12, "wins": 8, "goals": 5, "mvp": 2, "assists": 1, "win_balance": 5}) == "elite"
