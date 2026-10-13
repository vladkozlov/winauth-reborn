from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import threading
import time
from urllib.parse import quote, urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .authenticators import generate_code, validate_steam_id

API = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com"
logger = logging.getLogger(__name__)


class SteamError(Exception):
    pass


def confirmation_key(identity_secret: str, timestamp: int, tag: str) -> str:
    key = base64.b64decode(identity_secret, validate=True)
    message = struct.pack(">Q", timestamp) + tag.encode()[:32]
    return base64.b64encode(hmac.new(key, message, hashlib.sha1).digest()).decode()


def device_id(steam_id: str) -> str:
    digest = hashlib.sha1(steam_id.encode()).hexdigest()
    return f"android:{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


class SteamService:
    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = self.validate_proxy_url(proxy_url) if proxy_url else None
        self.offset = 0.0
        self.synced_at: float | None = None
        self.pending: dict[str, dict] = {}
        self.lock = threading.RLock()

    @staticmethod
    def validate_proxy_url(proxy_url: str) -> str:
        if not isinstance(proxy_url, str) or len(proxy_url) > 2048:
            raise ValueError("Steam proxy URL must be text of at most 2048 characters.")
        try:
            proxy = urlsplit(proxy_url)
            port = proxy.port
        except ValueError as exc:
            raise ValueError("Steam proxy must be a valid socks5h://host:port URL.") from exc
        if (
            proxy.scheme != "socks5h"
            or not proxy.hostname
            or port is None
            or port == 0
            or proxy.path
            or proxy.query
            or proxy.fragment
            or (proxy.username is None) != (proxy.password is None)
            or (proxy.username is not None and (not proxy.username or proxy.password == ""))
        ):
            raise ValueError(
                "Steam proxy must be a socks5h://host:port URL, optionally with "
                "username:password credentials."
            )
        return proxy_url

    def set_proxy(self, proxy_url: str | None):
        proxy_url = self.validate_proxy_url(proxy_url) if proxy_url else None
        with self.lock:
            if self.proxy_url == proxy_url:
                return
            self.proxy_url = proxy_url
            for flow in self.pending.values():
                flow["session"].close()
            self.pending.clear()

    @staticmethod
    def request(session: requests.Session, method: str, url: str, **kwargs) -> dict:
        response = None
        try:
            response = session.request(
                method, url, timeout=(5, 20), allow_redirects=False, **kwargs
            )
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise SteamError("Steam redirected the request. Your session may have expired.")
            body = response.json()
        except requests.RequestException as exc:
            failed_response = getattr(exc, "response", None) or response
            parsed_url = urlsplit(url)
            logger.warning(
                "Steam request failed: method=%s host=%s path=%s error=%s status=%s "
                "content_type=%s",
                method,
                parsed_url.hostname,
                parsed_url.path,
                type(exc).__name__,
                getattr(failed_response, "status_code", None),
                getattr(failed_response, "headers", {}).get("Content-Type"),
            )
            raise SteamError(
                "Steam could not be reached or returned invalid data. Try again later."
            ) from exc
        if not isinstance(body, dict):
            raise SteamError("Steam returned an unexpected response.")
        result = response.headers.get("X-eresult")
        if result and result not in {"1", "29"}:
            raise SteamError(
                f"Steam rejected this request (result {result}). Check your login and try again."
            )
        return body

    def api(self, session: requests.Session, endpoint: str, data: dict, method="POST") -> dict:
        kwargs = {"params" if method == "GET" else "data": data}
        body = self.request(
            session, method, f"{API}/IAuthenticationService/{endpoint}/v1/", **kwargs
        )
        if not isinstance(body.get("response"), dict):
            raise SteamError("Steam's authentication response is missing required data.")
        return body["response"]

    def sync_time(self):
        with self.lock:
            if self.synced_at is not None and time.monotonic() - self.synced_at < 300:
                return
            before = time.time()
            with self.new_session() as session:
                body = self.request(session, "POST", f"{API}/ITwoFactorService/QueryTime/v1/")
            response = body.get("response")
            if not isinstance(response, dict):
                raise SteamError("Steam did not return its server time.")
            server_time = response.get("server_time")
            if not server_time:
                raise SteamError("Steam did not return its server time.")
            try:
                self.offset = int(server_time) - (before + time.time()) / 2
            except (TypeError, ValueError) as exc:
                raise SteamError("Steam returned an invalid server time.") from exc
            self.synced_at = time.monotonic()

    def new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "WinAuth-Reborn/0.1",
                "Origin": COMMUNITY,
                "Referer": COMMUNITY + "/",
            }
        )
        if self.proxy_url:
            session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        return session

    def login(self, record: dict, username: str, password: str) -> dict:
        self.sync_time()
        session = self.new_session()
        try:
            session.cookies.set("sessionid", secrets.token_hex(12), domain="steamcommunity.com")
            key = self.api(session, "GetPasswordRSAPublicKey", {"account_name": username}, "GET")
            try:
                public_key = rsa.RSAPublicNumbers(
                    int(key["publickey_exp"], 16), int(key["publickey_mod"], 16)
                ).public_key()
                encrypted = public_key.encrypt(password.encode(), padding.PKCS1v15())
                timestamp = key["timestamp"]
            except (KeyError, ValueError) as exc:
                raise SteamError("Steam returned an invalid password encryption key.") from exc
            begin = self.api(
                session,
                "BeginAuthSessionViaCredentials",
                {
                    "account_name": username,
                    "encrypted_password": base64.b64encode(encrypted).decode(),
                    "encryption_timestamp": timestamp,
                    "persistence": "1",
                    "platform_type": "2",
                    "website_id": "Community",
                },
            )
            if not all(begin.get(key) for key in ("client_id", "request_id", "steamid")):
                raise SteamError(
                    "Steam did not start login. Check credentials or try on Steam first."
                )
            imported_id = record["steam_data"].get("steamid") or record["steam_data"].get(
                "steam_id"
            )
            if imported_id and str(imported_id) != str(begin["steamid"]):
                raise SteamError("This Steam login does not match the imported authenticator.")
            flow = {
                "account_id": record["id"],
                "session": session,
                "begin": begin,
                "expires": time.monotonic() + 180,
            }
            try:
                methods = {
                    int(item["confirmation_type"])
                    for item in begin.get("allowed_confirmations", [])
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise SteamError("Steam returned an invalid login challenge.") from exc
            if 3 in methods:
                code = generate_code(record, time.time() + self.offset)
                self.submit_code(flow, code, 3)
            elif 2 in methods:
                token = secrets.token_urlsafe(24)
                flow["code_type"] = 2
                with self.lock:
                    self.clear_expired()
                    previous = self.pending.pop(record["id"], None)
                    if previous:
                        previous["session"].close()
                    flow["token"] = token
                    self.pending[record["id"]] = flow
                return {
                    "needs_code": True,
                    "flow": token,
                    "message": "Enter the code Steam emailed you.",
                }
            elif 1 not in methods:
                raise SteamError(
                    "Steam requires approval in its official app. Approve there, then retry."
                )
            return {"session": self.finish_login(flow)}
        finally:
            with self.lock:
                pending = self.pending.get(record["id"])
                if pending is None or pending["session"] is not session:
                    session.close()

    def clear_expired(self):
        for account_id, flow in list(self.pending.items()):
            if flow["expires"] < time.monotonic():
                flow["session"].close()
                del self.pending[account_id]

    def login_code(self, account_id: str, token: str, code: str) -> dict:
        with self.lock:
            self.clear_expired()
            flow = self.pending.get(account_id)
            if not flow or not secrets.compare_digest(flow["token"], token):
                raise SteamError("Login expired. Start Steam login again.")
            self.submit_code(flow, code, flow["code_type"])
            try:
                return self.finish_login(flow)
            finally:
                self.pending.pop(account_id, None)
                flow["session"].close()

    def submit_code(self, flow: dict, code: str, code_type: int):
        begin = flow["begin"]
        self.api(
            flow["session"],
            "UpdateAuthSessionWithSteamGuardCode",
            {
                "client_id": begin["client_id"],
                "steamid": begin["steamid"],
                "code": code,
                "code_type": str(code_type),
            },
        )

    def finish_login(self, flow: dict) -> dict:
        session, begin = flow["session"], flow["begin"]
        interval = min(max(float(begin.get("interval", 1)), 0.5), 3)
        status = {}
        for attempt in range(8):
            if attempt:
                time.sleep(interval)
            status = self.api(
                session,
                "PollAuthSessionStatus",
                {
                    "client_id": begin["client_id"],
                    "request_id": begin["request_id"],
                },
            )
            if status.get("new_client_id"):
                begin["client_id"] = status["new_client_id"]
            if status.get("refresh_token"):
                break
        if not status.get("refresh_token"):
            raise SteamError(
                "Steam login is still awaiting approval. Try again after approving it."
            )
        finalized = self.request(
            session,
            "POST",
            "https://login.steampowered.com/jwt/finalizelogin",
            data={
                "nonce": status["refresh_token"],
                "sessionid": session.cookies.get("sessionid", domain="steamcommunity.com"),
                "redir": COMMUNITY + "/login/home/?goto=",
            },
        )
        steam_id = validate_steam_id(str(finalized.get("steamID", begin["steamid"])))
        transfers = finalized.get("transfer_info")
        if not isinstance(transfers, list):
            raise SteamError("Steam did not return Community login transfer data.")
        for transfer in transfers:
            if not isinstance(transfer, dict) or not isinstance(transfer.get("url"), str):
                raise SteamError("Steam returned invalid Community login transfer data.")
            url = urlsplit(transfer["url"])
            if url.scheme == "https" and url.netloc == "steamcommunity.com":
                if not isinstance(transfer.get("params"), dict):
                    raise SteamError("Steam returned invalid Community login transfer parameters.")
                self.request(
                    session,
                    "POST",
                    transfer["url"],
                    data={
                        **transfer["params"],
                        "steamID": steam_id,
                    },
                )
        secure = session.cookies.get_dict(domain="steamcommunity.com").get("steamLoginSecure")
        if not secure:
            secure = session.cookies.get_dict(domain=".steamcommunity.com").get("steamLoginSecure")
        if not secure:
            raise SteamError("Steam login did not issue a Community session. Please retry.")
        return {"steam_id": steam_id, "steam_login_secure": secure}

    def confirmation_params(self, record: dict, steam_id: str, tag: str) -> dict:
        identity = record["steam_data"].get("identity_secret")
        if not identity:
            raise SteamError(
                "This export has no identity_secret. Re-export Steam with its full data."
            )
        timestamp = int(time.time() + self.offset)
        return {
            "p": record["device_id"] or device_id(steam_id),
            "a": steam_id,
            "k": confirmation_key(identity, timestamp, tag),
            "t": str(timestamp),
            "m": "android",
            "tag": tag,
        }

    def confirmations(
        self,
        record: dict,
        operation: str | None = None,
        confirmation_id: str | None = None,
        nonce: str | None = None,
    ) -> list[dict]:
        saved = record.get("steam_session")
        if not saved:
            raise SteamError("Connect your Steam account first.")
        self.sync_time()
        with self.new_session() as session:
            session.cookies.set(
                "steamLoginSecure",
                saved["steam_login_secure"],
                domain="steamcommunity.com",
                secure=True,
            )
            session.cookies.set("sessionid", secrets.token_hex(12), domain="steamcommunity.com")
            session.headers["X-Requested-With"] = "com.valvesoftware.android.steam.community"
            tag = operation or "conf"
            params = self.confirmation_params(record, saved["steam_id"], tag)
            path = "getlist"
            if operation:
                params.update({"op": operation, "cid": confirmation_id, "ck": nonce})
                path = "ajaxop"
            body = self.request(session, "GET", f"{COMMUNITY}/mobileconf/{path}", params=params)
        if body.get("success") is not True:
            if body.get("needauth"):
                raise SteamError("Steam session expired. Connect your Steam account again.")
            raise SteamError(
                "Steam rejected the confirmation request. Check your session and server clock."
            )
        if operation:
            return []
        confirmations = body.get("conf")
        if not isinstance(confirmations, list):
            raise SteamError("Steam did not return a confirmation list.")
        result = []
        for item in confirmations:
            if not isinstance(item, dict) or not all(item.get(key) for key in ("id", "nonce")):
                raise SteamError("Steam returned an incomplete confirmation.")
            summary = item.get("summary", [])
            if not isinstance(summary, (list, str)) or (
                isinstance(summary, list) and any(not isinstance(text, str) for text in summary)
            ):
                raise SteamError("Steam returned an invalid confirmation summary.")
            result.append(
                {
                    "id": str(item["id"]),
                    "nonce": str(item["nonce"]),
                    "headline": str(item.get("headline") or "Steam confirmation"),
                    "summary": summary,
                    "type": item.get("type"),
                    "creator_id": str(item.get("creator_id", "")),
                    "creation_time": item.get("creation_time"),
                }
            )
        return result


def cookie_session(steam_id: str, cookie: str, record: dict) -> dict:
    steam_id = validate_steam_id(steam_id)
    imported_id = record["steam_data"].get("steamid") or record["steam_data"].get("steam_id")
    if imported_id and str(imported_id) != steam_id:
        raise ValueError("SteamID does not match the imported authenticator.")
    from urllib.parse import unquote

    cookie = unquote(cookie).strip()
    if not cookie.startswith(steam_id + "||") or "\n" in cookie or "\r" in cookie:
        raise ValueError(
            "Paste the steamLoginSecure cookie value for this account, not an access token."
        )
    return {"steam_id": steam_id, "steam_login_secure": quote(cookie, safe="")}
