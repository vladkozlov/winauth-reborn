import base64
from unittest.mock import Mock

import pytest
import requests
from conftest import IDENTITY, STEAM_ID
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from winauth_reborn.steam import (
    API,
    COMMUNITY,
    SteamError,
    SteamService,
    confirmation_key,
    cookie_session,
    device_id,
)


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("fixture HTTP failure")

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class ScriptedSession:
    def __init__(self, steps=()):
        self.steps = list(steps)
        self.calls = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def request(self, method, url, **kwargs):
        assert self.steps, f"Unexpected request: {method} {url}"
        expected_method, suffix, result = self.steps.pop(0)
        assert method == expected_method
        assert url.endswith(suffix)
        assert kwargs["timeout"] == (5, 20)
        assert kwargs["allow_redirects"] is False
        self.calls.append((method, url, kwargs))
        if callable(result):
            result = result(self, kwargs)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Response) else Response(result)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr("winauth_reborn.steam.time.time", lambda: 1700000000.0)
    monkeypatch.setattr("winauth_reborn.steam.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("winauth_reborn.steam.time.sleep", lambda _: None)
    monkeypatch.setattr("winauth_reborn.steam.secrets.token_hex", lambda _: "test-session-id")
    monkeypatch.setattr("winauth_reborn.steam.secrets.token_urlsafe", lambda _: "test-flow-token")


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=1024)


def rsa_response(private_key):
    numbers = private_key.public_key().public_numbers()
    return {
        "response": {
            "publickey_exp": format(numbers.e, "x"),
            "publickey_mod": format(numbers.n, "x"),
            "timestamp": "test-rsa-timestamp",
        },
    }


def auth_response(method=3, **extra):
    return {
        "response": {
            "client_id": "client-1",
            "request_id": "request-1",
            "steamid": STEAM_ID,
            "interval": 0.5,
            "allowed_confirmations": [{"confirmation_type": method}],
            **extra,
        }
    }


def community_transfer(session, kwargs):
    assert kwargs["data"] == {"auth": "fixture-transfer", "steamID": STEAM_ID}
    session.cookies.set(
        "steamLoginSecure",
        STEAM_ID + "%7C%7Cfixture-secure-cookie",
        domain=".steamcommunity.com",
        secure=True,
    )
    return {"result": 1}


def final_steps():
    return [
        (
            "POST",
            "PollAuthSessionStatus/v1/",
            {"response": {"refresh_token": "test-refresh-token"}},
        ),
        (
            "POST",
            "/jwt/finalizelogin",
            {
                "steamID": STEAM_ID,
                "transfer_info": [
                    {"url": "https://store.steampowered.com/login/settoken", "params": {}},
                    {"url": "https://steamcommunity.com.evil.invalid/login/settoken", "params": {}},
                    {"url": "http://steamcommunity.com/login/settoken", "params": {}},
                    {"url": COMMUNITY + "/login/settoken", "params": {"auth": "fixture-transfer"}},
                ],
            },
        ),
        ("POST", "/login/settoken", community_transfer),
    ]


def setup_login(monkeypatch, session):
    service = SteamService()
    monkeypatch.setattr(service, "sync_time", lambda: None)
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    return service


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("conf", "uOi1VQCWymi7kGGUQW8vh0odoS0="),
        ("allow", "+DyBq65u5ZJKRi75PrLwE9vklmc="),
        ("cancel", "OQ1xLzrvr/OZek6cDLIZDLuPyZE="),
        ("x" * 40, "kfOKv8Oe1yA0Gf0coNnfSnblV9Y="),
    ],
)
def test_independently_derived_confirmation_key_vectors(tag, expected):
    assert confirmation_key(IDENTITY, 1700000000, tag) == expected


def test_device_id_fixture():
    assert device_id(STEAM_ID) == "android:5c9df5a2-d7de-1e2c-8fc8-766523ca130f"


