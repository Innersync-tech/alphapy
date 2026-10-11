from utils.rate_limit_policy import is_write_rate_limit_exempt


def test_webhook_paths_are_exempt() -> None:
    assert is_write_rate_limit_exempt("/webhooks/revoke-reflection") is True
    assert is_write_rate_limit_exempt("/webhooks/app-reflections/") is True
    assert is_write_rate_limit_exempt("/webhooks/revoke-reflection?x=1") is True


def test_service_revoke_path_is_exempt() -> None:
    assert is_write_rate_limit_exempt("/api/reflections/revoke") is True
    assert is_write_rate_limit_exempt("/api/reflections/revoke/") is True


def test_ordinary_writes_are_not_exempt() -> None:
    assert is_write_rate_limit_exempt("/api/dashboard/1/reminders") is False
    assert is_write_rate_limit_exempt("/api/hermit/growth-checkins") is False
