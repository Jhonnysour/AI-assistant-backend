import base64
import hashlib
import http.client
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ai_assistant.conversations.adapters.outbound.chatgpt_plan import oauth
from ai_assistant.conversations.adapters.outbound.chatgpt_plan.credentials import CredentialRecord

HOST_ID = "urn:uuid:00000000-0000-4000-8000-000000000000"
ISSUED = "oaiapp_test_client_0001"
REDIRECT = "http://127.0.0.1:1455/auth/callback"


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def make_record(**overrides) -> CredentialRecord:
    values = dict(
        issuer=oauth.ISSUER, subject="user-123", client_id=ISSUED, ext_agent_host_id=HOST_ID,
        id_token="old-id", access_token="old-access", refresh_token="old-refresh",
        token_type="Bearer", expires_at=time.time() + 3600, scopes=oauth.SCOPES.split(),
    )
    values.update(overrides)
    return CredentialRecord(**values)


def test_pkce_challenge_is_s256_of_verifier():
    pkce = oauth.Pkce.generate()
    assert 43 <= len(pkce.verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest()).rstrip(b"=")
    assert pkce.challenge == expected.decode()


def test_registration_url_has_required_parameters():
    request = oauth.AuthorizationRequest.new(None, HOST_ID, REDIRECT)
    params = query_of(request.url())
    assert request.url().startswith(oauth.AUTHORIZE_URL + "?")
    assert params["client_id"] == "dynamic_agent_client"
    assert params["agent_name_hint"] == oauth.AGENT_NAME
    assert params["ext_agent_host_id"] == HOST_ID
    assert params["redirect_uri"] == REDIRECT
    assert params["scope"] == oauth.SCOPES
    assert "chatgpt.tokens.use.direct" in params["scope"].split()
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == request.pkce.challenge
    assert params["state"] == request.state and params["nonce"] == request.nonce
    assert request.pkce.verifier not in request.url()


def test_returning_url_uses_issued_client_without_name_hint():
    params = query_of(oauth.AuthorizationRequest.new(ISSUED, HOST_ID, REDIRECT).url())
    assert params["client_id"] == ISSUED
    assert "agent_name_hint" not in params
    assert params["ext_agent_host_id"] == HOST_ID


def test_callback_rejects_wrong_state():
    request = oauth.AuthorizationRequest.new(None, HOST_ID, REDIRECT)
    with pytest.raises(oauth.OAuthError) as error:
        oauth.parse_callback({"state": "other", "code": "c", "client_id": ISSUED}, request)
    assert error.value.code == "state_mismatch"


def test_callback_access_denied_stops_without_code():
    request = oauth.AuthorizationRequest.new(None, HOST_ID, REDIRECT)
    with pytest.raises(oauth.OAuthError) as error:
        oauth.parse_callback({"state": request.state, "error": "access_denied"}, request)
    assert error.value.code == "access_denied"


def test_registration_callback_requires_issued_client():
    request = oauth.AuthorizationRequest.new(None, HOST_ID, REDIRECT)
    with pytest.raises(oauth.OAuthError):
        oauth.parse_callback({"state": request.state, "code": "c"}, request)
    assert oauth.parse_callback(
        {"state": request.state, "code": "c", "client_id": ISSUED}, request
    ) == ("c", ISSUED)


def test_reauthorization_keeps_saved_client_and_rejects_a_different_one():
    request = oauth.AuthorizationRequest.new(ISSUED, HOST_ID, REDIRECT)
    assert oauth.parse_callback({"state": request.state, "code": "c"}, request) == ("c", ISSUED)
    with pytest.raises(oauth.OAuthError) as error:
        oauth.parse_callback({"state": request.state, "code": "c", "client_id": "oaiapp_other"}, request)
    assert error.value.code == "client_id_mismatch"


def test_id_token_validation(jwks, make_id_token):
    claims = oauth.validate_id_token(make_id_token(ISSUED, "n1"), jwks, ISSUED, "n1")
    assert claims["sub"] == "user-123"
    for token, nonce in [
        (make_id_token(ISSUED, "n1"), "n2"),  # nonce distinto
        (make_id_token("oaiapp_other", "n1"), "n1"),  # audiencia distinta
        (make_id_token(ISSUED, "n1", iss="https://evil.example"), "n1"),
        (make_id_token(ISSUED, "n1", exp=int(time.time()) - 3600), "n1"),
    ]:
        with pytest.raises(oauth.OAuthError):
            oauth.validate_id_token(token, jwks, ISSUED, nonce)


