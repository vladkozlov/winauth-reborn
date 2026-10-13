"""Real-browser checks with synthetic authenticators and a mocked Steam service."""

import io
import json
import threading
from urllib.parse import quote, urlsplit

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from winauth_reborn.app import create_app

SAMPLE = (
    "# Synthetic fixtures only\n"
    "otpauth://totp/Example:alice?secret=JBSWY3DPEHPK3PXP&issuer=Example\n"
    "otpauth://hotp/Counter:bob?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&counter=0\n"
    "otpauth://totp/Steam:trader?secret=JBSWY3DPEHPK3PXP&digits=5&deviceid=android:test"
    "&data="
    + quote(
        json.dumps(
            {
                "identity_secret": "aWRlbnRpdHktc2VjcmV0",
                "revocation_code": "R-SYNTHETIC-ONLY",
            }
        )
    )
)


@pytest.fixture
def live_app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path / "vault",
            "AUTH_USERNAME": "test-owner",
            "AUTH_PASSWORD": "synthetic-browser-test-password",
            "AUTH_PASSWORD_HASH": "",
            "HTTPS_ONLY": False,
        }
    )
    steam = app.extensions["steam"]
    state = {"actions": []}
    steam.login = lambda *args: {
        "session": {"steam_id": "76561198000000000", "steam_login_secure": "synthetic-cookie"}
    }

    def confirmations(record, operation=None, confirmation_id=None, nonce=None):
        if operation:
            state["actions"].append((operation, confirmation_id, nonce))
            return []
        return (
            []
            if state["actions"]
            else [
                {
                    "id": "123",
                    "nonce": "456",
                    "headline": "Trade with synthetic friend",
                    "summary": ["You give: test item", "You receive: another test item"],
                    "creator_id": "789",
                    "type": 2,
                }
            ]
        )

    steam.confirmations = confirmations
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def browser_page(live_app):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            permissions=["clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_app[2])
        sign_in(page)
        expect(page.locator("#empty-state")).to_be_visible()
        yield page
        assert errors == []
        context.close()
        browser.close()


def sign_in(page, pin=None):
    page.locator('#password-login-form [name="username"]').fill("test-owner")
    page.locator('#password-login-form [name="password"]').fill("synthetic-browser-test-password")
    if pin:
        page.locator("#remember-browser").check()
        page.locator('#password-login-form [name="new_pin"]').fill(pin)
        page.locator('#password-login-form [name="confirm_pin"]').fill(pin)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator("#empty-state")).to_be_visible()


def import_sample(page):
    page.locator("#import-button").click()
    page.locator("#import-file").set_input_files(
        {
            "name": "original-winauth.txt",
            "mimeType": "text/plain",
            "buffer": SAMPLE.encode(),
        }
    )
    expect(page.locator("#file-label")).to_have_text("original-winauth.txt")
    page.get_by_role("button", name="Import accounts", exact=True).click()
    expect(page.locator(".account-card")).to_have_count(3)
    expect(page.locator("#import-dialog")).not_to_be_visible()


def test_import_copy_search_privacy_hotp_and_restart(browser_page, live_app):
    page = browser_page
    import_sample(page)
    expect(page.locator("#total-stat")).to_have_text("3")
    alice = page.locator(".account-card").filter(has=page.get_by_role("heading", name="alice"))
    alice.get_by_role("button", name="Copy code for alice", exact=True).last.click()
    expect(page.locator("#toast")).to_have_text("Code copied to clipboard")
    clipboard = page.evaluate("navigator.clipboard.readText()")
    assert len(clipboard) == 6 and clipboard.isdigit()
    page.locator("#privacy-button").click()
    expect(alice.locator(".code")).to_have_text("••••••")
    page.locator("#privacy-button").click()
    page.locator("#search").fill("no such account")
    expect(page.locator("#no-results")).to_be_visible()
    page.locator("#search").fill("alice")
    expect(page.locator(".account-card")).to_have_count(1)
    page.locator("#search").fill("")
    bob = page.locator(".account-card").filter(has=page.get_by_role("heading", name="bob"))
    expect(bob.locator(".copy-button")).to_be_disabled()
    bob.get_by_role("button", name="Generate next").click()
    page.locator("#confirm-ok").click()
    expect(bob.locator(".code")).to_have_text("755 224")
    expect(bob.locator(".time-row")).to_contain_text("Counter: 1")
    bob.locator(".copy-button").click()
    assert page.evaluate("navigator.clipboard.readText()") == "755224"
    assert live_app[0].extensions["vault"].list()[1]["counter"] == 1
    page.reload()
    expect(page.locator(".account-card")).to_have_count(3)
    expect(bob.locator(".copy-button")).to_be_disabled()
    expect(bob.locator(".time-row")).to_contain_text("Counter: 1")


