"""Exercise the generated PWA behind an ordinary static server, with no Flask backend."""

import base64
import functools
import io
import json
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from winauth_reborn.authenticators import export_record, generate_code, parse_export
from winauth_reborn.build_browser import build

PASSWORD = "synthetic-browser-vault-password"
SAMPLE = (
    "otpauth://totp/Example:alice?secret=JBSWY3DPEHPK3PXP&issuer=Example\n"
    "otpauth://hotp/Counter:bob?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&counter=0\n"
    "otpauth://totp/Steam:trader?secret=JBSWY3DPEHPK3PXP&issuer=Steam"
    "&data=%7B%22revocation_code%22%3A%22R-SYNTHETIC-ONLY%22%7D"
)


@pytest.fixture(scope="module")
def static_browser(tmp_path_factory):
    directory = tmp_path_factory.mktemp("browser-artifact")
    build(directory / "reborn")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/reborn/", directory / "reborn"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def browser_edition(chromium, static_browser):
    context = chromium.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    context.on("request", lambda request: requests.append(request.url))
    page.goto(static_browser[0])
    expect(page.locator("#browser-unlock-submit")).to_have_text("Create browser vault")
    page.locator('#browser-unlock-form [name="password"]').fill(PASSWORD)
    page.locator('#browser-unlock-form [name="confirm_password"]').fill(PASSWORD)
    page.locator("#browser-unlock-submit").click()
    expect(page.locator("#empty-state")).to_be_visible()
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    yield page
    assert errors == []
    assert all(url.startswith(static_browser[0]) for url in requests)
    assert not any("/api/" in url for url in requests)
    context.close()


def local_request(page, path, method="GET", data=None):
    return page.evaluate(
        """async ({path, method, data}) => {
          const response = await browserVault.request(path, {
            method, ...(data === null ? {} : {body: JSON.stringify(data)})
          });
          return {status: response.status, data: await response.json()};
        }""",
        {"path": path, "method": method, "data": data},
    )


def import_sample(page):
    page.locator("#import-button").click()
    page.locator("#import-text").fill(SAMPLE)
    page.get_by_role("button", name="Import accounts", exact=True).click()
    expect(page.locator(".account-card")).to_have_count(3)


def unlock(page, password=PASSWORD):
    expect(page.locator("#browser-unlock-submit")).to_have_text("Unlock vault")
    page.locator('#browser-unlock-form [name="password"]').fill(password)
    page.locator("#browser-unlock-submit").click()


def stored_envelope(page):
    return page.evaluate(
        """() => new Promise((resolve, reject) => {
          const opening = indexedDB.open(browserVault.databaseName, 1);
          opening.onerror = () => reject(opening.error);
          opening.onsuccess = () => {
            const database = opening.result;
            const reading = database.transaction('vault').objectStore('vault').get('owner');
            reading.onsuccess = () => {database.close(); resolve(reading.result);};
            reading.onerror = () => {database.close(); reject(reading.error);};
          };
        })"""
    )


def test_encrypted_persistence_offline_reload_and_password_lock(browser_edition):
    page = browser_edition
    import_sample(page)
    envelope = stored_envelope(page)
    assert set(envelope) == {"version", "iterations", "salt", "iv", "ciphertext"}
    assert envelope["iterations"] == 600000
    assert len(base64.b64decode(envelope["iv"])) == 12
    serialized = json.dumps(envelope)
    for secret in ("alice", "JBSWY3DPEHPK3PXP", "R-SYNTHETIC-ONLY", PASSWORD):
        assert secret not in serialized
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    page.context.set_offline(True)
    page.reload()
    expect(page.locator(".account-card")).to_have_count(0)
    unlock(page, "incorrect-password")
    expect(page.locator("#browser-unlock-error")).to_contain_text("Incorrect vault password")
    unlock(page)
    expect(page.locator(".account-card")).to_have_count(3)
    alice = page.locator(".account-card").filter(has=page.get_by_role("heading", name="alice"))
    alice.locator(".copy-button").click()
    expect(page.locator("#toast")).to_have_text("Code copied to clipboard")
    assert len(page.evaluate("navigator.clipboard.readText()")) == 6
    page.locator("#lock-button").click()
    expect(page.locator("#browser-unlock")).to_be_visible()
    assert local_request(page, "/api/accounts")["status"] == 401
    unlock(page)
    expect(page.locator(".account-card")).to_have_count(3)
    assert stored_envelope(page) == envelope