def test_id_token_signed_by_unknown_key_is_rejected(jwks, make_id_token):
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt as pyjwt

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = pyjwt.encode(
        {"iss": oauth.ISSUER, "aud": ISSUED, "sub": "x", "iat": int(time.time()),
         "exp": int(time.time()) + 600, "nonce": "n"},
        other, algorithm="RS256", headers={"kid": "test-key"},
    )
    with pytest.raises(oauth.OAuthError):
        oauth.validate_id_token(forged, jwks, ISSUED, "n")


def test_refresh_posts_rotation_grant_and_replaces_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={
            "access_token": "new-access", "refresh_token": "new-refresh",
            "token_type": "Bearer", "expires_in": 3600, "scope": oauth.SCOPES,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        record = oauth.refresh(http, make_record(expires_at=0))
    assert seen == {
        "grant_type": "refresh_token", "client_id": ISSUED,
        "refresh_token": "old-refresh", "resource": "https://api.openai.com/v1",
    }
    assert "scope" not in seen  # Se omite para conservar el grant.
    assert (record.access_token, record.refresh_token) == ("new-access", "new-refresh")
    assert record.expires_at > time.time() + 3000
    assert record.plan_usage_granted


def test_terminal_refresh_error_requires_reauthorization():
    def handler(request):
        return httpx.Response(400, json={"error": "refresh_token_reused"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(oauth.ReauthorizationRequired) as error:
            oauth.refresh(http, make_record())
    assert error.value.code == "refresh_token_reused"
    assert "old-refresh" not in str(error.value)


def test_needs_refresh_near_expiry():
    now = time.time()
    assert oauth.needs_refresh(make_record(expires_at=now + 60), now)
    assert not oauth.needs_refresh(make_record(expires_at=now + 3600), now)


def fake_openai(jwks, make_id_token, scope=oauth.SCOPES, captured=None):
    """Simula discovery, JWKS y el endpoint de tokens (sin red)."""
    state = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == oauth.DISCOVERY_URL:
            return httpx.Response(200, json={
                "issuer": oauth.ISSUER, "jwks_uri": f"{oauth.ISSUER}/.well-known/jwks.json",
                "revocation_endpoint": f"{oauth.ISSUER}/api/accounts/oauth/revoke",
            })
        if url.endswith("/jwks.json"):
            return httpx.Response(200, json=jwks)
        if url == oauth.TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if captured is not None:
                captured.update(form)
            return httpx.Response(200, json={
                "access_token": "acc", "refresh_token": "ref", "token_type": "Bearer",
                "expires_in": 3600, "scope": scope,
                "id_token": make_id_token(form["client_id"], state["nonce"]),
            })
        return httpx.Response(404)

    return handler, state


def browser_that_approves(state, client_id=ISSUED):
    """Simula al navegador: sigue la redirección al loopback con code/state/client_id."""
    def open_url(url: str) -> None:
        params = query_of(url)
        state["nonce"] = params["nonce"]
        callback = urlsplit(params["redirect_uri"])

        def visit():
            connection = http.client.HTTPConnection(callback.hostname, callback.port, timeout=5)
            connection.request("GET", f"{callback.path}?code=the-code&state={params['state']}&client_id={client_id}")
            connection.getresponse().read()
            connection.close()

        threading.Thread(target=visit, daemon=True).start()
    return open_url


def test_login_end_to_end_with_loopback_callback(jwks, make_id_token):
    captured = {}
    handler, state = fake_openai(jwks, make_id_token, captured=captured)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        record = oauth.login(http, HOST_ID, 0, browser_that_approves(state), timeout_seconds=10)
    assert record.client_id == ISSUED
    assert record.subject == "user-123"
    assert record.plan_usage_granted
    assert captured["grant_type"] == "authorization_code"
    assert captured["client_id"] == ISSUED
    assert captured["code"] == "the-code"
    assert captured["redirect_uri"].startswith("http://127.0.0.1:")
    assert captured["code_verifier"]


def test_login_without_plan_scope_reports_disabled(jwks, make_id_token):
    handler, state = fake_openai(jwks, make_id_token, scope="openid profile email offline_access")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        record = oauth.login(http, HOST_ID, 0, browser_that_approves(state), timeout_seconds=10)
    assert not record.plan_usage_granted


def test_login_rejects_other_account(jwks, make_id_token):
    handler, state = fake_openai(jwks, make_id_token)
    existing = make_record(subject="someone-else")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(oauth.OAuthError) as error:
            oauth.login(http, HOST_ID, 0, browser_that_approves(state), existing, timeout_seconds=10)
    assert error.value.code == "account_mismatch"


def test_callback_listener_times_out():
    with oauth.CallbackListener(0) as listener:
        with pytest.raises(oauth.OAuthError) as error:
            listener.wait(0.6)
    assert error.value.code == "callback_timeout"
