import pytest

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
