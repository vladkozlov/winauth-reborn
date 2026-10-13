import base64
import json
import re
import shutil
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests

from winauth_reborn.app import create_app
from winauth_reborn.authenticators import parse_export

SECRET = base64.b32encode(b"12345678901234567890").decode()
IDENTITY = base64.b64encode(b"12345678901234567890").decode()
STEAM_ID = "76561198000000000"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", reject)


@pytest.fixture
def vault_dir():
    root = Path(__file__).parent / ".test-data"
    directory = root / uuid.uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
        with suppress(OSError):
            root.rmdir()


@pytest.fixture
def totp_record():
    return parse_export(f"otpauth://totp/Example:alice?secret={SECRET}&issuer=Example")[0]


@pytest.fixture
def hotp_record():
    return parse_export(f"otpauth://hotp/Example:bob?secret={SECRET}&counter=0")[0]


@pytest.fixture
def steam_record():
    params = {
        "secret": SECRET,
        "issuer": "Steam",
        "data": json.dumps({"identity_secret": IDENTITY, "steamid": STEAM_ID}),
    }
    return parse_export("otpauth://totp/Steam:alice?" + urlencode(params))[0]


@pytest.fixture
def app(vault_dir):
    return create_app(
        {
            "TESTING": True,
            "AUTH_USERNAME": "test-admin",
            "AUTH_PASSWORD": "test-password-only",
            "AUTH_PASSWORD_HASH": "",
            "DATA_DIR": str(vault_dir),
            "HTTPS_ONLY": True,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    value = base64.b64encode(b"test-admin:test-password-only").decode()
    return {"Authorization": "Basic " + value}


@pytest.fixture
def write_headers(client, auth):
    response = client.get("/", headers=auth)
    token = re.search(r'name="csrf-token" content="([^"]+)"', response.text).group(1)
    return {**auth, "X-CSRF-Token": token}
