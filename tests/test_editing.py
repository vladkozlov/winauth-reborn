from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import SECRET

from winauth_reborn.authenticators import export_record, generate_code, parse_export
from winauth_reborn.storage import Vault


def test_edit_name_parameters_and_export_preserve_extensions(app, client, write_headers):
    vault = app.extensions["vault"]
    record = parse_export(f"otpauth://totp/Old:alice?secret={SECRET}&custom=kept&icon=Google")[0]
    vault.import_records([record])
    changes = {
        "name": "alice+work #1",
        "issuer": "New",
        "algorithm": "SHA256",
        "digits": 8,
        "period": 45,
        "icon": "mail",
        "color": "#8ebfff",
        "pinned": True,
    }
    response = client.patch(f"/api/accounts/{record['id']}", headers=write_headers, json=changes)
    assert response.status_code == 200
    edited = vault.get(record["id"])
    assert edited["id"] == record["id"] and edited["secret"] == SECRET
    assert generate_code(edited, 59) == generate_code({**record, **changes}, 59)
    exported = export_record(edited)
    params = parse_qs(urlsplit(exported).query)
    assert params["custom"] == ["kept"] and params["icon"] == ["Google"]
    restored = parse_export(exported)[0]
    for key in changes:
        assert restored[key] == changes[key]


def test_secret_replacement_is_validated(app, client, write_headers, totp_record):
    vault = app.extensions["vault"]
    vault.import_records([totp_record])
    endpoint = f"/api/accounts/{totp_record['id']}"
    assert client.patch(endpoint, headers=write_headers, json={"secret": "!"}).status_code == 400
    assert vault.get(totp_record["id"])["secret"] == SECRET
    assert (
        client.patch(
            endpoint,
            headers=write_headers,
            json={
                "secret": "JBSWY3DPEHPK3PXP",
            },
        ).status_code
        == 200
    )
    assert (
        parse_export(export_record(vault.get(totp_record["id"])))[0]["secret"] == "JBSWY3DPEHPK3PXP"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"counter": 0},
        {"name": ""},
        {"name": 3},
        {"issuer": "bad:issuer"},
        {"issuer": "Steam"},
        {"period": 0},
        {"period": True},
        {"digits": 12},
        {"digits": "6"},
        {"algorithm": "MD5"},
        {"secret": None},
        {"icon": "custom"},
        {"color": "#xyzxyz"},
        {"pinned": 1},
        {"steam_session": {}},
    ],
)
def test_invalid_edits_are_atomic(app, client, write_headers, totp_record, changes):
    vault = app.extensions["vault"]
    vault.import_records([totp_record])
    before = vault.get(totp_record["id"])
    response = client.patch(
        f"/api/accounts/{totp_record['id']}", headers=write_headers, json=changes
    )
    assert response.status_code == 400
    assert vault.get(totp_record["id"]) == before


def test_hotp_edit_preserves_current_counter_and_restart(vault_dir, hotp_record):
    vault = Vault(vault_dir)
    vault.import_records([hotp_record])
    vault.consume_hotp(hotp_record["id"])
    vault.edit(hotp_record["id"], {"name": "renamed", "pinned": True})
    restored = Vault(vault_dir).get(hotp_record["id"])
    assert restored["counter"] == 1 and restored["pinned"] is True
    assert parse_export(export_record(restored))[0]["counter"] == 1


def test_concurrent_presentation_edit_does_not_overwrite_steam_session(vault_dir, steam_record):
    vault = Vault(vault_dir)
    vault.import_records([steam_record])
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(vault.edit, steam_record["id"], {"name": "renamed", "pinned": True}),
            pool.submit(vault.update_session, steam_record["id"], {"synthetic": "session"}),
        ]
        for task in tasks:
            task.result()
    saved = vault.get(steam_record["id"])
    assert saved["name"] == "renamed" and saved["steam_session"] == {"synthetic": "session"}


def test_steam_parameters_cannot_be_changed_but_presentation_can(
    app,
    client,
    write_headers,
    steam_record,
):
    app.extensions["vault"].import_records([steam_record])
    endpoint = f"/api/accounts/{steam_record['id']}"
    for changes in [{"secret": SECRET}, {"period": 60}, {"issuer": "other"}, {"digits": 6}]:
        assert client.patch(endpoint, headers=write_headers, json=changes).status_code == 400
    assert (
        client.patch(
            endpoint,
            headers=write_headers,
            json={
                "name": "renamed",
                "icon": "gamepad-2",
                "pinned": True,
            },
        ).status_code
        == 200
    )


def test_edit_endpoint_requires_auth_and_csrf(app, client, auth, totp_record):
    app.extensions["vault"].import_records([totp_record])
    endpoint = f"/api/accounts/{totp_record['id']}"
    assert client.patch(endpoint, json={"name": "new"}).status_code == 401
    assert client.patch(endpoint, headers=auth, json={"name": "new"}).status_code == 403
