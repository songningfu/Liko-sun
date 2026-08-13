from core.admin_auth import AdminConfig, AdminSessionManager, hash_password, verify_password


def test_password_hash_and_signed_session_round_trip() -> None:
    encoded = hash_password("correct horse battery staple", salt=b"fixed-test-salt-12")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong password", encoded) is False

    config = AdminConfig(
        username="admin",
        password_hash=encoded,
        session_secret="s" * 32,
        cookie_secure=False,
        cookie_path="/",
        liko_api_base_url="http://127.0.0.1:8001",
        liko_admin_token="test-token",
        fastgpt_admin_url="http://127.0.0.1:3100",
    )
    sessions = AdminSessionManager(config)
    assert sessions.authenticate("admin", "correct horse battery staple") is True
    assert sessions.authenticate("admin", "wrong password") is False
    token, csrf = sessions.issue()
    payload = sessions.read(token)
    assert payload is not None
    assert payload["sub"] == "admin"
    assert payload["csrf"] == csrf
    assert sessions.read(token + "tampered") is None


def test_admin_configuration_fails_closed_without_secrets() -> None:
    config = AdminConfig(
        username="admin",
        password_hash="",
        session_secret="",
        cookie_secure=False,
        cookie_path="/admin",
        liko_api_base_url="http://127.0.0.1:8001",
        liko_admin_token="",
        fastgpt_admin_url="",
    )
    assert config.configured is False
    assert AdminSessionManager(config).authenticate("admin", "anything") is False
