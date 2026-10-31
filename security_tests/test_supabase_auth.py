from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
import json
import logging
from threading import Barrier
import time
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwk, jws, jwt
import pytest

from app.core import supabase_auth as auth


USER_ID = "00000000-0000-4000-8000-000000000001"
TEST_SECRET = "local-security-tests-only-not-a-production-secret"
ALGORITHMS = ["HS256", "ES256", "RS256"]


def _asymmetric_key(algorithm, kid):
    private = (ec.generate_private_key(ec.SECP256R1()) if algorithm == "ES256"
               else rsa.generate_private_key(public_exponent=65537, key_size=2048))
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_jwk = jwk.construct(public_pem, algorithm).to_dict()
    public_jwk.update(kid=kid, use="sig", key_ops=["verify"])
    return SimpleNamespace(private=private_pem, public=public_jwk)


@pytest.fixture(scope="session")
def keys():
    return {
        "ES256": _asymmetric_key("ES256", "ec-current"),
        "RS256": _asymmetric_key("RS256", "rsa-current"),
        "ec-next": _asymmetric_key("ES256", "ec-next"),
        "rsa-other": _asymmetric_key("RS256", "rsa-other"),
    }


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch, keys):
    # No production settings or network access are used by this suite.
    config = SimpleNamespace(
        supabase_url="https://security-tests.invalid",
        supabase_jwt_audience="authenticated",
        supabase_jwt_secret="",
        jwt_secret=TEST_SECRET,
        supabase_service_role_key="local-fake-service-role-api-key",
    )
    monkeypatch.setattr(auth, "settings", config)
    monkeypatch.setattr(auth, "_jwks_cache", None)
    monkeypatch.setattr(auth, "_jwks_cache_expires_at", 0.0)
    monkeypatch.setattr(auth, "_jwks_next_refresh_at", 0.0)
    monkeypatch.setattr(auth, "urlopen", Mock(side_effect=AssertionError("Network forbidden in tests")))
    download = Mock(return_value={"keys": [keys[a].public for a in ("ES256", "RS256")]})
    monkeypatch.setattr(auth, "_download_jwks", download)
    return SimpleNamespace(config=config, download=download)


def claims(**changes):
    payload = {
        "sub": USER_ID, "email": "test@example.invalid", "aud": "authenticated",
        "iss": "https://security-tests.invalid/auth/v1", "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    payload.update(changes)
    return payload


def signed(keys, algorithm="HS256", payload=None, **headers):
    key = TEST_SECRET if algorithm == "HS256" else keys[algorithm].private
    if algorithm != "HS256":
        headers.setdefault("kid", keys[algorithm].public["kid"])
    return jwt.encode(claims() if payload is None else payload, key, algorithm=algorithm, headers=headers)


def assert_denied(token):
    with pytest.raises(auth.SupabaseAuthError):
        auth._decode_token(token)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_valid_signature_preserves_identity(keys, algorithm):
    principal = auth.get_current_supabase_principal(signed(keys, algorithm))
    assert principal.user_id == USER_ID
    assert principal.email == "test@example.invalid"
    assert principal.raw_claims["aud"] == "authenticated"


@pytest.mark.parametrize("network_available", [False, True])
@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_original_forged_signature_regression(keys, isolated_auth, network_available, algorithm):
    if not network_available:
        isolated_auth.download.side_effect = OSError("Local simulated outage")
    if algorithm == "HS256":
        token = jwt.encode(claims(), "wrong-test-key", algorithm=algorithm)
    else:
        other = keys["ec-next" if algorithm == "ES256" else "rsa-other"]
        token = jwt.encode(claims(), other.private, algorithm=algorithm,
                           headers={"kid": keys[algorithm].public["kid"]})
    assert_denied(token)
    if algorithm == "HS256":
        isolated_auth.download.assert_not_called()


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "exp"])
def test_required_claims(keys, algorithm, claim):
    payload = claims()
    payload.pop(claim)
    assert_denied(signed(keys, algorithm, payload))


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("changes", [
    {"iss": "https://another-project.invalid/auth/v1"},
    {"iss": None}, {"aud": "another-audience"}, {"aud": []},
    {"aud": None}, {"aud": {"authenticated": True}},
    {"sub": ""}, {"sub": "   "}, {"sub": None}, {"sub": 7},
    {"exp": 1}, {"exp": None}, {"exp": "9999999999"},
    {"exp": True}, {"exp": float("inf")}, {"exp": float("nan")},
    {"nbf": 9999999999}, {"nbf": None}, {"iat": "invalid"},
    {"iat": None},
])
def test_invalid_claims_always_denied(keys, algorithm, changes):
    assert_denied(signed(keys, algorithm, claims(**changes)))


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_standard_audience_array_is_supported(keys, algorithm):
    assert auth._decode_token(signed(keys, algorithm, claims(aud=["authenticated", "other"])))


