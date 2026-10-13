from __future__ import annotations

import hashlib
import json
from pathlib import Path

PUBLIC_ASSETS = {
    "app.css",
    "login.css",
    "login.js",
    "pwa.css",
    "pwa.js",
    "offline.html",
    "pwa/icon-180.png",
    "pwa/icon-192.png",
    "pwa/icon-512.png",
    "pwa/maskable-512.png",
}


def manifest(browser_mode: bool = False) -> dict:
    return {
        "id": "../",
        "name": "WinAuth Reborn Browser" if browser_mode else "WinAuth Reborn",
        "short_name": "WinAuth",
        "description": "Encrypted browser-local authenticators"
        if browser_mode
        else "Private self-hosted authenticators",
        "start_url": "../",
        "scope": "../",
        "display": "standalone",
        "background_color": "#101214",
        "theme_color": "#101214",
        "icons": [
            {"src": "pwa/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "pwa/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "pwa/maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }


def fingerprint(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:20]


def manifest_text(browser_mode: bool = False) -> str:
    return json.dumps(manifest(browser_mode), indent=2) + "\n"
