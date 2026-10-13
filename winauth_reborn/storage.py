from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .authenticators import MAX_COUNTER, decode_secret, edit_record, generate_code


class AccountNotFound(KeyError):
    pass


class Vault:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        key_path = directory / "vault.key"
        self.path = directory / "vault.sqlite3"
        if self.path.exists() and not key_path.exists():
            raise RuntimeError("Vault key is missing. Restore vault.key before opening this vault.")
        try:
            with key_path.open("xb") as file:
                os.chmod(key_path, 0o600)
                file.write(Fernet.generate_key())
        except FileExistsError:
            pass
        self.cipher = Fernet(key_path.read_bytes().strip())
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, payload BLOB NOT NULL)"
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def encode(self, record: dict) -> bytes:
        return self.cipher.encrypt(json.dumps(record).encode())

    def decode(self, payload: bytes) -> dict:
        try:
            return json.loads(self.cipher.decrypt(payload))
        except InvalidToken as exc:
            raise RuntimeError(
                "Vault cannot be decrypted. Restore the matching vault.key backup."
            ) from exc

    def list(self) -> list[dict]:
        with self.connection() as db:
            return [
                self.decode(row[0])
                for row in db.execute("SELECT payload FROM accounts ORDER BY rowid")
            ]

    def get(self, account_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise AccountNotFound(account_id)
        return self.decode(row[0])

    def import_records(self, records: list[dict]) -> tuple[int, int]:
        def identity(record):
            return (
                record["kind"],
                decode_secret(record["secret"]),
                record["name"],
                record["issuer"],
                record["algorithm"],
                record["digits"],
                record["period"],
            )

        added = 0
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = [self.decode(row[0]) for row in db.execute("SELECT payload FROM accounts")]
            identities = {identity(record) for record in existing}
            for record in records:
                token_identity = identity(record)
                if token_identity in identities:
                    continue
                db.execute(
                    "INSERT INTO accounts VALUES (?, ?)", (record["id"], self.encode(record))
                )
                identities.add(token_identity)
                added += 1
        return added, len(records) - added

    def update_session(self, account_id: str, session: dict | None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise AccountNotFound(account_id)
            record = self.decode(row[0])
            if session is None:
                record.pop("steam_session", None)
            else:
                record["steam_session"] = session
            db.execute(
                "UPDATE accounts SET payload = ? WHERE id = ?", (self.encode(record), account_id)
            )

    def edit(self, account_id: str, changes: dict) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise AccountNotFound(account_id)
            record = edit_record(self.decode(row[0]), changes)
            db.execute(
                "UPDATE accounts SET payload = ? WHERE id = ?", (self.encode(record), account_id)
            )
        return record

    def consume_hotp(self, account_id: str) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if row is None:
                raise AccountNotFound(account_id)
            record = self.decode(row[0])
            if record["kind"] != "hotp":
                raise ValueError("This account is not counter-based.")
            if record["counter"] == MAX_COUNTER:
                raise ValueError("HOTP counter is exhausted.")
            code = generate_code(record, 0)
            record["counter"] += 1
            db.execute(
                "UPDATE accounts SET payload = ? WHERE id = ?", (self.encode(record), account_id)
            )
        return code

    def delete(self, account_id: str):
        with self.connection() as db:
            if db.execute("DELETE FROM accounts WHERE id = ?", (account_id,)).rowcount == 0:
                raise AccountNotFound(account_id)
