from app.core.config import settings


def test_settings_have_app_version():
    assert isinstance(settings.app_version, str)
    assert settings.app_version.strip()


def test_main_exposes_version_metadata():
    from app.main import root, health

    root_payload = root()
    assert root_payload["version"] == settings.app_version
    health_payload = health()
    assert health_payload["version"] == settings.app_version


def test_main_has_db_error_handlers_registered():
    from sqlalchemy.exc import DBAPIError, OperationalError
    from app.main import app

    assert OperationalError in app.exception_handlers
    assert DBAPIError in app.exception_handlers