def test_original_export_compatibility_and_crypto_vectors(browser_edition):
    page = browser_edition
    fixtures = [
        "\ufeff# Header\n" + SAMPLE,
        "otpauth://totp/Example:a+b#tag?secret=jbsw y3dp ehpk 3pxp&unknown=keep+this&icon=legacy",
        "otpauth://totp/BattleNet:battle?secret=JBSWY3DPEHPK3PXP&issuer=BattleNet&serial=US-123",
        "otpauth://hotp/high?secret=JBSWY3DPEHPK3PXP&counter=18446744073709551614",
    ]
    for text in fixtures:
        python_records = parse_export(text)
        browser_records = page.evaluate("(text) => WinAuthTokens.parseExport(text)", text)
        for python_record, browser_record in zip(python_records, browser_records, strict=True):
            for field in ("name", "issuer", "kind", "algorithm", "digits", "period", "secret"):
                assert python_record[field] == browser_record[field]
            assert int(browser_record["counter"]) == python_record["counter"]
        exported = page.evaluate(
            "(records) => records.map(WinAuthTokens.exportRecord).join('\\n')", browser_records
        )
        reparsed = parse_export(exported)
        assert [record["counter"] for record in reparsed] == [
            record["counter"] for record in python_records
        ]
        for before, after in zip(python_records, reparsed, strict=True):
            before_params = dict(parse_qsl(urlsplit(export_record(before)).query))
            after_params = dict(parse_qsl(urlsplit(export_record(after)).query))
            assert all(after_params[key] == value for key, value in before_params.items())
    for algorithm, size in (("SHA1", 20), ("SHA256", 32), ("SHA512", 64)):
        secret = base64.b32encode((b"1234567890" * 7)[:size]).decode()
        record = parse_export(f"otpauth://totp/RFC?secret={secret}&algorithm={algorithm}&digits=8")[
            0
        ]
        for timestamp in (59, 1111111109, 1111111111, 1234567890, 2000000000, 20000000000):
            actual = page.evaluate(
                "({record, timestamp}) => WinAuthTokens.generateCode(record, timestamp)",
                {"record": record, "timestamp": timestamp},
            )
            assert actual == generate_code(record, timestamp)
    steam = parse_export(SAMPLE)[2]
    assert page.evaluate(
        "(record) => WinAuthTokens.generateCode(record, 59)", steam
    ) == generate_code(steam, 59)


def test_hotp_atomic_across_tabs_and_lossless_large_counters(browser_edition, static_browser):
    page = browser_edition
    import_sample(page)
    bob = local_request(page, "/api/accounts")["data"]["accounts"][1]
    second = page.context.new_page()
    second.goto(static_browser[0])
    unlock(second)
    expect(second.locator(".account-card")).to_have_count(3)
    first_call = page.evaluate_handle(
        "(id) => ({pending: browserVault.request(`/api/accounts/${id}/hotp`, {method:'POST'}).then(r => r.json())})",
        bob["id"],
    )
    second_code = local_request(second, f"/api/accounts/{bob['id']}/hotp", "POST")["data"]["code"]
    first_code = first_call.evaluate("(call) => call.pending")["code"]
    assert {first_code, second_code} == {"755224", "287082"}
    assert local_request(page, "/api/accounts")["data"]["accounts"][1]["counter"] == "2"
    assert (
        local_request(
            page,
            "/api/import",
            "POST",
            {"text": "otpauth://hotp/Big?secret=JBSWY3DPEHPK3PXP&counter=18446744073709551614"},
        )["status"]
        == 200
    )
    big = local_request(page, "/api/accounts")["data"]["accounts"][-1]
    uri = f"/api/accounts/{big['id']}/hotp"
    assert local_request(page, uri, "POST")["status"] == 200
    assert (
        local_request(page, "/api/accounts")["data"]["accounts"][-1]["counter"]
        == "18446744073709551615"
    )
    assert local_request(page, uri, "POST")["status"] == 400
    second.close()


def test_atomic_imports_duplicate_preservation_edit_preferences_and_export(browser_edition):
    page = browser_edition
    import_sample(page)
    accounts = local_request(page, "/api/accounts")["data"]["accounts"]
    bob = accounts[1]
    local_request(page, f"/api/accounts/{bob['id']}/hotp", "POST")
    assert local_request(page, "/api/import", "POST", {"text": SAMPLE})["data"] == {
        "added": 0,
        "skipped": 3,
    }
    failed = local_request(
        page,
        "/api/import",
        "POST",
        {"text": "otpauth://totp/New?secret=JBSWY3DPEHPK3PXP\ninvalid"},
    )
    assert failed["status"] == 400 and "Line 2" in failed["data"]["error"]
    changes = {
        "name": "edited + account",
        "issuer": "New",
        "algorithm": "SHA512",
        "digits": 8,
        "period": 45,
        "pinned": True,
        "color": "#8ebfff",
        "icon": "key-round",
    }
    assert local_request(page, f"/api/accounts/{bob['id']}", "PATCH", changes)["status"] == 200
    preferences = {"theme": "light", "density": "compact", "hide_codes": True}
    assert local_request(page, "/api/settings", "PATCH", preferences)["status"] == 200
    exported = page.evaluate(
        "() => browserVault.request('/api/export', {method:'POST'}).then(r => r.text())"
    )
    records = parse_export(exported)
    assert len(records) == 3 and records[1]["counter"] == 1
    assert records[1]["name"] == "edited + account" and records[1]["pinned"]
    assert records[1]["algorithm"] == "SHA512" and records[1]["period"] == 45
    page.reload()
    unlock(page)
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator("html")).to_have_attribute("data-density", "compact")
    expect(page.locator(".account-card").first.locator("h3")).to_have_text("edited + account")
    expect(page.locator(".account-card").first.locator(".code")).to_have_text("--- ---")
    assert (
        local_request(page, f"/api/accounts/{bob['id']}", "PATCH", {"counter": 0})["status"] == 400
    )


