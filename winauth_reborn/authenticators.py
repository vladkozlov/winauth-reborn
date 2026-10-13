from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
import uuid
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from .appearance import validate_presentation

STEAM_ALPHABET = "23456789BCDFGHJKMNPQRTVWXY"
MAX_COUNTER = (1 << 64) - 1


def decode_secret(secret: str) -> bytes:
    normalized = secret.replace(" ", "").upper().rstrip("=")
    try:
        decoded = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Secret must be valid Base32.") from exc
    if not decoded:
        raise ValueError("Secret cannot be empty.")
    return decoded


def parse_export(text: str) -> list[dict]:
    if text.lstrip().startswith("-----BEGIN PGP"):
        raise ValueError(
            "This export is PGP-encrypted. Decrypt it locally before importing the .txt."
        )
    records = []
    for number, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            # Older WinAuth exports can contain an unescaped # in the account label.
            label_part, separator, query_part = line.partition("?")
            uri = urlsplit(label_part.replace("#", "%23") + separator + query_part)
            if uri.scheme != "otpauth" or uri.netloc not in {"totp", "hotp"}:
                raise ValueError("Expected an otpauth://totp/ or otpauth://hotp/ URI.")
            if uri.fragment:
                raise ValueError("Unexpected URI fragment; encode # as %23 in query values.")
            pairs = parse_qsl(uri.query, keep_blank_values=True)
            params = dict(pairs)
            if len(params) != len(pairs):
                raise ValueError("Duplicate query parameters are not supported.")
            decode_secret(params.get("secret", ""))
            label = unquote(uri.path.lstrip("/"))
            prefix, colon, account = label.partition(":")
            issuer = params.get("issuer") or (prefix if colon else "")
            name = account if colon else label
            kind = "steam" if issuer.lower() == "steam" else uri.netloc
            algorithm = params.get("algorithm", "SHA1").upper()
            if algorithm not in {"SHA1", "SHA256", "SHA512"}:
                raise ValueError("Algorithm must be SHA1, SHA256, or SHA512.")
            digits = int(params.get("digits", "8" if issuer.lower() == "battlenet" else "6"))
            if not (5 <= digits <= 10):
                raise ValueError("Digits must be between 5 and 10.")
            period = int(params.get("period", "30"))
            if not (1 <= period <= 86400):
                raise ValueError("Period must be between 1 and 86400 seconds.")
            counter = int(params.get("counter", "0"))
            if uri.netloc == "hotp" and "counter" not in params:
                raise ValueError("HOTP requires a counter.")
            if not (0 <= counter <= MAX_COUNTER):
                raise ValueError("Counter must be an unsigned 64-bit integer.")
            steam_data = {}
            if kind == "steam":
                if uri.netloc != "totp" or algorithm != "SHA1":
                    raise ValueError("Steam requires TOTP with SHA1.")
                steam_data = json.loads(params.get("data") or "{}")
                if not isinstance(steam_data, dict):
                    raise ValueError("Steam data must be a JSON object.")
                identity = steam_data.get("identity_secret")
                if identity is not None and (
                    not isinstance(identity, str) or not base64.b64decode(identity, validate=True)
                ):
                    raise ValueError("Steam identity_secret must be non-empty Base64.")
            records.append(
                {
                    "id": uuid.uuid4().hex,
                    "name": name or issuer or "Imported",
                    "issuer": issuer,
                    "kind": kind,
                    "secret": params["secret"],
                    "algorithm": algorithm,
                    "digits": 5 if kind == "steam" else digits,
                    "period": period,
                    "counter": counter,
                    "uri": urlunsplit(uri),
                    "steam_data": steam_data,
                    "device_id": params.get("deviceid", ""),
                }
            )
            presentation = {}
            for key in ("icon", "color", "custom_icon"):
                if f"x-reborn-{key}" in params:
                    presentation[key] = params[f"x-reborn-{key}"]
            if "x-reborn-pinned" in params:
                if params["x-reborn-pinned"] not in {"true", "false"}:
                    raise ValueError("Invalid pinned value.")
                presentation["pinned"] = params["x-reborn-pinned"] == "true"
            records[-1].update(validate_presentation(presentation))
            if records[-1].get("icon") == "custom" and not records[-1].get("custom_icon"):
                raise ValueError("Custom icon is missing its image.")
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Line {number}: {exc}") from exc
    if not records:
        raise ValueError("No authenticators found in this file.")
    if len(records) > 1000:
        raise ValueError("Import at most 1000 authenticators at a time.")
    return records