def test_modern_login_rsa_guard_poll_finalize_community_transfer(
    monkeypatch,
    steam_record,
    rsa_key,
    clock,
):
    def check_begin(session, kwargs):
        data = kwargs["data"]
        encrypted = base64.b64decode(data["encrypted_password"])
        assert rsa_key.decrypt(encrypted, padding.PKCS1v15()) == b"fixture-password"
        assert data["account_name"] == "fixture-user"
        assert data["encryption_timestamp"] == "test-rsa-timestamp"
        assert data["website_id"] == "Community"
        assert data["platform_type"] == "2"
        assert data["persistence"] == "1"
        return auth_response()

    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", check_begin),
            ("POST", "UpdateAuthSessionWithSteamGuardCode/v1/", {"response": {}}),
            ("POST", "PollAuthSessionStatus/v1/", {"response": {"new_client_id": "client-2"}}),
            *final_steps(),
        ]
    )
    service = setup_login(monkeypatch, session)
    result = service.login(steam_record, "fixture-user", "fixture-password")
    assert result == {
        "session": {
            "steam_id": STEAM_ID,
            "steam_login_secure": STEAM_ID + "%7C%7Cfixture-secure-cookie",
        }
    }
    assert session.calls[0][2]["params"] == {"account_name": "fixture-user"}
    guard = session.calls[2][2]["data"]
    assert guard["steamid"] == STEAM_ID
    assert guard["code_type"] == "3"
    assert len(guard["code"]) == 5
    assert session.calls[4][2]["data"]["client_id"] == "client-2"
    assert session.calls[5][2]["data"]["nonce"] == "test-refresh-token"
    assert session.calls[5][2]["data"]["sessionid"] == "test-session-id"
    assert session.headers["Origin"] == COMMUNITY
    assert session.steps == []
    assert session.closed is True
    assert service.pending == {}


def test_email_challenge_preserves_flow_until_code(
    monkeypatch,
    steam_record,
    rsa_key,
    clock,
):
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", auth_response(2)),
            ("POST", "UpdateAuthSessionWithSteamGuardCode/v1/", {"response": {}}),
            *final_steps(),
        ]
    )
    service = setup_login(monkeypatch, session)
    challenge = service.login(steam_record, "fixture-user", "fixture-password")
    assert challenge["needs_code"] is True
    assert challenge["flow"] == "test-flow-token"
    assert "session" not in challenge
    assert session.closed is False
    assert steam_record["id"] in service.pending
    with pytest.raises(SteamError, match="expired"):
        service.login_code(steam_record["id"], "wrong-flow", "ABCDE")
    assert len(session.calls) == 2
    saved = service.login_code(steam_record["id"], challenge["flow"], "ABCDE")
    assert saved["steam_id"] == STEAM_ID
    assert session.calls[2][2]["data"]["code"] == "ABCDE"
    assert session.calls[2][2]["data"]["code_type"] == "2"
    assert session.closed
    assert service.pending == {}


def test_enrollment_login_sms_activation_and_vault_record(
    monkeypatch,
    rsa_key,
    clock,
):
    shared_secret = base64.b64encode(b"12345678901234567890").decode()
    final = final_steps()
    final[0] = (
        "POST",
        "PollAuthSessionStatus/v1/",
        {
            "response": {
                "refresh_token": "test-refresh-token",
                "access_token": "test-access-token",
            }
        },
    )
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", auth_response(2)),
            ("POST", "UpdateAuthSessionWithSteamGuardCode/v1/", {"response": {}}),
            *final,
            (
                "POST",
                "AddAuthenticator/v1/",
                {
                    "response": {
                        "shared_secret": shared_secret,
                        "identity_secret": IDENTITY,
                        "serial_number": "serial-fixture",
                        "revocation_code": "R-SYNTHETIC",
                        "server_time": "1700000000",
                        "account_name": "fixture-user",
                        "phone_number_hint": "***1234",
                    }
                },
            ),
            (
                "POST",
                "FinalizeAddAuthenticator/v1/",
                {"response": {"success": True}},
            ),
        ]
    )
    service = setup_login(monkeypatch, session)
    challenge = service.start_enrollment("fixture-user", "fixture-password")
    assert challenge["needs_code"] is True
    enrollment = service.continue_enrollment(challenge["flow"], "EMAIL1")
    assert enrollment["needs_activation"] is True
    assert enrollment["phone_hint"] == "***1234"

    vault = Mock()
    vault.import_records.return_value = (1, 0)
    result = service.activate_enrollment(challenge["flow"], "SMS123", vault)
    assert result == {
        "added": True,
        "account_name": "fixture-user",
        "recovery_code": "R-SYNTHETIC",
    }
    record = vault.import_records.call_args.args[0][0]
    assert record["kind"] == "steam"
    assert record["digits"] == 5
    assert record["steam_data"]["identity_secret"] == IDENTITY
    assert record["steam_data"]["revocation_code"] == "R-SYNTHETIC"
    assert record["steam_session"]["steam_id"] == STEAM_ID
    assert "access_token" not in record
    assert session.calls[-1][2]["data"]["activation_code"] == "SMS123"
    assert len(session.calls[-1][2]["data"]["authenticator_code"]) == 5
    assert session.calls[-1][2]["data"]["authenticator_time"] == "1700000000000"
    assert session.closed is True
    assert service.pending_enrollments == {}


