import copy
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from winauth_reborn.authenticators import MAX_COUNTER, export_record
from winauth_reborn.storage import Vault


def test_encrypted_restart_and_file_permissions(vault_dir, steam_record):
    vault = Vault(vault_dir)
    vault.import_records([steam_record])
    session = {"steam_id": "76561198000000000", "steam_login_secure": "test-private-cookie"}
    vault.update_session(steam_record["id"], session)
    with sqlite3.connect(vault.path) as db:
        payload = db.execute("SELECT payload FROM accounts").fetchone()[0]
    for private in [steam_record["secret"], "identity_secret", "test-private-cookie", "alice"]:
        assert private.encode() not in payload
        assert private.encode() not in vault.path.read_bytes()
    restarted = Vault(vault_dir)
    assert restarted.get(steam_record["id"]) == {**steam_record, "steam_session": session}
    assert os.stat(vault_dir).st_mode & 0o777 == 0o700
    assert os.stat(vault_dir / "vault.key").st_mode & 0o777 == 0o600
    assert os.stat(vault.path).st_mode & 0o777 == 0o600
    restarted.update_session(steam_record["id"], None)
    assert "steam_session" not in Vault(vault_dir).get(steam_record["id"])


def test_wrong_key_is_not_silently_accepted(vault_dir, totp_record):
    vault = Vault(vault_dir)
    vault.import_records([totp_record])
    (vault_dir / "vault.key").write_bytes(Fernet.generate_key())
    with pytest.raises(RuntimeError, match="matching vault.key"):
        Vault(vault_dir).list()


def test_duplicate_normalized_secret_and_batch_handling(vault_dir, totp_record):
    vault = Vault(vault_dir)
    duplicate = copy.deepcopy(totp_record)
    duplicate.update(id="duplicate", secret=duplicate["secret"].lower().rstrip("="))
    other = {**totp_record, "id": "different-account", "name": "other"}
    assert vault.import_records([totp_record, duplicate, other]) == (2, 1)
    assert vault.import_records([duplicate, other]) == (0, 2)
    assert len(vault.list()) == 2


def test_failed_batch_rolls_back_all_new_records(vault_dir, totp_record):
    vault = Vault(vault_dir)
    malformed = {**totp_record, "id": "bad", "name": "bad", "secret": "invalid!"}
    with pytest.raises(ValueError):
        vault.import_records([totp_record, malformed])
    assert Vault(vault_dir).list() == []
    conflict = {**totp_record, "name": "different"}
    with pytest.raises(sqlite3.IntegrityError):
        vault.import_records([totp_record, conflict])
    assert vault.list() == []


def test_hotp_transaction_restart_and_export(vault_dir, hotp_record):
    vault = Vault(vault_dir)
    vault.import_records([hotp_record])
    assert vault.consume_hotp(hotp_record["id"]) == "755224"
    restarted = Vault(vault_dir)
    assert restarted.get(hotp_record["id"])["counter"] == 1
    assert "counter=1" in export_record(restarted.get(hotp_record["id"]))
    assert restarted.consume_hotp(hotp_record["id"]) == "287082"
    assert vault.get(hotp_record["id"])["counter"] == 2


def test_concurrent_hotp_consumption_is_serialized(vault_dir, hotp_record):
    vault = Vault(vault_dir)
    vault.import_records([hotp_record])
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda _: vault.consume_hotp(hotp_record["id"]), range(10)))
    assert set(codes) == {
        "755224",
        "287082",
        "359152",
        "969429",
        "338314",
        "254676",
        "287922",
        "162583",
        "399871",
        "520489",
    }
    assert Vault(vault_dir).get(hotp_record["id"])["counter"] == 10


def test_hotp_failure_does_not_change_state(vault_dir, hotp_record, totp_record):
    vault = Vault(vault_dir)
    hotp_record["counter"] = MAX_COUNTER
    vault.import_records([hotp_record, totp_record])
    with pytest.raises(ValueError, match="exhausted"):
        vault.consume_hotp(hotp_record["id"])
    with pytest.raises(ValueError, match="counter-based"):
        vault.consume_hotp(totp_record["id"])
    with pytest.raises(KeyError):
        vault.consume_hotp("missing")
    assert vault.get(hotp_record["id"])["counter"] == MAX_COUNTER
    assert vault.get(totp_record["id"]) == totp_record


def test_delete_and_session_missing_account(vault_dir, totp_record):
    vault = Vault(vault_dir)
    vault.import_records([totp_record])
    vault.delete(totp_record["id"])
    assert Vault(vault_dir).list() == []
    for action in [
        lambda: vault.get("missing"),
        lambda: vault.delete("missing"),
        lambda: vault.update_session("missing", None),
    ]:
        with pytest.raises(KeyError):
            action()
