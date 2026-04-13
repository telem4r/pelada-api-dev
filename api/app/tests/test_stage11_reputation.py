from app.routes.stats_routes import _reputation_label, _reputation_score


def test_reputation_score_rewards_attendance_and_penalizes_no_show():
    strong = {"games": 10, "attendance_rate": 90.0, "unjustified_absences": 0, "abandonments": 0}
    weak = {"games": 4, "attendance_rate": 40.0, "unjustified_absences": 2, "abandonments": 1}
    assert _reputation_score(strong) > _reputation_score(weak)


def test_reputation_label_ranges():
    assert _reputation_label(4.7, True) == "Excelente"
    assert _reputation_label(3.7, True) == "Confiável"
    assert _reputation_label(2.7, True) == "Regular"
    assert _reputation_label(1.8, True) == "Atenção"
    assert _reputation_label(0.0, False) == "Sem histórico"