def test_expiry_boundary_is_denied_even_with_valid_signature(keys, monkeypatch):
    now = int(time.time())
    token = signed(keys, payload=claims(exp=now))
    monkeypatch.setattr(auth.time, "time", lambda: now)
    assert_denied(token)


def test_supabase_secret_override_and_legacy_compatibility(keys, isolated_auth):
    assert auth._decode_token(signed(keys))
    isolated_auth.config.supabase_jwt_secret = "explicit-supabase-test-secret"
    token = jwt.encode(claims(), isolated_auth.config.supabase_jwt_secret, algorithm="HS256")
    assert auth._decode_token(token)
    assert_denied(signed(keys))


def test_service_role_is_never_a_signing_secret(keys, isolated_auth):
    isolated_auth.config.jwt_secret = ""
    assert auth._get_jwt_secret() is None
    token = jwt.encode(claims(), isolated_auth.config.supabase_service_role_key, algorithm="HS256")
    with pytest.raises(auth.SupabaseAuthUnavailable):
        auth._decode_token(token)


@pytest.mark.parametrize("field", ["supabase_url", "supabase_jwt_audience"])
def test_missing_auth_configuration_never_grants_access(keys, isolated_auth, field):
    setattr(isolated_auth.config, field, "")
    with pytest.raises(auth.SupabaseAuthUnavailable):
        auth._decode_token(signed(keys))


def test_trailing_slash_configuration(keys, isolated_auth):
    isolated_auth.config.supabase_url += "/"
    assert auth._decode_token(signed(keys))


def _replace_header(token, header):
    parts = token.split(".")
    parts[0] = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    return ".".join(parts)


@pytest.mark.parametrize("header", [
    {"alg": "none"}, {"alg": "HS512"}, {"alg": "PS256"}, {"alg": "EdDSA"},
    {"alg": []}, {"alg": {}}, {},
    {"alg": "HS256", "crit": ["unimplemented"]},
    {"alg": "HS256", "b64": False},
    {"alg": "ES256"}, {"alg": "ES256", "kid": ""},
    {"alg": "RS256", "kid": []},
])
def test_unsupported_headers_rejected_without_key_download(keys, isolated_auth, header):
    assert_denied(_replace_header(signed(keys), header))
    isolated_auth.download.assert_not_called()


@pytest.mark.parametrize("token", ["", "bad", "a.b.c", "a..c", "..", "eyJhbGciOiJIUzI1NiJ9.e30."])
def test_malformed_tokens(keys, token):
    assert_denied(token)


def test_algorithm_confusion_cannot_use_public_rsa_bytes_as_hmac(keys, isolated_auth):
    public_der = base64.urlsafe_b64decode(keys["RS256"].public["n"] + "==")
    token = jwt.encode(claims(), public_der, algorithm="HS256",
                       headers={"kid": keys["RS256"].public["kid"]})
    assert_denied(token)
    isolated_auth.download.assert_not_called()


