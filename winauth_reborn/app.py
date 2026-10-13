from __future__ import annotations

import base64
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from .appearance import ICONS
from .authenticators import decode_secret, export_record, generate_code, parse_export
from .pwa import PUBLIC_ASSETS, fingerprint, manifest
from .steam import SteamError, SteamService, cookie_session
from .storage import AccountNotFound, Vault


def create_app(config: dict | None = None) -> Flask:
    load_dotenv(Path.cwd() / ".env")
    app = Flask(__name__)
    app.config.update(
        AUTH_USERNAME=os.environ.get("WINAUTH_USERNAME", "admin"),
        AUTH_PASSWORD=os.environ.get("WINAUTH_PASSWORD", ""),
        AUTH_PASSWORD_HASH=os.environ.get("WINAUTH_PASSWORD_HASH", ""),
        DATA_DIR=os.environ.get("WINAUTH_DATA_DIR", "./data"),
        STEAM_PROXY=os.environ.get("WINAUTH_STEAM_PROXY", ""),
        HTTPS_ONLY=os.environ.get("WINAUTH_HTTPS_ONLY", "false").lower() == "true",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_NAME="winauth_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if config:
        app.config.update(config)
    password_hash = app.config["AUTH_PASSWORD_HASH"]
    if not password_hash:
        if not app.config["AUTH_PASSWORD"]:
            raise RuntimeError("Set WINAUTH_PASSWORD or WINAUTH_PASSWORD_HASH before starting.")
        password_hash = generate_password_hash(app.config["AUTH_PASSWORD"])
    app.config.pop("AUTH_PASSWORD", None)
    vault = Vault(Path(app.config["DATA_DIR"]).resolve())
    steam = SteamService(app.config["STEAM_PROXY"] or None)
    app.extensions.update(vault=vault, steam=steam)
    csrf = secrets.token_urlsafe(32)
    app.secret_key = secrets.token_bytes(32)
    app.config["SESSION_COOKIE_SECURE"] = app.config["HTTPS_ONLY"]
    source = Path(__file__).parent
    pwa_fingerprint = fingerprint(
        [source / "static" / name for name in PUBLIC_ASSETS]
        + [
            source / "pwa.py",
            source / "templates" / "login.html",
            source / "templates" / "pwa_head.html",
            source / "templates" / "pwa_controls.html",
            source / "templates" / "service-worker.js.j2",
        ]
    )

    @app.before_request
    def authenticate():
        public_asset = (
            request.endpoint == "static" and request.view_args.get("filename") in PUBLIC_ASSETS
        )
        if request.endpoint in {"login_page", "pwa_manifest", "pwa_worker"} or public_asset:
            return None
        browser_login = request.endpoint == "browser_login"
        auth = request.authorization
        username_ok = (
            auth is not None
            and auth.type == "basic"
            and secrets.compare_digest(
                (auth.username or "").encode(), app.config["AUTH_USERNAME"].encode()
            )
        )
        cookie_ok = session.get("authenticated") is True and not session.get("locked")
        password_ok = (
            check_password_hash(password_hash, (auth.password or ""))
            if auth and (browser_login or not cookie_ok)
            else False
        )
        authenticated = (username_ok and password_ok) or (cookie_ok and not browser_login)
        if session.get("locked") and not browser_login:
            authenticated = False
        if not authenticated:
            if request.endpoint == "index":
                return redirect("/login")
            if browser_login or request.headers.get("X-Requested-With") == "WinAuthReborn":
                return jsonify(
                    error="Incorrect username or password."
                    if browser_login
                    else "Sign in to access your vault."
                ), 401
            return Response(
                "Authentication required.",
                401,
                {
                    "WWW-Authenticate": 'Basic realm="WinAuth Reborn", charset="UTF-8"',
                },
            )
        if browser_login:
            origin = request.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.netloc != request.host
                    or (app.config["HTTPS_ONLY"] and parsed.scheme != "https")
                ):
                    return jsonify(error="Login must come from this application's origin."), 403
            if request.headers.get("X-Requested-With") != "WinAuthReborn" or not request.is_json:
                return jsonify(error="Use the login page to start a browser session."), 403
            return None
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
            request.headers.get("X-CSRF-Token", "").encode(), csrf.encode()
        ):
            return jsonify(
                error="Page expired or request is not from this app. Reload and retry."
            ), 403

    @app.after_request
    def protect(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if app.config["HTTPS_ONLY"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(AccountNotFound)
    def missing(error):
        return jsonify(error="Account not found."), 404

    @app.errorhandler(SteamError)
    def steam_failure(error):
        return jsonify(error=str(error)), 502

    @app.errorhandler(HTTPException)
    def http_failure(error):
        return jsonify(error=error.description), error.code

    def body() -> dict:
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    def string(data: dict, name: str, maximum=10000) -> str:
        value = data.get(name)
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise ValueError(
                f"{name} is required and must be text of at most {maximum} characters."
            )
        return value

    def steam_record(account_id: str) -> dict:
        record = vault.get(account_id)
        if record["kind"] != "steam":
            raise ValueError("This account is not a Steam authenticator.")
        if not record["steam_data"].get("identity_secret"):
            raise ValueError("Steam confirmations require identity_secret in the exported data.")
        return record

    @app.get("/login")
    def login_page():
        return render_template("login.html")

    @app.get("/static/manifest.webmanifest")
    def pwa_manifest():
        return jsonify(manifest()), 200, {"Content-Type": "application/manifest+json"}

    @app.get("/service-worker.js")
    def pwa_worker():
        return Response(
            render_template(
                "service-worker.js.j2",
                mode="server",
                fingerprint=pwa_fingerprint,
                assets=["./login", "./static/manifest.webmanifest"]
                + ["./static/" + name for name in sorted(PUBLIC_ASSETS)],
            ),
            mimetype="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @app.post("/api/login")
    def browser_login():
        body()
        session.clear()
        session["authenticated"] = True
        return jsonify(ok=True, csrf=csrf)

    @app.post("/api/logout")
    def browser_logout():
        session.clear()
        session["locked"] = True
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return render_template("index.html", csrf=csrf, settings=vault.settings(), icons=ICONS)

    @app.get("/api/settings")
    def settings():
        return jsonify(settings=vault.settings())

    @app.patch("/api/settings")
    def update_settings():
        return jsonify(settings=vault.update_settings(body()))

    @app.get("/api/accounts")
    def accounts():
        now = time.time()
        result = []
        for record in vault.list():
            timestamp = now + (steam.offset if record["kind"] == "steam" else 0)
            result.append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "issuer": record["issuer"],
                    "kind": record["kind"],
                    "period": record["period"],
                    "algorithm": record["algorithm"],
                    "digits": record["digits"],
                    "counter": record["counter"],
                    "code": None if record["kind"] == "hotp" else generate_code(record, timestamp),
                    "remaining": record["period"] - timestamp % record["period"],
                    "steam_connected": bool(record.get("steam_session")),
                    "confirmations_available": bool(record["steam_data"].get("identity_secret")),
                    "icon": record.get("icon", "initial"),
                    "custom_icon": record.get("custom_icon", "")
                    if record.get("icon") == "custom"
                    else "",
                    "color": record.get("color", "#b4ef82"),
                    "pinned": record.get("pinned", False),
                }
            )
        return jsonify(accounts=result, server_time=now, settings=vault.settings())

    @app.patch("/api/accounts/<account_id>")
    def edit_account(account_id):
        vault.edit(account_id, body())
        return jsonify(ok=True)

    @app.post("/api/accounts/<account_id>/steam/recovery")
    def steam_recovery(account_id):
        record = vault.get(account_id)
        if record["kind"] != "steam":
            raise ValueError("This account is not a Steam authenticator.")
        data = record["steam_data"]
        timestamp = time.time() + steam.offset
        return jsonify(
            guard_code=generate_code(record, timestamp),
            remaining=record["period"] - timestamp % record["period"],
            shared_secret=record["secret"],
            shared_secret_base64=base64.b64encode(decode_secret(record["secret"])).decode(),
            recovery_code=data.get("revocation_code") or data.get("recovery_code") or None,
            serial_number=data.get("serial_number") or None,
        )

    @app.post("/api/import")
    def import_file():
        text = string(body(), "text", 2 * 1024 * 1024)
        records = parse_export(text)
        added, skipped = vault.import_records(records)
        return jsonify(added=added, skipped=skipped)

    @app.post("/api/export")
    def export_file():
        text = "\n".join(export_record(record) for record in vault.list()) + "\n"
        return Response(
            text,
            mimetype="text/plain",
            headers={
                "Content-Disposition": 'attachment; filename="winauth-export.txt"',
            },
        )

    @app.delete("/api/accounts/<account_id>")
    def remove(account_id):
        vault.delete(account_id)
        return jsonify(ok=True)

    @app.post("/api/accounts/<account_id>/hotp")
    def hotp(account_id):
        return jsonify(code=vault.consume_hotp(account_id))

    @app.post("/api/accounts/<account_id>/steam/login")
    def steam_login(account_id):
        record = steam_record(account_id)
        data = body()
        result = steam.login(record, string(data, "username", 200), string(data, "password", 1000))
        if result.get("session"):
            vault.update_session(account_id, result.pop("session"))
            result["connected"] = True
        return jsonify(result)

    @app.post("/api/accounts/<account_id>/steam/login-code")
    def steam_login_code(account_id):
        steam_record(account_id)
        data = body()
        session = steam.login_code(account_id, string(data, "flow", 200), string(data, "code", 20))
        vault.update_session(account_id, session)
        return jsonify(connected=True)

    @app.post("/api/accounts/<account_id>/steam/session")
    def steam_session(account_id):
        record = steam_record(account_id)
        data = body()
        saved = cookie_session(string(data, "steam_id", 30), string(data, "cookie"), record)
        steam.confirmations({**record, "steam_session": saved})
        vault.update_session(account_id, saved)
        return jsonify(connected=True)

    @app.delete("/api/accounts/<account_id>/steam/session")
    def steam_disconnect(account_id):
        steam_record(account_id)
        vault.update_session(account_id, None)
        return jsonify(ok=True)

    @app.get("/api/accounts/<account_id>/steam/confirmations")
    def steam_confirmations(account_id):
        return jsonify(confirmations=steam.confirmations(steam_record(account_id)))

    @app.post("/api/accounts/<account_id>/steam/confirmations/<confirmation_id>")
    def steam_confirm(account_id, confirmation_id):
        data = body()
        operation = string(data, "operation", 10)
        if operation not in {"allow", "cancel"}:
            raise ValueError("Operation must be allow or cancel.")
        nonce = string(data, "nonce", 100)
        if not confirmation_id.isdigit() or not nonce.isdigit():
            raise ValueError("Confirmation ID and nonce must be numeric.")
        steam.confirmations(steam_record(account_id), operation, confirmation_id, nonce)
        return jsonify(ok=True)

    return app
