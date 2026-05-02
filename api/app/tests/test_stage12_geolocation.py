from app.services.social_service import haversine_km


def describe_distance_label(value: float) -> str:
    return f"{round(value, 1):.1f} km"


def test_haversine_distance_is_zero_for_same_coordinates():
    assert haversine_km(39.0, -9.0, 39.0, -9.0) == 0


def test_distance_label_formatting():
    assert describe_distance_label(5) == "5.0 km"
    assert describe_distance_label(12.34) == "12.3 km"
