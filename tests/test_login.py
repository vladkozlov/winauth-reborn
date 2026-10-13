import base64
import time

import pytest
from itsdangerous import TimestampSigner

from winauth_reborn.app import create_app


def login(client, auth, origin=None):
    headers = {**auth, "X-Requested-With": "WinAuthReborn"}
    if origin:
        headers["Origin"] = origin
    return client.post("/api/login", headers=headers, json={})


def test_friendly_login_is_public_but_vault_remains_private(client):
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"] == "/login"
    page = client.get("/login")
    assert page.status_code == 200
    assert "password-login-form" in page.text and "pin-login-form" in page.text
    assert "csrf-token" not in page.text
    for asset in ("app.css", "login.css", "login.js"):
        assert client.get("/static/" + asset).status_code == 200
    assert client.get("/static/editor.js").status_code == 401
    assert client.get("/api/accounts").status_code == 401


def test_public_pwa_shell_and_manifest_do_not_expose_the_vault(client):
    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.mimetype == "application/manifest+json"
    assert manifest.json["start_url"] == "../" and manifest.json["display"] == "standalone"
    for icon in manifest.json["icons"]:
        response = client.get("/static/" + icon["src"])
        assert response.status_code == 200 and response.mimetype == "image/png"
    worker = client.get("/service-worker.js")
    assert worker.status_code == 200 and worker.mimetype == "application/javascript"
    assert worker.headers["Service-Worker-Allowed"] == "/"
    assert '"./login"' in worker.text and '"./static/offline.html"' in worker.text
    assert 'const MODE = "server"' in worker.text
    assert "/api/" not in worker.text and '"./index.html"' not in worker.text
    assert client.get("/static/offline.html").status_code == 200
    assert client.get("/static/browser-vault.js").status_code == 401
    assert client.get("/api/export").status_code == 401


def test_cookie_session_contains_no_credentials_and_requires_csrf(app, client, auth):
    response = login(client, auth)
    assert response.status_code == 200
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
    decoded = app.session_interface.get_signing_serializer(app).loads(
        client.get_cookie("winauth_session").value
    )
    assert decoded == {"authenticated": True}
    assert client.get("/api/accounts").status_code == 200
    assert client.post("/api/export").status_code == 403
    token = response.json["csrf"]
    assert client.post("/api/export", headers={"X-CSRF-Token": token}).status_code == 200


def test_logout_blocks_cookie_and_cached_basic_until_explicit_login(client, auth):
    response = login(client, auth)
    token = response.json["csrf"]
    assert client.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.get("/api/accounts", headers=auth).status_code == 401
    assert client.get("/", headers=auth).headers["Location"] == "/login"
    assert login(client, auth).status_code == 200
    assert client.get("/api/accounts").status_code == 200


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://localhost", "https://localhost.evil.example"]
)
def test_login_rejects_cross_origin_or_insecure_origin(client, auth, origin):
    assert login(client, auth, origin).status_code == 403
    assert client.get("/api/accounts").status_code == 401


def test_login_requires_basic_credentials_and_custom_json_request(client, auth):
    assert client.post("/api/login", headers=auth, json={}).status_code == 403
    assert (
        client.post("/api/login", headers={**auth, "X-Requested-With": "WinAuthReborn"}).status_code
        == 403
    )
    wrong = {"Authorization": "Basic " + base64.b64encode(b"test-admin:wrong").decode()}
    response = login(client, wrong)
    assert response.status_code == 401 and "WWW-Authenticate" not in response.headers
    assert login(client, auth).status_code == 200
    assert login(client, wrong).status_code == 401
    assert login(client, {}, "https://localhost").status_code == 401


def test_ajax_expired_session_does_not_trigger_native_auth_prompt(client):
    response = client.get("/api/accounts", headers={"X-Requested-With": "WinAuthReborn"})
    assert response.status_code == 401 and "WWW-Authenticate" not in response.headers


def test_cookie_expires_after_twelve_hours(app, client, monkeypatch):
    serializer = app.session_interface.get_signing_serializer(app)
    old_timestamp = int(time.time()) - 12 * 3600 - 1
    with monkeypatch.context() as patch:
        patch.setattr(TimestampSigner, "get_timestamp", lambda self: old_timestamp)
        cookie = serializer.dumps({"authenticated": True})
    client.set_cookie("winauth_session", cookie)
    assert client.get("/api/accounts").status_code == 401


def test_same_origin_login_allowed_and_logout_requires_csrf(client, auth):
    assert login(client, auth, "https://localhost").status_code == 200
    assert client.post("/api/logout").status_code == 403
    assert client.get("/api/accounts").status_code == 200


def test_server_restart_invalidates_browser_session_but_basic_still_works(
    app,
    client,
    auth,
    vault_dir,
):
    assert login(client, auth).status_code == 200
    cookie = client.get_cookie("winauth_session").value
    restarted = create_app(
        {
            "TESTING": True,
            "DATA_DIR": vault_dir,
            "AUTH_USERNAME": "test-admin",
            "AUTH_PASSWORD": "test-password-only",
            "AUTH_PASSWORD_HASH": "",
            "HTTPS_ONLY": True,
        }
    ).test_client()
    restarted.set_cookie("winauth_session", cookie)
    assert restarted.get("/api/accounts").status_code == 401
    assert restarted.get("/api/accounts", headers=auth).status_code == 200
