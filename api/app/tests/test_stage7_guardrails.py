from pathlib import Path


def test_no_now_utc_in_active_code():
    app_root = Path(__file__).resolve().parents[1]
    for path in app_root.rglob('*.py'):
        if path.name == 'models_legacy.py' or '__pycache__' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        assert 'now_utc(' not in text, f'legacy helper leaked into {path}'


def test_no_datetime_utcnow_in_active_code():
    app_root = Path(__file__).resolve().parents[1]
    allowed = {'models_legacy.py'}
    for path in app_root.rglob('*.py'):
        if path.name in allowed or '__pycache__' in path.parts:
            continue
        text = path.read_text(encoding='utf-8')
        assert 'datetime.utcnow(' not in text, f'datetime.utcnow still active in {path}'
