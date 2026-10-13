import base64

from conftest import SECRET

from winauth_reborn.authenticators import decode_secret


def test_recovery_details_are_explicit_csrf_protected_and_never_listed(
    app,
    client,
    auth,
    write_headers,
    steam_record,
):
    steam_record["steam_data"]["revocation_code"] = "R-SYNTHETIC-ONLY"
    steam_record["steam_data"]["serial_number"] = "synthetic-serial"
    app.extensions["vault"].import_records([steam_record])
    endpoint = f"/api/accounts/{steam_record['id']}/steam/recovery"
    assert client.post(endpoint).status_code == 401
    assert client.post(endpoint, headers=auth).status_code == 403
    assert client.get(endpoint, headers=auth).status_code == 405
    listed = client.get("/api/accounts", headers=auth)
    assert "R-SYNTHETIC-ONLY" not in listed.text and SECRET not in listed.text
    response = client.post(endpoint, headers=write_headers)
    assert response.status_code == 200
    assert response.json["recovery_code"] == "R-SYNTHETIC-ONLY"
    assert response.json["shared_secret"] == SECRET
    assert response.json["shared_secret_base64"] == base64.b64encode(decode_secret(SECRET)).decode()
    assert len(response.json["guard_code"]) == 5
    assert response.headers["Cache-Control"] == "no-store"


def test_missing_recovery_data_is_explicit_and_does_not_require_steam_login(
    app,
    client,
    write_headers,
    steam_record,
    totp_record,
):
    steam_record["steam_data"] = {}
    vault = app.extensions["vault"]
    vault.import_records([steam_record, totp_record])
    response = client.post(
        f"/api/accounts/{steam_record['id']}/steam/recovery",
        headers=write_headers,
    )
    assert response.status_code == 200 and response.json["recovery_code"] is None
    assert (
        client.post(
            f"/api/accounts/{totp_record['id']}/steam/recovery",
            headers=write_headers,
        ).status_code
        == 400
    )
