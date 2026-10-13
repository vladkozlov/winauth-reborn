from __future__ import annotations

import base64
import binascii
import io
import re
import warnings

from PIL import Image, UnidentifiedImageError

ICONS = (
    "initial",
    "shield-check",
    "key-round",
    "gamepad-2",
    "mail",
    "globe",
    "briefcase-business",
    "star",
    "custom",
)


def normalize_icon(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(
        ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
    ):
        raise ValueError("Custom icons must be PNG, JPEG, or WebP images, not SVG or URLs.")
    if len(value) > 180000:
        raise ValueError("Custom icon must be smaller than 128 KB.")
    try:
        content = base64.b64decode(value.partition(",")[2], validate=True)
        if len(content) > 128 * 1024:
            raise ValueError("Custom icon must be smaller than 128 KB.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("Custom icon is not a supported image.")
                if image.width > 2048 or image.height > 2048:
                    raise ValueError("Custom icon dimensions must not exceed 2048 pixels.")
                image.load()
                result = image.convert("RGBA")
                result.info.clear()
                result.thumbnail((64, 64))
                output = io.BytesIO()
                result.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    except (
        binascii.Error,
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError(
            "Could not read this icon. Choose a valid PNG, JPEG, or WebP image."
        ) from exc


def validate_presentation(changes: dict) -> dict:
    if "icon" in changes and changes["icon"] not in ICONS:
        raise ValueError("Choose a supported icon.")
    if "color" in changes and (
        not isinstance(changes["color"], str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", changes["color"])
    ):
        raise ValueError("Color must be a six-digit hex color.")
    if "pinned" in changes and not isinstance(changes["pinned"], bool):
        raise ValueError("Pinned must be a boolean.")
    if "custom_icon" in changes:
        if changes["custom_icon"] is None:
            changes["custom_icon"] = ""
        elif changes["custom_icon"]:
            changes["custom_icon"] = normalize_icon(changes["custom_icon"])
        elif not isinstance(changes["custom_icon"], str):
            raise ValueError("Custom icon must be an image or empty text.")
    return changes