def test_steam_login_review_approve_and_disconnect(browser_page, live_app):
    page = browser_page
    import_sample(page)
    page.locator("#steam-filter").click()
    expect(page.locator(".account-card")).to_have_count(1)
    page.get_by_role("button", name="Confirmations").click()
    page.locator('#steam-login-form [name="username"]').fill("synthetic-trader")
    page.locator('#steam-login-form [name="password"]').fill("synthetic-password")
    page.get_by_role("button", name="Connect Steam", exact=True).click()
    expect(page.locator(".confirmation")).to_have_count(1)
    expect(page.locator(".confirmation")).to_contain_text("You give: test item")
    assert live_app[1]["actions"] == []
    page.get_by_role("button", name="Approve", exact=True).click()
    expect(page.locator("#confirm-dialog")).to_be_visible()
    assert live_app[1]["actions"] == []
    page.locator("#confirm-ok").click()
    expect(page.locator(".confirmation")).to_have_count(0)
    assert live_app[1]["actions"] == [("allow", "123", "456")]
    page.locator("#disconnect-steam").click()
    expect(page.locator("#steam-connect")).to_be_visible()
    page.get_by_role("button", name="Close Steam").click()
    page.get_by_role("button", name="Confirmations").click()
    expect(page.locator("#steam-connect")).to_be_visible()
    expect(page.locator('#steam-login-form [name="password"]')).to_have_value("")


def test_mobile_invalid_import_export_and_delete(browser_page, tmp_path):
    page = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#import-button").click()
    page.locator("#import-text").fill("not a valid URI")
    page.get_by_role("button", name="Import accounts", exact=True).click()
    expect(page.locator("#import-error")).to_contain_text("Line 1")
    page.get_by_role("button", name="Close import").click()
    import_sample(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#steam-filter").click()
    expect(page.locator(".account-card")).to_have_count(1)
    page.locator("#all-filter").click()
    expect(page.locator(".account-card")).to_have_count(3)
    page.locator("#mobile-export-button").click()
    with page.expect_download() as download_info:
        page.locator("#confirm-ok").click()
    download = download_info.value
    destination = tmp_path / "export.txt"
    download.save_as(destination)
    assert "identity_secret" in destination.read_text()
    assert "steam_login_secure" not in destination.read_text()
    page.get_by_role("button", name="Delete alice", exact=True).click()
    page.locator("#confirm-ok").click()
    expect(page.locator(".account-card")).to_have_count(2)


def test_card_editor_icons_colors_pins_and_custom_upload(browser_page, live_app):
    page = browser_page
    import_sample(page)
    page.get_by_role("button", name="Pin trader", exact=True).click()
    expect(page.locator(".account-card").first.locator("h3")).to_have_text("trader")
    page.locator("#pinned-filter").click()
    expect(page.locator(".account-card")).to_have_count(1)
    page.locator("#pinned-filter").click()
    page.get_by_role("button", name="Edit alice", exact=True).click()
    expect(page.locator('#edit-form [name="secret"]')).to_have_value("")
    page.locator('#edit-form [name="name"]').fill("Work account")
    page.locator('#edit-form [name="issuer"]').fill("GitHub")
    page.locator('[data-icon="shield-check"]').click()
    page.get_by_role("button", name="Blue", exact=True).click()
    page.locator('#edit-form [name="pinned"]').check()
    page.locator("#edit-advanced summary").click()
    page.locator('#edit-form [name="period"]').fill("45")
    page.get_by_role("button", name="Save changes", exact=True).click()
    card = page.locator(".account-card").filter(
        has=page.get_by_role("heading", name="Work account")
    )
    expect(card.locator(".icon-shield-check")).to_be_visible()
    page.evaluate("""async () => {
      const image = new Image();
      image.src = "/static/icons/shield-check.svg";
      await image.decode();
      if (!image.naturalWidth || !image.naturalHeight) throw new Error("SVG icon did not load");
    }""")
    assert "shield-check.svg" in card.locator(".icon-shield-check").evaluate(
        "(node) => getComputedStyle(node).maskImage"
    )
    expect(card.get_by_role("button", name="Unpin Work account")).to_have_attribute(
        "aria-pressed", "true"
    )
    record = next(r for r in live_app[0].extensions["vault"].list() if r["name"] == "Work account")
    assert record["period"] == 45 and record["color"] == "#8ebfff"
    card.hover()
    expect(card).to_have_css("border-top-color", "rgb(142, 191, 255)")
    page.reload()
    expect(card.locator(".icon-shield-check")).to_be_visible()
    card.get_by_role("button", name="Edit Work account").click()
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, format="PNG")
    page.locator("#edit-icon-file").set_input_files(
        {
            "name": "custom.png",
            "mimeType": "image/png",
            "buffer": output.getvalue(),
        }
    )
    expect(page.locator('[data-icon="custom"]')).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(card.locator(".service-icon img")).to_be_visible()
    page.get_by_role("button", name="Unpin trader", exact=True).click()
    expect(page.locator(".account-card").first.locator("h3")).to_have_text("Work account")