def test_expired_email_flow_closes_session(monkeypatch, steam_record, rsa_key, clock):
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", auth_response(2)),
        ]
    )
    service = setup_login(monkeypatch, session)
    challenge = service.login(steam_record, "fixture-user", "fixture-password")
    monkeypatch.setattr("winauth_reborn.steam.time.monotonic", lambda: 1181.0)
    with pytest.raises(SteamError, match="expired"):
        service.login_code(steam_record["id"], challenge["flow"], "ABCDE")
    assert session.closed
    assert service.pending == {}


@pytest.mark.parametrize(
    "begin,message",
    [
        (auth_response(4), "official app"),
        ({"response": {}}, "did not start login"),
        (auth_response(3, steamid="76561198000000001"), "does not match"),
    ],
)
def test_login_rejects_unsupported_incomplete_and_mismatched_accounts(
    monkeypatch,
    steam_record,
    rsa_key,
    clock,
    begin,
    message,
):
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", begin),
        ]
    )
    service = setup_login(monkeypatch, session)
    with pytest.raises(SteamError, match=message):
        service.login(steam_record, "fixture-user", "fixture-password")
    assert session.closed


def test_login_without_guard_and_no_community_cookie_fails(
    monkeypatch,
    steam_record,
    rsa_key,
    clock,
):
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            ("POST", "BeginAuthSessionViaCredentials/v1/", auth_response(1)),
            ("POST", "PollAuthSessionStatus/v1/", {"response": {"refresh_token": "test-refresh"}}),
            ("POST", "/jwt/finalizelogin", {"steamID": STEAM_ID, "transfer_info": []}),
        ]
    )
    service = setup_login(monkeypatch, session)
    with pytest.raises(SteamError, match="Community session"):
        service.login(steam_record, "fixture-user", "fixture-password")
    assert session.closed


def test_polling_has_bounded_attempts(monkeypatch, clock):
    session = ScriptedSession(
        [("POST", "PollAuthSessionStatus/v1/", {"response": {}}) for _ in range(8)]
    )
    service = SteamService()
    with pytest.raises(SteamError, match="awaiting approval"):
        service.finish_login(
            {
                "session": session,
                "begin": {"client_id": "fixture", "request_id": "fixture", "interval": 100},
            }
        )
    assert len(session.calls) == 8


@pytest.mark.parametrize(
    "key",
    [
        {"response": {}},
        {"response": {"publickey_exp": "invalid", "publickey_mod": "bad", "timestamp": "1"}},
    ],
)
def test_malformed_rsa_key_is_steam_error(monkeypatch, steam_record, clock, key):
    session = ScriptedSession([("GET", "GetPasswordRSAPublicKey/v1/", key)])
    service = setup_login(monkeypatch, session)
    with pytest.raises(SteamError, match="encryption key"):
        service.login(steam_record, "fixture-user", "fixture-password")
    assert session.closed


def test_time_sync_midpoint_and_cache(monkeypatch):
    times = iter([100.0, 104.0])
    monotonic = [1000.0]
    monkeypatch.setattr("winauth_reborn.steam.time.time", lambda: next(times))
    monkeypatch.setattr("winauth_reborn.steam.time.monotonic", lambda: monotonic[0])
    session = ScriptedSession(
        [
            ("POST", "QueryTime/v1/", {"response": {"server_time": "110"}}),
        ]
    )
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.sync_time()
    assert service.offset == 8
    assert service.synced_at == 1000
    monotonic[0] = 1299
    service.sync_time()
    assert len(session.calls) == 1
    assert session.calls[0][1] == API + "/ITwoFactorService/QueryTime/v1/"
    assert session.closed


def test_time_sync_missing_server_time(monkeypatch, clock):
    session = ScriptedSession([("POST", "QueryTime/v1/", {"response": {}})])
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    with pytest.raises(SteamError, match="server time"):
        SteamService().sync_time()


def test_first_time_sync_is_not_skipped_on_recent_system_boot(monkeypatch):
    monkeypatch.setattr("winauth_reborn.steam.time.time", lambda: 100.0)
    monkeypatch.setattr("winauth_reborn.steam.time.monotonic", lambda: 10.0)
    session = ScriptedSession(
        [
            ("POST", "QueryTime/v1/", {"response": {"server_time": "110"}}),
        ]
    )
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.sync_time()
    assert service.offset == 10
    assert len(session.calls) == 1