def test_recovery_and_explicit_server_only_confirmations(browser_edition):
    page = browser_edition
    import_sample(page)
    page.get_by_role("button", name="Server-only confirmations", exact=True).click()
    expect(page.locator("#steam-error")).to_contain_text("server version")
    expect(page.locator("#steam-connect")).not_to_be_visible()
    page.get_by_role("button", name="Close Steam", exact=True).click()
    page.get_by_role("button", name="Edit trader", exact=True).click()
    page.locator("#edit-recovery-button").click()
    page.locator("#confirm-ok").click()
    expect(page.locator('[data-field="recovery_code"]')).to_have_value("R-SYNTHETIC-ONLY")
    page.get_by_role("button", name="Close recovery details", exact=True).click()
    expect(page.locator("#recovery-fields")).to_be_empty()


def test_custom_icons_are_normalized_and_svg_rejected(browser_edition):
    page = browser_edition
    import_sample(page)
    account = local_request(page, "/api/accounts")["data"]["accounts"][0]
    image = Image.new("RGB", (200, 100), "blue")
    content = io.BytesIO()
    image.save(content, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(content.getvalue()).decode()
    changes = {"icon": "custom", "custom_icon": data_uri}
    assert local_request(page, f"/api/accounts/{account['id']}", "PATCH", changes)["status"] == 200
    saved = local_request(page, "/api/accounts")["data"]["accounts"][0]["custom_icon"]
    with Image.open(io.BytesIO(base64.b64decode(saved.split(",")[1]))) as normalized:
        assert normalized.size == (64, 32)
        assert not normalized.info
    changes["custom_icon"] = "data:image/png;base64," + base64.b64encode(b"<svg/>").decode()
    assert local_request(page, f"/api/accounts/{account['id']}", "PATCH", changes)["status"] == 400


def test_manifest_scope_install_controls_and_nonsecret_cache(browser_edition, static_browser):
    page = browser_edition
    import_sample(page)
    info = page.evaluate(
        """async () => {
          const manifestURL = document.querySelector('link[rel=manifest]').href;
          const manifest = await fetch(manifestURL).then(r => r.json());
          const registration = await navigator.serviceWorker.ready;
          const cached = [];
          for (const name of await caches.keys()) {
            for (const request of await (await caches.open(name)).keys()) cached.push(request.url);
          }
          return {manifest, manifestURL, scope: registration.scope, cached};
        }"""
    )
    assert info["scope"] == static_browser[0]
    assert info["manifest"]["display"] == "standalone"
    assert info["manifest"]["scope"] == "../"
    assert any(icon.get("purpose") == "maskable" for icon in info["manifest"]["icons"])
    assert all(url.startswith(static_browser[0]) for url in info["cached"])
    assert not any("/api/" in url for url in info["cached"])
    assert static_browser[0] + "index.html" in info["cached"]
    page.evaluate(
        """() => {
          const event = new Event('beforeinstallprompt', {cancelable:true});
          event.prompt = async () => {};
          event.userChoice = Promise.resolve({outcome:'accepted'});
          window.dispatchEvent(event);
        }"""
    )
    page.locator("#pwa-install").click()
    expect(page.locator("#pwa-status")).to_contain_text("Installing app")
    page.context.set_offline(True)
    page.goto(static_browser[0] + "?installed=1")
    unlock(page)
    expect(page.locator(".account-card")).to_have_count(3)


def test_unsupported_storage_never_silently_creates_an_unencrypted_vault(chromium, static_browser):
    context = chromium.new_context()
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'locks', {value: undefined})")
    page.goto(static_browser[0])
    expect(page.locator("#browser-unlock-error")).to_contain_text("Web Locks")
    expect(page.locator("#browser-unlock-submit")).to_be_disabled()
    context.close()


def test_build_refuses_to_overwrite_existing_files(tmp_path):
    target = tmp_path / "static-app"
    build(target)
    original = (target / "index.html").read_text()
    with pytest.raises(ValueError, match="empty output"):
        build(target)
    assert (target / "index.html").read_text() == original