def test_preferences_themes_compact_privacy_sort_and_shortcuts(browser_page):
    page = browser_page
    import_sample(page)
    page.locator("#settings-button").click()
    page.locator('#settings-form [name="theme"]').select_option("light")
    page.locator('#settings-form [name="density"]').select_option("compact")
    page.locator('#settings-form [name="sort_order"]').select_option("name")
    page.locator('#settings-form [name="hide_codes"]').check()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator("html")).to_have_attribute("data-density", "compact")
    expect(page.locator(".account-card").first.locator(".code")).to_have_text("••••••")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(245, 247, 245)"
    page.reload()
    expect(page.locator(".account-card")).to_have_count(3)
    expect(page.locator(".account-card").first.locator(".code")).to_have_text("••••••")
    page.locator("h1").click()
    page.keyboard.press("h")
    expect(page.locator("#privacy-button")).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("/")
    expect(page.locator("#search")).to_be_focused()
    page.locator("#search").fill("h")
    expect(page.locator("#privacy-button")).to_have_attribute("aria-pressed", "false")
    page.locator("#search").fill("")
    page.locator("h1").click()
    page.keyboard.press(",")
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.locator('#settings-form [name="theme"]').select_option("system")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "system")
    page.emulate_media(color_scheme="dark")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(16, 18, 20)"
    page.emulate_media(color_scheme="light")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(245, 247, 245)"


def test_recovery_is_revealed_only_after_confirmation_and_cleared(browser_page):
    page = browser_page
    import_sample(page)
    page.get_by_role("button", name="Edit trader", exact=True).click()
    page.get_by_role("button", name="Recovery details", exact=True).click()
    expect(page.locator("#recovery-dialog")).not_to_be_visible()
    page.locator("#confirm-cancel").click()
    assert "R-SYNTHETIC-ONLY" not in page.content()
    page.get_by_role("button", name="Recovery details", exact=True).click()
    page.locator("#confirm-ok").click()
    expect(page.locator('[data-field="recovery_code"]')).to_have_value("R-SYNTHETIC-ONLY")
    page.get_by_role("button", name="Copy Recovery / revocation code", exact=True).click()
    assert page.evaluate("navigator.clipboard.readText()") == "R-SYNTHETIC-ONLY"
    page.get_by_role("button", name="Close recovery details", exact=True).click()
    expect(page.locator("#recovery-fields")).to_be_empty()


def test_idle_privacy_and_recovery_timeout(browser_page):
    page = browser_page
    page.clock.install()
    import_sample(page)
    page.locator("#settings-button").click()
    page.locator('#settings-form [name="auto_hide_seconds"]').select_option("30")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    page.clock.fast_forward(31000)
    expect(page.locator("#privacy-button")).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Edit trader", exact=True).click()
    page.get_by_role("button", name="Recovery details", exact=True).click()
    page.locator("#confirm-ok").click()
    expect(page.locator('[data-field="recovery_code"]')).to_be_visible()
    page.clock.fast_forward(61000)
    expect(page.locator("#recovery-dialog")).not_to_be_visible()
    expect(page.locator("#recovery-fields")).to_be_empty()
    page.get_by_role("button", name="Recovery details", exact=True).click()
    page.locator("#confirm-ok").click()
    expect(page.locator('[data-field="recovery_code"]')).to_be_visible()
    page.evaluate(
        "Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange'))"
    )
    expect(page.locator("#recovery-dialog")).not_to_be_visible()
    expect(page.locator("#recovery-fields")).to_be_empty()