def test_confirmation_list_json_and_identity_signature(monkeypatch, steam_record, clock):
    body = {
        "success": True,
        "conf": [
            {
                "id": 123,
                "nonce": 456,
                "headline": "Fixture trade",
                "summary": ["Test item"],
                "type": 2,
                "creator_id": 789,
                "creation_time": 1699999999,
            }
        ],
    }
    session = ScriptedSession([("GET", "/mobileconf/getlist", body)])
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.synced_at = 1000
    steam_record["steam_session"] = {
        "steam_id": STEAM_ID,
        "steam_login_secure": "fixture-cookie",
    }
    result = service.confirmations(steam_record)
    assert result == [
        {
            "id": "123",
            "nonce": "456",
            "headline": "Fixture trade",
            "summary": ["Test item"],
            "type": 2,
            "creator_id": "789",
            "creation_time": 1699999999,
        }
    ]
    params = session.calls[0][2]["params"]
    assert params == {
        "p": "android:5c9df5a2-d7de-1e2c-8fc8-766523ca130f",
        "a": STEAM_ID,
        "k": "uOi1VQCWymi7kGGUQW8vh0odoS0=",
        "t": "1700000000",
        "m": "android",
        "tag": "conf",
    }
    assert session.cookies.get("steamLoginSecure", domain="steamcommunity.com") == "fixture-cookie"
    assert session.headers["X-Requested-With"] == "com.valvesoftware.android.steam.community"
    assert session.closed


@pytest.mark.parametrize(
    "operation,key",
    [
        ("allow", "+DyBq65u5ZJKRi75PrLwE9vklmc="),
        ("cancel", "OQ1xLzrvr/OZek6cDLIZDLuPyZE="),
    ],
)
def test_confirmation_operations_sign_the_operation_tag(
    monkeypatch,
    steam_record,
    clock,
    operation,
    key,
):
    session = ScriptedSession([("GET", "/mobileconf/ajaxop", {"success": True})])
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.synced_at = 1000
    steam_record["device_id"] = "android:fixture-imported"
    steam_record["steam_session"] = {
        "steam_id": STEAM_ID,
        "steam_login_secure": "fixture-cookie",
    }
    assert service.confirmations(steam_record, operation, "123", "456") == []
    params = session.calls[0][2]["params"]
    assert params["p"] == "android:fixture-imported"
    assert params["tag"] == params["op"] == operation
    assert params["k"] == key
    assert (params["cid"], params["ck"]) == ("123", "456")


@pytest.mark.parametrize(
    "body,message",
    [
        ({"success": False, "needauth": True}, "expired"),
        ({"success": False}, "rejected"),
        ({"success": True, "conf": {}}, "confirmation list"),
    ],
)
def test_confirmation_errors(monkeypatch, steam_record, clock, body, message):
    session = ScriptedSession([("GET", "/mobileconf/getlist", body)])
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.synced_at = 1000
    steam_record["steam_session"] = {
        "steam_id": STEAM_ID,
        "steam_login_secure": "fixture-cookie",
    }
    with pytest.raises(SteamError, match=message):
        service.confirmations(steam_record)
    assert session.closed


@pytest.mark.parametrize("entry", [{}, None, {"id": "123"}, "invalid"])
def test_malformed_confirmation_entries_are_reported_as_steam_errors(
    monkeypatch,
    steam_record,
    clock,
    entry,
):
    session = ScriptedSession(
        [
            ("GET", "/mobileconf/getlist", {"success": True, "conf": [entry]}),
        ]
    )
    monkeypatch.setattr("winauth_reborn.steam.requests.Session", lambda: session)
    service = SteamService()
    service.synced_at = 1000
    steam_record["steam_session"] = {
        "steam_id": STEAM_ID,
        "steam_login_secure": "fixture-cookie",
    }
    with pytest.raises(SteamError):
        service.confirmations(steam_record)


def test_confirmation_missing_session_and_identity(steam_record, clock):
    service = SteamService()
    with pytest.raises(SteamError, match="Connect"):
        service.confirmations(steam_record)
    steam_record["steam_data"].pop("identity_secret")
    with pytest.raises(SteamError, match="identity_secret"):
        service.confirmation_params(steam_record, STEAM_ID, "conf")


def test_steam_sessions_use_configured_socks_proxy():
    service = SteamService("socks5h://127.0.0.1:1080")
    session = service.new_session()
    try:
        assert session.proxies == {
            "http": "socks5h://127.0.0.1:1080",
            "https": "socks5h://127.0.0.1:1080",
        }
    finally:
        session.close()


