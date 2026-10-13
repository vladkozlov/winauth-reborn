import pytest

from winauth_reborn.app import create_app
from winauth_reborn.preferences import DEFAULT_SETTINGS, validate_settings
from winauth_reborn.storage import Vault


@pytest.mark.parametrize(
    "changes",
    [
        {"theme": "blue"},
        {"theme": []},
        {"density": "tiny"},
        {"sort_order": "password"},
        {"hide_codes": "false"},
        {"auto_hide_seconds": True},
        {"auto_hide_seconds": -1},
        {"auto_hide_seconds": 31},
        {"password": "not-supported"},
    ],
)
def test_invalid_preferences_are_rejected(changes):
    with pytest.raises(ValueError):
        validate_settings(changes)


def test_settings_defaults_persistence_encryption_and_partial_updates(vault_dir):
    vault = Vault(vault_dir)
    assert vault.settings() == DEFAULT_SETTINGS
    assert vault.update_settings({"theme": "light", "hide_codes": True})["theme"] == "light"
    assert vault.update_settings({"density": "compact"})["hide_codes"] is True
    restored = Vault(vault_dir)
    assert restored.settings()["density"] == "compact"
    assert b"compact" not in vault.path.read_bytes()
    with pytest.raises(ValueError):
        vault.update_settings({"theme": "light", "density": "bad"})
    assert restored.settings()["density"] == "compact"


def test_owner_preferences_api_auth_csrf_and_initial_theme(client, auth, write_headers):
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers=auth).json["settings"] == DEFAULT_SETTINGS
    assert client.patch("/api/settings", headers=auth, json={"theme": "light"}).status_code == 403
    response = client.patch(
        "/api/settings",
        headers=write_headers,
        json={
            "theme": "light",
            "sort_order": "name",
            "auto_hide_seconds": 30,
        },
    )
    assert response.status_code == 200
    assert response.json["settings"]["theme"] == "light"
    page = client.get("/", headers=auth)
    assert 'data-theme="light"' in page.text
    assert client.get("/api/accounts", headers=auth).json["settings"]["auto_hide_seconds"] == 30
    assert (
        client.patch("/api/settings", headers=write_headers, json={"theme": []}).status_code == 400
    )


def test_protected_steam_proxy_settings_are_encrypted_and_applied(client, auth, write_headers):
    assert client.get("/api/steam/proxy").status_code == 401
    initial = client.get("/api/steam/proxy", headers=auth)
    assert initial.json["proxy"] == {"mode": "environment", "active": False}
    assert client.put("/api/steam/proxy", headers=auth, json={"mode": "direct"}).status_code == 403

    proxy_url = "socks5h://steam-user:secret%40phrase@127.0.0.1:1080"
    response = client.put(
        "/api/steam/proxy",
        headers=write_headers,
        json={"mode": "custom", "url": proxy_url},
    )
    assert response.status_code == 200
    assert response.json["proxy"] == {"mode": "custom", "active": True}
    assert proxy_url not in response.get_data(as_text=True)
    assert client.application.extensions["steam"].proxy_url == proxy_url

    vault = client.application.extensions["vault"]
    assert vault.steam_proxy_override() == {"mode": "custom", "url": proxy_url}
    assert proxy_url.encode() not in vault.path.read_bytes()
    assert b"secret%40phrase" not in vault.path.read_bytes()
    assert b"secret%40phrase" not in client.get("/api/settings", headers=auth).data
    assert Vault(vault.path.parent).steam_proxy_override() == {
        "mode": "custom",
        "url": proxy_url,
    }
    restarted_app = create_app(
        {
            "TESTING": True,
            "AUTH_USERNAME": "proxy-test",
            "AUTH_PASSWORD": "test-only-proxy-password",
            "DATA_DIR": str(vault.path.parent),
            "HTTPS_ONLY": False,
        }
    )
    assert restarted_app.extensions["steam"].proxy_url == proxy_url

    assert client.put(
        "/api/steam/proxy",
        headers=write_headers,
        json={"mode": "custom", "url": ""},
    ).json["proxy"] == {"mode": "custom", "active": True}
    assert client.put(
        "/api/steam/proxy",
        headers=write_headers,
        json={"mode": "direct"},
    ).json["proxy"] == {"mode": "direct", "active": False}
    assert client.application.extensions["steam"].proxy_url is None
    server_default = "socks5h://127.0.0.2:1080"
    client.application.config["STEAM_PROXY"] = server_default
    assert client.put(
        "/api/steam/proxy",
        headers=write_headers,
        json={"mode": "environment"},
    ).json["proxy"] == {"mode": "environment", "active": True}
    assert vault.steam_proxy_override() is None
    assert client.application.extensions["steam"].proxy_url == server_default


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "custom"},
        {"mode": "custom", "url": "https://proxy.example:1080"},
        {"mode": "unexpected"},
        {"mode": []},
        {"mode": "direct", "extra": True},
        {"mode": "direct", "url": []},
    ],
)
def test_invalid_steam_proxy_settings_are_rejected(client, write_headers, data):
    assert client.put("/api/steam/proxy", headers=write_headers, json=data).status_code == 400


def test_additive_preferences_migration_preserves_existing_tokens(vault_dir, hotp_record):
    vault = Vault(vault_dir)
    vault.import_records([hotp_record])
    vault.consume_hotp(hotp_record["id"])
    with vault.connection() as db:
        db.execute("DROP TABLE settings")
    migrated = Vault(vault_dir)
    assert migrated.settings() == DEFAULT_SETTINGS
    assert migrated.get(hotp_record["id"])["counter"] == 1
    assert migrated.consume_hotp(hotp_record["id"]) == "287082"