def test_token_controlled_key_urls_are_never_used(keys, isolated_auth):
    token = signed(keys, "ES256", jku="https://attacker.invalid/keys", x5u="https://attacker.invalid/cert")
    assert auth._decode_token(token)
    assert isolated_auth.download.call_count == 1
    auth.urlopen.assert_not_called()
    assert auth._jwks_url() == "https://security-tests.invalid/auth/v1/.well-known/jwks.json"


@pytest.mark.parametrize("mutation", [
    {"alg": "RS256"}, {"use": "enc"}, {"key_ops": ["sign"]},
    {"key_ops": "verify"}, {"kty": "oct"}, {"crv": "P-384"},
])
def test_jwk_metadata_must_match_token(keys, isolated_auth, mutation):
    bad = {**keys["ES256"].public, **mutation}
    isolated_auth.download.return_value = {"keys": [bad]}
    assert_denied(signed(keys, "ES256"))


def test_duplicate_key_id_is_rejected(keys, isolated_auth):
    isolated_auth.download.return_value = {"keys": [keys["ES256"].public] * 2}
    assert_denied(signed(keys, "ES256"))


def test_signature_failure_does_not_trigger_forced_refresh(keys, isolated_auth):
    token = jwt.encode(claims(), keys["ec-next"].private, algorithm="ES256", headers={"kid": "ec-current"})
    for _ in range(10):
        assert_denied(token)
    assert isolated_auth.download.call_count == 1