@pytest.mark.parametrize(
    "proxy_url",
    [
        "http://127.0.0.1:1080",
        "socks5://127.0.0.1:1080",
        "socks5h://127.0.0.1",
        "socks5h:///1080",
        "socks5h://127.0.0.1:bad",
        "socks5h://127.0.0.1:1080/path",
        "socks5h://:password@127.0.0.1:1080",
        "socks5h://username:@127.0.0.1:1080",
    ],
)
def test_steam_proxy_rejects_unsupported_or_malformed_urls(proxy_url):
    with pytest.raises(ValueError, match="Steam proxy must"):
        SteamService(proxy_url)


@pytest.mark.parametrize(
    "response,message",
    [
        (Response({}, status=302), "expired"),
        (Response({}, status=500), "could not be reached"),
        (Response([], status=200), "unexpected response"),
        (Response({}, headers={"X-eresult": "5"}), "result 5"),
        (Response(requests.exceptions.JSONDecodeError("invalid", "", 0)), "invalid data"),
        (requests.Timeout("fixture timeout"), "could not be reached"),
    ],
)
def test_steam_request_errors(response, message):
    session = ScriptedSession([("GET", "/fixture", response)])
    with pytest.raises(SteamError, match=message):
        SteamService.request(session, "GET", COMMUNITY + "/fixture")


def test_steam_request_failure_logs_sanitized_upstream_details(caplog):
    session = ScriptedSession(
        [
            (
                "GET",
                "/mobileconf/getlist?key=secret-signature",
                Response({}, status=403, headers={"Content-Type": "text/html"}),
            ),
        ]
    )
    with pytest.raises(SteamError, match="could not be reached"):
        SteamService.request(
            session,
            "GET",
            COMMUNITY + "/mobileconf/getlist?key=secret-signature",
        )
    assert "method=GET host=steamcommunity.com path=/mobileconf/getlist" in caplog.text
    assert "error=HTTPError status=403 content_type=text/html" in caplog.text
    assert "secret-signature" not in caplog.text


@pytest.mark.parametrize("result", ["1", "29"])
def test_steam_request_allowed_results(result):
    session = ScriptedSession(
        [
            ("GET", "/fixture", Response({"ok": True}, headers={"X-eresult": result})),
        ]
    )
    assert SteamService.request(session, "GET", COMMUNITY + "/fixture") == {"ok": True}


def test_auth_api_requires_response_object():
    session = ScriptedSession([("POST", "/Fixture/v1/", {"response": []})])
    with pytest.raises(SteamError, match="missing required data"):
        SteamService().api(session, "Fixture", {})


@pytest.mark.parametrize(
    "cookie",
    [
        STEAM_ID + "||fixture-token",
        STEAM_ID + "%7C%7Cfixture-token",
    ],
)
def test_cookie_fallback_accepts_raw_and_encoded(steam_record, cookie):
    assert cookie_session(STEAM_ID, cookie, steam_record) == {
        "steam_id": STEAM_ID,
        "steam_login_secure": STEAM_ID + "%7C%7Cfixture-token",
    }


@pytest.mark.parametrize(
    "steam_id,cookie",
    [
        ("invalid", "fixture-cookie"),
        ("76561198000000001", "76561198000000001||fixture"),
        (STEAM_ID, "bare-access-token"),
        (STEAM_ID, STEAM_ID + "||token\ninjected"),
        (STEAM_ID, STEAM_ID + "%7C%7Ctoken%0Dinjected"),
    ],
)
def test_cookie_fallback_rejects_bad_account_and_header_injection(
    steam_record,
    steam_id,
    cookie,
):
    with pytest.raises(ValueError):
        cookie_session(steam_id, cookie, steam_record)


@pytest.mark.parametrize(
    "confirmations",
    [
        [{}],
        [{"confirmation_type": "not-an-integer"}],
        None,
        "invalid",
    ],
)
def test_malformed_login_confirmation_methods_are_steam_errors(
    monkeypatch,
    steam_record,
    rsa_key,
    clock,
    confirmations,
):
    session = ScriptedSession(
        [
            ("GET", "GetPasswordRSAPublicKey/v1/", rsa_response(rsa_key)),
            (
                "POST",
                "BeginAuthSessionViaCredentials/v1/",
                auth_response(allowed_confirmations=confirmations),
            ),
        ]
    )
    service = setup_login(monkeypatch, session)
    with pytest.raises(SteamError):
        service.login(steam_record, "fixture-user", "fixture-password")
    assert session.closed
