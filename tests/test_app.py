import base64
import re
from unittest.mock import Mock

import pytest
from conftest import SECRET, STEAM_ID
from werkzeug.security import generate_password_hash

from winauth_reborn.app import create_app
from winauth_reborn.authenticators import parse_export
from winauth_reborn.steam import SteamError
from winauth_reborn.storage import Vault


@pytest.mark.parametrize("path", ["/", "/static/app.css", "/static/app.js", "/api/accounts"])
@pytest.mark.parametrize("credentials", [None, "test-admin:wrong", "wrong:test-password-only"])
def test_basic_auth_covers_html_static_and_api(client, path, credentials):
    headers = {}
    if credentials:
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    response = client.get(path, headers=headers)
    if path == "/":
        assert response.status_code == 302
        assert response.headers["Location"] == "/login"
        return
    if path == "/static/app.css":
        assert response.status_code == 200
        return
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic ")
    assert SECRET not in response.text


@pytest.mark.parametrize("path", ["/", "/static/app.css", "/static/app.js", "/api/accounts"])
def test_valid_basic_auth(client, auth, path):
    assert client.get(path, headers=auth).status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/import"),
        ("post", "/api/export"),
        ("delete", "/api/accounts/missing"),
        ("post", "/api/accounts/missing/hotp"),
        ("post", "/api/accounts/missing/steam/login"),
        ("post", "/api/accounts/missing/steam/login-code"),
        ("post", "/api/accounts/missing/steam/session"),
        ("delete", "/api/accounts/missing/steam/session"),
        ("post", "/api/accounts/missing/steam/confirmations/1"),
    ],
)
@pytest.mark.parametrize("token", [None, "forged-token"])
def test_all_writes_require_csrf(client, auth, method, path, token):
    headers = dict(auth)
    if token:
        headers["X-CSRF-Token"] = token
    response = getattr(client, method)(path, headers=headers, json={})
    assert response.status_code == 403


def test_csrf_never_bypasses_basic_auth(client, write_headers):
    response = client.post(
        "/api/export",
        headers={
            "X-CSRF-Token": write_headers["X-CSRF-Token"],
        },
    )
    assert response.status_code == 401