def test_rotation_refreshes_for_new_kid(keys, isolated_auth, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    assert auth._decode_token(signed(keys, "ES256"))
    clock[0] += 31
    isolated_auth.download.return_value = {"keys": [keys["ES256"].public, keys["ec-next"].public]}
    rotated = jwt.encode(claims(), keys["ec-next"].private, algorithm="ES256", headers={"kid": "ec-next"})
    assert auth._decode_token(rotated)["sub"] == USER_ID
    assert isolated_auth.download.call_count == 2
    assert auth._decode_token(signed(keys, "ES256"))  # overlap during rotation


def test_unknown_ids_are_rate_limited(keys, isolated_auth, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    for index in range(30):
        assert_denied(signed(keys, "ES256", kid=f"random-{index}"))
    assert isolated_auth.download.call_count == 1
    clock[0] += 31
    assert_denied(signed(keys, "ES256", kid="still-unknown"))
    assert isolated_auth.download.call_count == 2


def test_expired_cache_not_accepted_during_outage(keys, isolated_auth, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    token = signed(keys, "ES256")
    assert auth._decode_token(token)
    isolated_auth.download.side_effect = TimeoutError("Simulated offline")
    clock[0] += 601
    with pytest.raises(auth.SupabaseAuthUnavailable):
        auth._decode_token(token)
    assert auth.get_jwks(force_refresh=True) is None
    assert isolated_auth.download.call_count == 2


def test_failed_forced_refresh_does_not_extend_valid_cache(keys, isolated_auth, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    cached = auth.get_jwks()
    expires = auth._jwks_cache_expires_at
    isolated_auth.download.side_effect = OSError("Simulated offline")
    clock[0] += 31
    assert auth.get_jwks(force_refresh=True) is cached
    assert auth._jwks_cache_expires_at == expires
    clock[0] = expires
    assert auth.get_jwks() is None


def test_empty_jwks_revokes_old_keys(keys, isolated_auth, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    token = signed(keys, "ES256")
    assert auth._decode_token(token)
    isolated_auth.download.return_value = {"keys": []}
    clock[0] += 601
    assert_denied(token)
    assert auth._jwks_cache == {"keys": []}


@pytest.mark.parametrize("payload", [None, [], {}, {"keys": "bad"}, {"keys": [None]}])
def test_malformed_jwks_never_grants_access(keys, isolated_auth, payload):
    isolated_auth.download.return_value = payload
    with pytest.raises(auth.SupabaseAuthUnavailable):
        auth._decode_token(signed(keys, "ES256"))


def test_outage_retries_are_rate_limited(keys, isolated_auth):
    isolated_auth.download.side_effect = OSError("Simulated offline")
    for _ in range(20):
        assert auth.get_jwks() is None
    assert isolated_auth.download.call_count == 1


def test_parallel_requests_share_one_key_fetch(keys, isolated_auth):
    barrier = Barrier(8)

    def request_keys(_):
        barrier.wait(timeout=5)
        return auth.get_jwks()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(request_keys, range(8)))
    assert all(result == isolated_auth.download.return_value for result in results)
    assert isolated_auth.download.call_count == 1


def test_download_only_contacts_configured_project(keys, isolated_auth, monkeypatch):
    # Restore the downloader, with HTTP replaced by an in-memory response.
    response = BytesIO(json.dumps({"keys": [keys["ES256"].public]}).encode())
    opener = Mock(return_value=response)
    monkeypatch.setattr(auth, "urlopen", opener)
    result = ORIGINAL_DOWNLOADER()
    request = opener.call_args.args[0]
    assert request.full_url == "https://security-tests.invalid/auth/v1/.well-known/jwks.json"
    assert not request.has_header("Authorization")
    assert opener.call_args.kwargs["timeout"] == 5
    assert result["keys"][0]["kid"] == "ec-current"


ORIGINAL_DOWNLOADER = auth._download_jwks


def test_oversized_jwks_response_rejected(monkeypatch):
    monkeypatch.setattr(auth, "urlopen", Mock(return_value=BytesIO(b" " * (auth._JWKS_MAX_BYTES + 1))))
    with pytest.raises(ValueError, match="size limit"):
        ORIGINAL_DOWNLOADER()


def test_logs_do_not_include_error_body_or_credentials(isolated_auth, caplog):
    isolated_auth.download.side_effect = OSError("sensitive-response-body-secret")
    with caplog.at_level(logging.WARNING, logger="app.supabase_auth"):
        assert auth.get_jwks() is None
    assert "supabase_jwks_fetch_failed" in caplog.text
    assert "sensitive-response-body-secret" not in caplog.text
    assert TEST_SECRET not in caplog.text


def test_signed_non_object_payload_rejected(keys):
    token = jws.sign(b"[]", TEST_SECRET, algorithm="HS256")
    assert_denied(token)


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/protected")
    def protected(principal=Depends(auth.get_current_supabase_principal)):
        return {"user_id": principal.user_id}

    return TestClient(app)


def test_http_valid_token(client, keys):
    response = client.get("/protected", headers={"Authorization": f"Bearer {signed(keys)}"})
    assert response.status_code == 200
    assert response.json() == {"user_id": USER_ID}


def test_http_forged_token_denied(client):
    token = jwt.encode(claims(), "wrong-local-key", algorithm="HS256")
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "wrong-local-key" not in response.text


def test_http_outage_is_503_not_an_expired_session(client, keys, isolated_auth):
    isolated_auth.download.side_effect = OSError("Local simulated outage")
    response = client.get("/protected", headers={"Authorization": f"Bearer {signed(keys, 'ES256')}"})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_http_no_bearer_token_is_denied(client):
    response = client.get("/protected")
    assert response.status_code == 401


def test_malformed_trusted_public_key_is_unavailable(keys, isolated_auth):
    isolated_auth.download.return_value = {"keys": [{
        "kid": "ec-current", "alg": "ES256", "kty": "EC", "crv": "P-256",
        "x": "invalid-test-key", "y": "invalid-test-key",
    }]}
    with pytest.raises(auth.SupabaseAuthUnavailable):
        auth._decode_token(signed(keys, "ES256"))


@pytest.fixture
def production_client(monkeypatch):
    # Real registered routes, real middleware and real exception handler;
    # business/database dependencies alone are replaced. No startup/deploy.
    from app.main import app
    from app.routes import foundation
    from app import auth_routes
    from app.schemas.foundation import FoundationSessionModel

    session = FoundationSessionModel(
        user={"id": USER_ID, "email": "test@example.invalid"},
        player={"id": "00000000-0000-4000-8000-000000000002", "user_id": USER_ID,
                "display_name": "Local test user"},
    )
    stub_service = Mock()
    stub_service.get_session.return_value = session
    stub_service.bootstrap_session.return_value = session
    monkeypatch.setattr(foundation, "service", stub_service)
    monkeypatch.setattr(auth_routes, "foundation_identity_service", stub_service)
    db_calls = []

    def fake_db():
        db_calls.append(True)
        yield object()

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[foundation.get_db_session] = fake_db
    app.dependency_overrides[auth_routes.get_db] = fake_db
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield SimpleNamespace(client=client, service=stub_service, db_calls=db_calls)
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("method,path", [
    ("GET", "/v2/me"), ("POST", "/v2/bootstrap/me"), ("POST", "/auth/social-login"),
])
def test_real_routes_reject_forged_identity_before_business_access(production_client, method, path):
    token = jwt.encode(claims(), "wrong-test-key", algorithm="HS256")
    response = production_client.client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["code"] == "http_error"
    assert response.headers["X-Request-ID"]
    assert not production_client.db_calls
    assert not production_client.service.mock_calls


@pytest.mark.parametrize("method,path", [
    ("GET", "/v2/me"), ("POST", "/v2/bootstrap/me"), ("POST", "/auth/social-login"),
])
def test_real_routes_preserve_valid_sessions(keys, production_client, method, path):
    response = production_client.client.request(
        method, path, headers={"Authorization": f"Bearer {signed(keys, 'ES256')}"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == USER_ID
    assert production_client.db_calls
    assert len(production_client.service.mock_calls) == 1


def test_real_error_handler_preserves_retry_after(keys, isolated_auth, production_client):
    isolated_auth.download.side_effect = OSError("Local simulated outage")
    response = production_client.client.get(
        "/v2/me", headers={"Authorization": f"Bearer {signed(keys, 'ES256')}"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert not production_client.db_calls
    assert not production_client.service.mock_calls


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_preflight_checks_available_material(algorithm, capsys):
    from scripts.check_supabase_auth import main
    assert main(["--algorithm", algorithm]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "key_material_available"
    assert result["algorithm"] == algorithm


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_preflight_verifies_real_token_without_exposing_user_data(keys, algorithm, monkeypatch, capsys):
    from scripts.check_supabase_auth import main
    token = signed(keys, algorithm)
    monkeypatch.setattr("sys.stdin", StringIO(token + "\n"))
    assert main(["--verify-token-stdin"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "token_verified", "algorithm": algorithm}
    assert token not in output
    assert USER_ID not in output
    assert "test@example.invalid" not in output
    assert TEST_SECRET not in output


def test_preflight_rejects_forged_token(monkeypatch, capsys):
    from scripts.check_supabase_auth import main
    token = jwt.encode(claims(), "wrong-test-key", algorithm="HS256")
    monkeypatch.setattr("sys.stdin", StringIO(token + "\n"))
    assert main(["--verify-token-stdin"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "token_or_key_invalid"


def test_preflight_outage_is_not_success(isolated_auth, capsys):
    from scripts.check_supabase_auth import main
    isolated_auth.download.side_effect = OSError("Simulated offline")
    assert main(["--algorithm", "ES256"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "verification_unavailable"


def test_preflight_does_not_use_service_role_as_secret(isolated_auth, capsys):
    from scripts.check_supabase_auth import main
    isolated_auth.config.jwt_secret = ""
    assert main(["--algorithm", "HS256"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "verification_unavailable"


def test_preflight_hides_interactive_input(keys, monkeypatch, capsys):
    from scripts.check_supabase_auth import main
    stdin = Mock()
    stdin.isatty.return_value = True
    monkeypatch.setattr("sys.stdin", stdin)
    hidden_input = Mock(return_value=signed(keys))
    monkeypatch.setattr("scripts.check_supabase_auth.getpass.getpass", hidden_input)
    assert main(["--verify-token-stdin"]) == 0
    hidden_input.assert_called_once()
    stdin.readline.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "token_verified"
