import base64
import json
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from conftest import IDENTITY, SECRET, STEAM_ID

from winauth_reborn.authenticators import (
    MAX_COUNTER,
    decode_secret,
    export_record,
    generate_code,
    parse_export,
)

RFC6238 = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize(
    "algorithm,size,index",
    [
        ("SHA1", 20, 1),
        ("SHA256", 32, 2),
        ("SHA512", 64, 3),
    ],
)
@pytest.mark.parametrize("vector", RFC6238)
def test_rfc6238_vectors(algorithm, size, index, vector):
    secret = base64.b32encode((b"1234567890" * 7)[:size]).decode()
    record = parse_export(f"otpauth://totp/RFC?secret={secret}&algorithm={algorithm}&digits=8")[0]
    assert generate_code(record, vector[0]) == vector[index]


@pytest.mark.parametrize(
    "counter,expected",
    list(
        enumerate(
            [
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
            ]
        )
    ),
)
def test_rfc4226_vectors_are_counter_not_time_based(hotp_record, counter, expected):
    hotp_record["counter"] = counter
    assert generate_code(hotp_record, 0) == expected
    assert generate_code(hotp_record, 2000000000) == expected
    assert hotp_record["counter"] == counter


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (0, "GG5F5"),
        (59, "PV9M4"),
        (1111111109, "PY4YB"),
        (2000000000, "9N776"),
    ],
)
def test_independent_steam_guard_vectors(steam_record, timestamp, expected):
    assert generate_code(steam_record, timestamp) == expected


def test_totp_period_boundary_and_custom_digits(totp_record):
    totp_record.update(period=60, digits=8)
    assert generate_code(totp_record, 59.999) == "84755224"
    assert generate_code(totp_record, 60) == "94287082"


def test_faithful_winauth_import_and_export():
    data = {"identity_secret": IDENTITY, "steamid": STEAM_ID, "revocation_code": "TEST-ONLY"}
    steam_params = {
        "secret": SECRET,
        "issuer": "Steam",
        "deviceid": "android:imported-device",
        "data": json.dumps(data),
        "vendor-extra": "a+b & # café",
        "blank": "",
    }
    text = (
        "\ufeff# WinAuth export\r\n\r\n"
        f"otpauth://totp/Acme%20Corp:alice%2Btest%40example.com?secret={SECRET}"
        "&algorithm=sha256&period=45&digits=7&issuer=Acme+Corp&custom=preserve\r\n"
        f"otpauth://totp/Steam:player#123?{urlencode(steam_params)}\r\n"
        f"otpauth://totp/BattleNet:hero?secret={SECRET}&serial=US-1234-5678\r\n"
        f"otpauth://hotp/Counter:alice?secret={SECRET}&counter=7&opaque=yes\r\n"
    )
    records = parse_export(text)
    assert len(records) == 4
    acme, steam, battle, hotp = records
    assert (acme["name"], acme["issuer"], acme["algorithm"], acme["period"], acme["digits"]) == (
        "alice+test@example.com",
        "Acme Corp",
        "SHA256",
        45,
        7,
    )
    assert steam["name"] == "player#123"
    assert steam["kind"] == "steam"
    assert steam["digits"] == 5
    assert steam["steam_data"] == data
    assert steam["device_id"] == "android:imported-device"
    assert battle["digits"] == 8
    hotp["counter"] = 12
    exported = [export_record(record) for record in records]
    roundtrip = parse_export("\n".join(exported))
    assert [r["name"] for r in roundtrip] == [r["name"] for r in records]
    assert roundtrip[1]["steam_data"] == data
    params = [dict(parse_qsl(urlsplit(uri).query, keep_blank_values=True)) for uri in exported]
    assert params[0]["custom"] == "preserve"
    assert params[1]["vendor-extra"] == "a+b & # café"
    assert params[1]["blank"] == ""
    assert params[2]["serial"] == "US-1234-5678"
    assert params[3]["opaque"] == "yes"
    assert params[3]["counter"] == "12"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# comments only",
        "-----BEGIN PGP MESSAGE-----\nsecret",
        "https://example.invalid/",
        "otpauth://totp/name?secret=",
        "otpauth://totp/name?secret=NOT-BASE32",
        f"otpauth://totp/name?secret={SECRET}&secret={SECRET}",
        f"otpauth://totp/name?secret={SECRET}&algorithm=MD5",
        f"otpauth://totp/name?secret={SECRET}&digits=4",
        f"otpauth://totp/name?secret={SECRET}&digits=11",
        f"otpauth://totp/name?secret={SECRET}&digits=no",
        f"otpauth://totp/name?secret={SECRET}&period=0",
        f"otpauth://totp/name?secret={SECRET}&period=86401",
        f"otpauth://hotp/name?secret={SECRET}",
        f"otpauth://hotp/name?secret={SECRET}&counter=-1",
        f"otpauth://hotp/name?secret={SECRET}&counter={MAX_COUNTER + 1}",
        f"otpauth://totp/name?secret={SECRET}&issuer=Steam&data=%5B%5D",
        f"otpauth://totp/name?secret={SECRET}&issuer=Steam&data=notjson",
        f"otpauth://totp/name?secret={SECRET}&issuer=Steam&algorithm=SHA256",
        f"otpauth://hotp/name?secret={SECRET}&issuer=Steam&counter=1",
        f"otpauth://totp/name?secret={SECRET}&issuer=Steam&data="
        + urlencode({"": '{"identity_secret":"invalid!"}'})[1:],
        f"otpauth://totp/name?secret={SECRET}&issuer=name#fragment",
    ],
)
def test_invalid_import_rejected(text):
    with pytest.raises(ValueError):
        parse_export(text)


def test_line_number_and_import_limit():
    with pytest.raises(ValueError, match="Line 3"):
        parse_export(f"# comment\n otpauth://totp/good?secret={SECRET}\ninvalid")
    with pytest.raises(ValueError, match="1000"):
        parse_export("\n".join(f"otpauth://totp/a{i}?secret={SECRET}" for i in range(1001)))


def test_secret_normalization():
    assert decode_secret(SECRET.lower().replace("=", "") + " ") == b"12345678901234567890"


def test_uri_path_plus_is_literal_not_form_encoded_space():
    record = parse_export(f"otpauth://totp/Example:alice+backup?secret={SECRET}")[0]
    assert record["name"] == "alice+backup"
    assert parse_export(export_record(record))[0]["name"] == "alice+backup"


@pytest.mark.parametrize("identity", [0, 1, False, True, [], {}, ["invalid"], "", "%%%"])
def test_steam_import_rejects_malformed_identity_secret_types(identity):
    params = {
        "secret": SECRET,
        "issuer": "Steam",
        "data": json.dumps({"identity_secret": identity}),
    }
    with pytest.raises(ValueError):
        parse_export("otpauth://totp/Steam:fixture?" + urlencode(params))
