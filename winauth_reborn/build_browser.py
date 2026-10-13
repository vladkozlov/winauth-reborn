from __future__ import annotations

import argparse
import shutil
from importlib.metadata import distribution
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .appearance import ICONS
from .preferences import DEFAULT_SETTINGS
from .pwa import fingerprint, manifest_text


def build(output: Path) -> Path:
    source = Path(__file__).parent
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "Choose an empty output directory; the build never deletes existing files."
        )
    output.mkdir(parents=True, exist_ok=True)
    environment = Environment(
        loader=FileSystemLoader(source / "templates"),
        undefined=StrictUndefined,
        autoescape=select_autoescape(["html"]),
    )
    environment.globals["url_for"] = lambda endpoint, filename: "./static/" + filename
    for file in (source / "static").rglob("*"):
        if file.is_file() and file.name not in {"login.js", "login.css", "offline.html"}:
            target = output / "static" / file.relative_to(source / "static")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
    html = environment.get_template("index.html").render(
        csrf="", settings=DEFAULT_SETTINGS, icons=ICONS, browser_mode=True
    )
    (output / "index.html").write_text(html, encoding="utf-8")
    (output / "static" / "manifest.webmanifest").write_text(
        manifest_text(browser_mode=True), encoding="utf-8"
    )
    (output / ".nojekyll").touch()
    license_file = source.parent / "LICENSE"
    if not license_file.is_file():
        package = distribution("winauth-reborn")
        candidates = [
            file
            for file in package.files or []
            if file.name == "LICENSE" and any(part.endswith(".dist-info") for part in file.parts)
        ]
        if not candidates:
            raise RuntimeError("The installed distribution is missing its MIT license.")
        license_file = Path(package.locate_file(candidates[0]))
    shutil.copyfile(license_file, output / "LICENSE")
    files = [file for file in output.rglob("*") if file.is_file()]
    worker = environment.get_template("service-worker.js.j2").render(
        mode="browser",
        fingerprint=fingerprint(files),
        assets=[
            "./" + file.relative_to(output).as_posix()
            for file in sorted(files)
            if not file.name.startswith(".")
        ],
    )
    (output / "service-worker.js").write_text(worker, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description="Build the backend-free WinAuth browser PWA.")
    parser.add_argument("--output", type=Path, default=Path("dist/browser"))
    args = parser.parse_args()
    print(f"Browser PWA built in {build(args.output).resolve()}")


if __name__ == "__main__":
    main()