def test_service_worker_update_preserves_vault_and_other_apps_caches(
    browser_edition, static_browser
):
    page = browser_edition
    import_sample(page)
    original_envelope = stored_envelope(page)
    page.evaluate(
        "() => caches.open('unrelated-app-cache').then(cache => cache.put('./unrelated', new Response('keep me')))"
    )
    worker_path = static_browser[1] / "service-worker.js"
    original = worker_path.read_text()
    original_stat = worker_path.stat()
    fingerprint = re.search(r'const CACHE = PREFIX \+ "([a-f0-9]+)"', original).group(1)
    try:
        worker_path.write_text(original.replace(fingerprint, fingerprint + "-update"))
        os.utime(worker_path, (original_stat.st_atime, original_stat.st_mtime + 2))
        page.evaluate("navigator.serviceWorker.ready.then(registration => registration.update())")
        expect(page.locator("#pwa-update")).to_be_visible()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#pwa-update").click()
        expect(page.locator("#browser-unlock")).to_be_visible()
        unlock(page)
        expect(page.locator(".account-card")).to_have_count(3)
        assert stored_envelope(page) == original_envelope
        cache_state = page.evaluate(
            """() => new Promise(resolve => {
              const channel = new MessageChannel();
              channel.port1.onmessage = event => resolve(event.data);
              navigator.serviceWorker.controller.postMessage('GET_CACHE_INFO', [channel.port2]);
            })"""
        )
        assert cache_state["cache"].endswith("-update"), (
            f"Page is controlled by the wrong worker: {cache_state}"
        )
        names = page.evaluate("caches.keys()")
        assert "unrelated-app-cache" in names
        assert any(name.endswith("-update") for name in names), (
            f"Updated PWA cache is missing: {names}"
        )
        assert not any(name.endswith(fingerprint) for name in names), (
            f"Stale PWA cache remains after update: {names}"
        )
    finally:
        worker_path.write_text(original)
        os.utime(worker_path, (original_stat.st_atime, original_stat.st_mtime))


@pytest.mark.parametrize(
    ("worker_state", "accept"),
    [("installed", True), ("activated", True), ("activated", False)],
)
def test_update_handles_worker_transition_during_confirmation(
    chromium, static_browser, worker_state, accept
):
    context = chromium.new_context()
    try:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            """(() => {
              const worker = new EventTarget();
              worker.state = 'installed';
              const registration = new EventTarget();
              registration.waiting = worker;
              const container = new EventTarget();
              container.controller = new EventTarget();
              container.register = async () => registration;
              container.ready = Promise.resolve(registration);
              worker.postMessage = (message, ports) => {
                if (message === 'CLEAN_OLD_CACHES') {
                  ports[0].postMessage('cleaned');
                  return;
                }
                sessionStorage.setItem('test-update-message', message);
                worker.state = 'activating';
                registration.waiting = null;
                worker.dispatchEvent(new Event('statechange'));
                setTimeout(() => {
                  worker.state = 'activated';
                  worker.dispatchEvent(new Event('statechange'));
                }, 50);
                setTimeout(() => {
                  container.controller = worker;
                  container.dispatchEvent(new Event('controllerchange'));
                }, 100);
              };
              Object.defineProperty(navigator, 'serviceWorker', {value: container});
              window.testUpdate = {worker, registration, container};
              window.addEventListener('beforeunload', () => {
                sessionStorage.setItem('test-update-state-at-navigation', window.testUpdate.worker.state);
              });
            })()"""
        )
        page.goto(static_browser[0])
        expect(page.locator("#pwa-update")).to_be_visible()
        page.evaluate(
            """({workerState, accept}) => {
              window.confirm = () => {
                testUpdate.registration.waiting = null;
                testUpdate.worker.state = workerState;
                if (workerState === 'activated') {
                  testUpdate.container.controller = testUpdate.worker;
                  testUpdate.container.dispatchEvent(new Event('controllerchange'));
                }
                return accept;
              };
            }""",
            {"workerState": worker_state, "accept": accept},
        )
        navigations = []
        page.on("framenavigated", lambda frame: navigations.append(frame.url))
        if accept:
            with page.expect_navigation(timeout=5000):
                page.locator("#pwa-update").click()
            assert len(navigations) == 1
            expected = "ACTIVATE_UPDATE" if worker_state == "installed" else None
            assert page.evaluate("sessionStorage.getItem('test-update-message')") == expected
            assert (
                page.evaluate("sessionStorage.getItem('test-update-state-at-navigation')")
                == "activated"
            )
        else:
            page.locator("#pwa-update").click()
            assert navigations == []
            assert page.evaluate("sessionStorage.getItem('test-update-message')") is None
        assert errors == []
    finally:
        context.close()