def test_non_ascii_csrf_is_rejected_without_server_error(client, auth):
    response = client.post("/api/export", headers={**auth, "X-CSRF-Token": "not-valid-é"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path,authenticated",
    [
        ("/", True),
        ("/static/app.js", True),
        ("/api/accounts", True),
        ("/unknown-route", True),
        ("/api/accounts", False),
    ],
)
def test_security_headers_on_every_response(client, auth, path, authenticated):
    response = client.get(path, headers=auth if authenticated else {})
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    csp = response.headers["Content-Security-Policy"]
    for directive in [
        "default-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]:
        assert directive in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp


def test_password_hash_config_and_no_plaintext_config(vault_dir):
    app = create_app(
        {
            "TESTING": True,
            "AUTH_USERNAME": "hashed",
            "AUTH_PASSWORD": "",
            "AUTH_PASSWORD_HASH": generate_password_hash("test-only-hash-password"),
            "DATA_DIR": str(vault_dir),
            "HTTPS_ONLY": False,
        }
    )
    auth = "Basic " + base64.b64encode(b"hashed:test-only-hash-password").decode()
    response = app.test_client().get("/api/accounts", headers={"Authorization": auth})
    assert response.status_code == 200
    assert "AUTH_PASSWORD" not in app.config
    assert "Strict-Transport-Security" not in response.headers


def test_startup_requires_password(vault_dir):
    with pytest.raises(RuntimeError, match="PASSWORD"):
        create_app({"DATA_DIR": str(vault_dir), "AUTH_PASSWORD": "", "AUTH_PASSWORD_HASH": ""})


def test_api_import_export_delete_hotp_restart(app, client, auth, write_headers, vault_dir):
    text = (
        f"otpauth://hotp/RFC:alice?secret={SECRET}&counter=0&custom=kept\n"
        f"otpauth://totp/RFC:bob?secret={SECRET}&algorithm=SHA256&period=45\n"
    )
    response = client.post("/api/import", headers=write_headers, json={"text": text})
    assert response.status_code == 200
    assert response.json == {"added": 2, "skipped": 0}
    assert client.post("/api/import", headers=write_headers, json={"text": text}).json == {
        "added": 0,
        "skipped": 2,
    }
    listed = client.get("/api/accounts", headers=auth).json["accounts"]
    hotp = next(record for record in listed if record["kind"] == "hotp")
    assert hotp["code"] is None
    assert hotp["counter"] == 0
    assert app.extensions["vault"].get(hotp["id"])["counter"] == 0
    response = client.post(f"/api/accounts/{hotp['id']}/hotp", headers=write_headers)
    assert response.json == {"code": "755224"}
    assert Vault(vault_dir).get(hotp["id"])["counter"] == 1
    exported = client.post("/api/export", headers=write_headers)
    assert exported.status_code == 200
    assert exported.mimetype == "text/plain"
    assert "attachment" in exported.headers["Content-Disposition"]
    records = parse_export(exported.text)
    assert next(r for r in records if r["kind"] == "hotp")["counter"] == 1
    assert "custom=kept" in exported.text
    assert client.delete(f"/api/accounts/{hotp['id']}", headers=write_headers).json == {"ok": True}
    assert len(Vault(vault_dir).list()) == 1
    assert client.delete(f"/api/accounts/{hotp['id']}", headers=write_headers).status_code == 404


def test_invalid_import_is_atomic_with_existing_accounts(app, client, write_headers):
    valid = f"otpauth://totp/Existing?secret={SECRET}"
    client.post("/api/import", headers=write_headers, json={"text": valid})
    before = app.extensions["vault"].list()
    response = client.post(
        "/api/import",
        headers=write_headers,
        json={
            "text": f"otpauth://totp/New?secret={SECRET}\ninvalid",
        },
    )
    assert response.status_code == 400
    assert "Line 2" in response.json["error"]
    assert app.extensions["vault"].list() == before


@pytest.mark.parametrize("payload", [None, [], "text", {"text": ""}, {"text": 12}, {}])
def test_api_import_rejects_invalid_json_bodies(client, write_headers, payload):
    response = client.post("/api/import", headers=write_headers, json=payload)
    assert response.status_code in {400, 415}


def test_list_does_not_expose_secrets_sessions_or_export_uri(
    app,
    client,
    auth,
    steam_record,
    hotp_record,
    monkeypatch,
):
    monkeypatch.setattr("winauth_reborn.app.time.time", lambda: 59)
    vault = app.extensions["vault"]
    vault.import_records([steam_record, hotp_record])
    vault.update_session(
        steam_record["id"],
        {
            "steam_id": STEAM_ID,
            "steam_login_secure": "unique-private-cookie",
        },
    )
    response = client.get("/api/accounts", headers=auth)
    accounts = response.json["accounts"]
    allowed = {
        "id",
        "name",
        "issuer",
        "kind",
        "period",
        "counter",
        "code",
        "remaining",
        "steam_connected",
        "confirmations_available",
        "algorithm",
        "digits",
        "icon",
        "custom_icon",
        "color",
        "pinned",
    }
    assert all(set(account) <= allowed for account in accounts)
    for forbidden in [
        SECRET,
        "identity_secret",
        "steam_login_secure",
        "unique-private-cookie",
        "otpauth://",
    ]:
        assert forbidden not in response.text
    steam = next(record for record in accounts if record["kind"] == "steam")
    assert steam["code"] == "PV9M4"
    assert steam["steam_connected"] is True
    assert steam["confirmations_available"] is True
    assert steam["remaining"] == 1
    assert vault.get(hotp_record["id"])["counter"] == 0


def test_account_route_errors(app, client, write_headers, totp_record):
    app.extensions["vault"].import_records([totp_record])
    assert client.post("/api/accounts/missing/hotp", headers=write_headers).status_code == 404
    assert (
        client.post(
            f"/api/accounts/{totp_record['id']}/hotp",
            headers=write_headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/accounts/{totp_record['id']}/steam/login",
            headers=write_headers,
            json={"username": "test", "password": "test"},
        ).status_code
        == 400
    )


def test_steam_api_session_workflow(app, client, auth, write_headers, steam_record, monkeypatch):
    vault = app.extensions["vault"]
    service = app.extensions["steam"]
    vault.import_records([steam_record])
    prefix = f"/api/accounts/{steam_record['id']}/steam"
    saved = {"steam_id": STEAM_ID, "steam_login_secure": "private-session"}
    login = Mock(return_value={"session": saved})
    monkeypatch.setattr(service, "login", login)
    response = client.post(
        prefix + "/login",
        headers=write_headers,
        json={
            "username": "fixture-user",
            "password": "fixture-password",
        },
    )
    assert response.json == {"connected": True}
    assert "private-session" not in response.text
    assert vault.get(steam_record["id"])["steam_session"] == saved
    login.assert_called_once_with(steam_record, "fixture-user", "fixture-password")
    confirmation = {"id": "123", "nonce": "456", "headline": "Test trade"}
    confirmations = Mock(return_value=[confirmation])
    monkeypatch.setattr(service, "confirmations", confirmations)
    assert client.get(prefix + "/confirmations", headers=auth).json == {
        "confirmations": [confirmation],
    }
    for operation in ["allow", "cancel"]:
        response = client.post(
            prefix + "/confirmations/123",
            headers=write_headers,
            json={
                "operation": operation,
                "nonce": "456",
            },
        )
        assert response.json == {"ok": True}
        assert confirmations.call_args.args[1:] == (operation, "123", "456")
    assert client.delete(prefix + "/session", headers=write_headers).json == {"ok": True}
    assert "steam_session" not in vault.get(steam_record["id"])


def test_steam_api_email_challenge_and_cookie_fallback(
    app,
    client,
    write_headers,
    steam_record,
    monkeypatch,
):
    vault = app.extensions["vault"]
    service = app.extensions["steam"]
    vault.import_records([steam_record])
    prefix = f"/api/accounts/{steam_record['id']}/steam"
    challenge = {"needs_code": True, "flow": "test-flow", "message": "Email code"}
    monkeypatch.setattr(service, "login", Mock(return_value=challenge))
    response = client.post(
        prefix + "/login",
        headers=write_headers,
        json={
            "username": "fixture-user",
            "password": "fixture-password",
        },
    )
    assert response.json == challenge
    assert "steam_session" not in vault.get(steam_record["id"])
    saved = {"steam_id": STEAM_ID, "steam_login_secure": "test-cookie"}
    login_code = Mock(return_value=saved)
    monkeypatch.setattr(service, "login_code", login_code)
    response = client.post(
        prefix + "/login-code",
        headers=write_headers,
        json={
            "flow": "test-flow",
            "code": "ABCDE",
        },
    )
    assert response.json == {"connected": True}
    login_code.assert_called_once_with(steam_record["id"], "test-flow", "ABCDE")
    confirmations = Mock(return_value=[])
    monkeypatch.setattr(service, "confirmations", confirmations)
    response = client.post(
        prefix + "/session",
        headers=write_headers,
        json={
            "steam_id": STEAM_ID,
            "cookie": STEAM_ID + "||test-cookie-token",
        },
    )
    assert response.json == {"connected": True}
    assert confirmations.call_args.args[0]["steam_session"]["steam_login_secure"] == (
        STEAM_ID + "%7C%7Ctest-cookie-token"
    )
    assert vault.get(steam_record["id"])["steam_session"]["steam_login_secure"].endswith(
        "%7C%7Ctest-cookie-token"
    )


def test_failed_cookie_validation_does_not_save_session(
    app,
    client,
    write_headers,
    steam_record,
    monkeypatch,
):
    vault = app.extensions["vault"]
    vault.import_records([steam_record])
    monkeypatch.setattr(
        app.extensions["steam"],
        "confirmations",
        Mock(side_effect=SteamError("Steam session expired")),
    )
    response = client.post(
        f"/api/accounts/{steam_record['id']}/steam/session",
        headers=write_headers,
        json={"steam_id": STEAM_ID, "cookie": STEAM_ID + "||expired-test-token"},
    )
    assert response.status_code == 502
    assert "expired" in response.json["error"]
    assert "steam_session" not in vault.get(steam_record["id"])


@pytest.mark.parametrize(
    "confirmation_id,payload",
    [
        ("123", {"operation": "invalid", "nonce": "456"}),
        ("non-numeric", {"operation": "allow", "nonce": "456"}),
        ("123", {"operation": "allow", "nonce": "not-numeric"}),
        ("123", {"operation": "allow"}),
    ],
)
def test_confirmation_api_validates_actions(
    app,
    client,
    write_headers,
    steam_record,
    confirmation_id,
    payload,
    monkeypatch,
):
    app.extensions["vault"].import_records([steam_record])
    operation = Mock()
    monkeypatch.setattr(app.extensions["steam"], "confirmations", operation)
    response = client.post(
        f"/api/accounts/{steam_record['id']}/steam/confirmations/{confirmation_id}",
        headers=write_headers,
        json=payload,
    )
    assert response.status_code == 400
    operation.assert_not_called()


def test_csrf_is_unique_across_app_restarts(app, client, auth, write_headers, vault_dir):
    restarted = create_app(
        {
            "TESTING": True,
            "AUTH_USERNAME": "test-admin",
            "AUTH_PASSWORD": "test-password-only",
            "AUTH_PASSWORD_HASH": "",
            "DATA_DIR": str(vault_dir),
        }
    )
    response = restarted.test_client().post("/api/export", headers=write_headers)
    assert response.status_code == 403
    new_page = restarted.test_client().get("/", headers=auth)
    new_token = re.search(r'name="csrf-token" content="([^"]+)"', new_page.text).group(1)
    assert new_token != write_headers["X-CSRF-Token"]


@pytest.mark.parametrize("entry", [{}, None, {"id": "123"}, "invalid"])
def test_malformed_steam_confirmation_is_upstream_error_not_missing_account(
    app,
    client,
    auth,
    steam_record,
    monkeypatch,
    entry,
):
    vault = app.extensions["vault"]
    vault.import_records([steam_record])
    vault.update_session(
        steam_record["id"],
        {
            "steam_id": STEAM_ID,
            "steam_login_secure": "fixture-cookie",
        },
    )
    service = app.extensions["steam"]
    monkeypatch.setattr(service, "sync_time", lambda: None)
    monkeypatch.setattr(
        service,
        "request",
        Mock(
            return_value={
                "success": True,
                "conf": [entry],
            }
        ),
    )
    response = client.get(
        f"/api/accounts/{steam_record['id']}/steam/confirmations",
        headers=auth,
    )
    assert response.status_code == 502
    assert response.json["error"] != "Account not found."
    assert vault.get(steam_record["id"])["id"] == steam_record["id"]
