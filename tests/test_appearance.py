import base64
import io

import pytest
from PIL import Image, PngImagePlugin

from winauth_reborn.appearance import normalize_icon, validate_presentation


def image_uri(size=(128, 96)):
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("private", "synthetic-metadata")
    Image.new("RGB", size, "#ff0000").save(output, format="PNG", pnginfo=info)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def test_uploaded_icons_are_resized_and_metadata_is_removed():
    normalized = normalize_icon(image_uri())
    with Image.open(io.BytesIO(base64.b64decode(normalized.partition(",")[2]))) as image:
        assert image.size == (64, 48)
        assert image.mode == "RGBA"
        assert "private" not in image.info


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/icon.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,bad",
        None,
        [],
        "",
        "data:image/png;base64,YmFk",
    ],
)
def test_invalid_uploads_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_icon(value)


def test_oversized_dimensions_are_rejected():
    with pytest.raises(ValueError, match="dimensions"):
        normalize_icon(image_uri((2049, 1)))


@pytest.mark.parametrize(
    "changes",
    [
        {"icon": "../bad"},
        {"icon": []},
        {"color": "red"},
        {"color": "#fff"},
        {"color": "url(https://example.com)"},
        {"pinned": "yes"},
        {"custom_icon": {}},
    ],
)
def test_presentation_validation(changes):
    with pytest.raises(ValueError):
        validate_presentation(changes)


def test_safe_presentation_and_reset():
    assert validate_presentation({"icon": "mail", "color": "#Aa00ff", "pinned": True}) == {
        "icon": "mail",
        "color": "#Aa00ff",
        "pinned": True,
    }
    assert validate_presentation({"custom_icon": None}) == {"custom_icon": ""}