def generate_code(record: dict, timestamp: float) -> str:
    counter = record["counter"] if record["kind"] == "hotp" else int(timestamp // record["period"])
    digest = hmac.new(
        decode_secret(record["secret"]),
        struct.pack(">Q", counter),
        getattr(hashlib, record["algorithm"].lower()),
    ).digest()
    offset = digest[-1] & 15
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    if record["kind"] == "steam":
        code = ""
        for _ in range(5):
            code += STEAM_ALPHABET[value % len(STEAM_ALPHABET)]
            value //= len(STEAM_ALPHABET)
        return code
    return str(value % (10 ** record["digits"])).zfill(record["digits"])


def export_record(record: dict) -> str:
    uri = urlsplit(record["uri"])
    params = dict(parse_qsl(uri.query, keep_blank_values=True))
    if record["kind"] == "hotp":
        params["counter"] = str(record["counter"])
    for key in ("icon", "color", "custom_icon", "pinned"):
        if key in record:
            value = record[key]
            params[f"x-reborn-{key}"] = str(value).lower() if key == "pinned" else value
    return urlunsplit(uri._replace(query=urlencode(params)))


def edit_record(record: dict, changes: dict) -> dict:
    allowed = {
        "name",
        "issuer",
        "secret",
        "algorithm",
        "digits",
        "period",
        "icon",
        "color",
        "custom_icon",
        "pinned",
    }
    if not changes or changes.keys() - allowed:
        raise ValueError("Provide supported account fields to update.")
    changes = validate_presentation(dict(changes))
    for key in ("name", "issuer"):
        if key in changes:
            value = changes[key]
            if (
                not isinstance(value, str)
                or len(value) > 200
                or (key == "name" and not value.strip())
            ):
                raise ValueError(f"{key} must be text of at most 200 characters.")
            changes[key] = value.strip()
            if key == "issuer" and ":" in changes[key]:
                raise ValueError("Issuer must not contain a colon.")
    if "secret" in changes:
        if not isinstance(changes["secret"], str):
            raise ValueError("Secret must be Base32 text.")
        decode_secret(changes["secret"])
    for key in ("digits", "period"):
        if key in changes and type(changes[key]) is not int:
            raise ValueError(f"{key} must be an integer.")
    if "algorithm" in changes and not isinstance(changes["algorithm"], str):
        raise ValueError("Algorithm must be text.")
    if record["kind"] == "steam":
        if changes.get("issuer", record["issuer"]).lower() != "steam":
            raise ValueError("A Steam authenticator's issuer must remain Steam.")
        if changes.keys() & {"secret", "algorithm", "digits", "period"}:
            raise ValueError(
                "Steam key parameters are read-only. Import a full export to replace them."
            )
    elif changes.get("issuer", record["issuer"]).lower() == "steam":
        raise ValueError("Changing an ordinary account into Steam is not supported.")
    edited = {**record, **changes}
    if edited.get("icon") == "custom" and not edited.get("custom_icon"):
        raise ValueError("Upload an image before selecting the custom icon.")
    uri = urlsplit(record["uri"])
    params = dict(parse_qsl(uri.query, keep_blank_values=True))
    params.update(
        {
            "secret": edited["secret"],
            "issuer": edited["issuer"],
            "algorithm": edited["algorithm"],
            "digits": str(edited["digits"]),
            "period": str(edited["period"]),
        }
    )
    if record["kind"] == "hotp":
        params["counter"] = str(record["counter"])
    label = (quote(edited["issuer"], safe="") + ":" if edited["issuer"] else "") + quote(
        edited["name"], safe=""
    )
    edited["uri"] = urlunsplit(uri._replace(path="/" + label, query=urlencode(params)))
    validated = parse_export(edited["uri"])[0]
    if validated["kind"] != record["kind"]:
        raise ValueError("Account type cannot be changed.")
    for key in ("secret", "algorithm", "digits", "period"):
        edited[key] = validated[key]
    return edited


def validate_steam_id(value: str) -> str:
    if not re.fullmatch(r"7656119\d{10}", value):
        raise ValueError("A valid 17-digit SteamID64 is required.")
    return value
