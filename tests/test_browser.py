"""Real-browser checks with synthetic authenticators and a mocked Steam service."""

import io
import json
import threading
from urllib.parse import quote

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
            http_credentials={
                "username": "test-owner",
                "password": "synthetic-browser-test-password",
            },
            permissions=["clipboard-read", "clipboard-write"],
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_app[2])
        expect(page.locator("#empty-state")).to_be_visible()
        yield page
        assert errors == []
        context.close()
        browser.close()


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
