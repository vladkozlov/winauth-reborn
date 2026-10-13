from __future__ import annotations

DEFAULT_SETTINGS = {
    "theme": "dark",
    "density": "comfortable",
    "hide_codes": False,
    "sort_order": "imported",
    "auto_hide_seconds": 0,
}


def validate_settings(changes: dict) -> dict:
    choices = {
        "theme": {"dark", "light", "system"},
        "density": {"comfortable", "compact"},
        "sort_order": {"imported", "name", "issuer"},
    }
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"Unknown setting: {key}.")
        if key in choices and (not isinstance(value, str) or value not in choices[key]):
            raise ValueError(f"Invalid {key} setting.")
        if key == "hide_codes" and not isinstance(value, bool):
            raise ValueError("hide_codes must be a boolean.")
        if key == "auto_hide_seconds" and (type(value) is not int or value not in {0, 30, 60, 300}):
            raise ValueError("Auto-hide must be disabled, 30 seconds, 1 minute, or 5 minutes.")
    return changes