def test_encrypted_pin_remember_unlock_wrong_pin_and_forget(browser_page, live_app):
    page = browser_page
    page.locator("#lock-button").click()
    expect(page.locator("#password-login-form")).to_be_visible()
    expect(page.locator("#password-login-form .pin-cells span")).to_have_count(8)
    sign_in(page, pin="8072")
    stored = page.evaluate("localStorage.getItem('winauth.remembered-login.v1')")
    envelope = json.loads(stored)
    assert envelope["version"] == 1 and envelope["iterations"] == 600000
    assert "test-owner" not in stored and "synthetic-browser-test-password" not in stored
    assert "8072" not in stored and "password" not in envelope
    assert page.evaluate("sessionStorage.length") == 0
    page.locator("#lock-button").click()
    expect(page.locator("#pin-view")).to_be_visible()
    pin_input = page.locator('#pin-login-form [name="pin"]')
    expect(page.locator("#pin-view .pin-cells span")).to_have_count(4)
    pin_input.fill("11x1")
    expect(pin_input).to_have_value("111")
    pin_input.fill("1111")
    page.get_by_role("button", name="Unlock vault", exact=True).click()
    expect(page.locator("#login-error")).to_contain_text("Incorrect PIN")
    expect(pin_input).to_have_value("")
    expect(page.locator("#pin-view .pin-cells")).to_have_text("")
    assert (
        page.evaluate(
            "fetch('/api/accounts', {headers:{'X-Requested-With':'WinAuthReborn'}}).then(response=>response.status)"
        )
        == 401
    )
    page.locator('#pin-login-form [name="pin"]').fill("8072")
    page.get_by_role("button", name="Unlock vault", exact=True).click()
    expect(page.locator("#empty-state")).to_be_visible()
    origins = page.context.storage_state()["origins"]
    context = page.context.browser.new_context(storage_state={"cookies": [], "origins": origins})
    try:
        restored = context.new_page()
        restored.goto(live_app[2])
        expect(restored.locator("#pin-view")).to_be_visible()
        restored.locator('#pin-login-form [name="pin"]').fill("8072")
        restored.get_by_role("button", name="Unlock vault", exact=True).click()
        expect(restored.locator("#empty-state")).to_be_visible()
    finally:
        context.close()
    page.locator("#lock-button").click()
    page.locator("#forget-login").click()
    page.locator("#confirm-forget").click()
    expect(page.locator("#password-view")).to_be_visible()
    assert page.evaluate("localStorage.getItem('winauth.remembered-login.v1')") is None


def test_filled_pin_separator_and_individual_dot_reveal(browser_page):
    page = browser_page
    import_sample(page)
    page.get_by_role("button", name="Pin trader", exact=True).click()
    pinned = page.locator(".account-card").first
    expect(pinned.locator(".pin-button .icon-star-filled")).to_be_visible()
    expect(page.locator(".pin-separator")).to_have_count(1)
    next_card = page.locator(".account-card").nth(1)
    pinned_box = pinned.bounding_box()
    next_box = next_card.bounding_box()
    gap = page.locator("#accounts").evaluate("(grid) => parseFloat(getComputedStyle(grid).rowGap)")
    assert next_box["y"] - (pinned_box["y"] + pinned_box["height"]) >= 2 * gap + 12
    page.set_viewport_size({"width": 430, "height": 900})
    page.locator("#settings-button").click()
    page.locator('#settings-form [name="density"]').select_option("compact")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    pinned_box = pinned.bounding_box()
    next_box = next_card.bounding_box()
    gap = page.locator("#accounts").evaluate("(grid) => parseFloat(getComputedStyle(grid).rowGap)")
    assert next_box["y"] - (pinned_box["y"] + pinned_box["height"]) >= 2 * gap + 12
    page.locator("#pinned-filter").click()
    expect(page.locator(".pin-separator")).to_have_count(0)
    page.locator("#pinned-filter").click()
    expect(page.locator(".pin-separator")).to_have_count(1)
    page.locator("#privacy-button").click()
    alice = page.locator(".account-card").filter(has=page.get_by_role("heading", name="alice"))
    expect(alice.locator(".code")).to_have_text("••••••")
    page.evaluate("navigator.clipboard.writeText('synthetic-do-not-copy')")
    alice.get_by_role("button", name="Reveal code for alice", exact=True).click()
    expect(alice.locator(".code")).not_to_have_text("••••••")
    expect(pinned.locator(".code")).to_have_text("•••••")
    assert page.evaluate("navigator.clipboard.readText()") == "synthetic-do-not-copy"
    expect(page.locator("#toast")).not_to_be_visible()
    pinned.get_by_role("button", name="Copy code for trader", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("Code copied")
    assert len(page.evaluate("navigator.clipboard.readText()")) == 5
    expect(pinned.locator(".code")).to_have_text("•••••")
    page.locator("#privacy-button").click()
    page.locator("#privacy-button").click()
    expect(alice.locator(".code")).to_have_text("••••••")
    page.get_by_role("button", name="Unpin trader", exact=True).click()
    expect(page.locator(".pin-separator")).to_have_count(0)
    expect(page.locator(".icon-star-filled")).to_have_count(0)
    page.get_by_role("button", name="Edit trader", exact=True).click()
    expect(page.locator("#edit-recovery-button")).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()
    page.get_by_role("button", name="Edit alice", exact=True).click()
    expect(page.locator("#edit-recovery-button")).not_to_be_visible()
    expect(page.locator(".account-card .steam-button").filter(has_text="Recovery")).to_have_count(0)


def test_login_wrong_password_show_control_and_damaged_storage(browser_page):
    page = browser_page
    page.locator("#lock-button").click()
    form = page.locator("#password-login-form")
    form.locator('[name="username"]').fill("test-owner")
    form.locator('[name="password"]').fill("wrong-password")
    page.locator("#show-login-password").click()
    expect(form.locator('[name="password"]')).to_have_attribute("type", "text")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator("#login-error")).to_contain_text("Incorrect username or password")
    expect(form.locator('[name="password"]')).to_have_attribute("type", "password")
    expect(form.locator('[name="password"]')).to_have_value("")
    page.evaluate(
        """localStorage.setItem('winauth.remembered-login.v1', JSON.stringify({
            version: 1, iterations: 600000, salt: 'A', iv: 'A', ciphertext: 'A'
        }))"""
    )
    page.reload()
    expect(page.locator("#login-error")).to_contain_text("Saved login is damaged")
    page.locator("#forget-login").click()
    page.evaluate(
        "() => {Storage.prototype.removeItem = () => {throw new DOMException('Storage blocked', 'SecurityError')};}"
    )
    page.locator("#confirm-forget").click()
    expect(page.locator("#forget-error")).to_contain_text("Could not remove the saved login")
    expect(page.locator("#forget-dialog")).to_be_visible()
    page.reload()
    page.locator("#forget-login").click()
    page.locator("#confirm-forget").click()
    sign_in(page)


def test_failed_pin_storage_signs_out_instead_of_silently_remembering(browser_page):
    page = browser_page
    page.locator("#lock-button").click()
    expect(page.locator("#password-login-form")).to_be_visible()
    page.evaluate(
        "() => {Storage.prototype.setItem = () => {throw new DOMException('Storage full', 'QuotaExceededError')};}"
    )
    form = page.locator("#password-login-form")
    form.locator('[name="username"]').fill("test-owner")
    form.locator('[name="password"]').fill("synthetic-browser-test-password")
    page.locator("#remember-browser").check()
    form.locator('[name="new_pin"]').fill("8072")
    form.locator('[name="confirm_pin"]').fill("8072")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator("#login-error")).to_contain_text("could not save the PIN")
    assert page.evaluate("localStorage.getItem('winauth.remembered-login.v1')") is None
    assert (
        page.evaluate(
            "fetch('/api/accounts', {headers:{'X-Requested-With':'WinAuthReborn'}}).then(response=>response.status)"
        )
        == 401
    )
    page.locator("#remember-browser").uncheck()
    sign_in(page)


def test_server_pwa_never_caches_vault_data_and_shows_offline_notice(browser_page):
    page = browser_page
    import_sample(page)
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    cached = page.evaluate(
        """async () => {
          const urls = [];
          for (const name of await caches.keys()) {
            for (const request of await (await caches.open(name)).keys()) urls.push(request.url);
          }
          return urls;
        }"""
    )
    assert any(url.endswith("/login") for url in cached)
    assert not any("/api/" in url for url in cached)
    assert not any(urlsplit(url).path == "/" for url in cached)
    assert not any(url.endswith("/static/app.js") for url in cached)
    page.context.set_offline(True)
    page.reload()
    expect(page.get_by_role("heading", name="Your server is unavailable.")).to_be_visible()
    assert "R-SYNTHETIC-ONLY" not in page.content()
