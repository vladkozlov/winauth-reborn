# Contributing

Use synthetic authenticator secrets, Steam sessions, and account names in issues,
tests, screenshots, and pull requests. Never attach your real WinAuth export,
`.env`, vault database/key, Steam cookie, or recovery code.

## Local development

Use Python 3.11+ and an isolated virtual environment:

```sh
python -m pip install -e '.[dev]'
playwright install chromium
python -m pytest
ruff check .
ruff format --check .
node --check winauth_reborn/static/app.js
```

Keep changes focused. Add deterministic tests for parser compatibility, cryptography,
storage transactions, and API behavior. Steam tests must mock network responses:
CI must never approve a real trade or connect to an account. Browser tests exercise
the actual UI against an ephemeral local server and a temporary encrypted vault.

The GitHub workflow runs lint, syntax checks, backend/browser tests, and package
builds, and separately builds the container. CI uses Python 3.11 and 3.13.

## Release checklist

1. Run the full checks and confirm CI passes.
2. Review tracked files for secrets and personal data. `.env`, `data/`, build
   outputs, and virtual environments must stay untracked.
3. Test `docker compose up --build` with disposable data and unique credentials.
   Verify HTTPS at the intended reverse proxy and back up the vault/key together.
4. If claiming live Steam compatibility, verify login and a deliberately chosen
   confirmation with a consenting account. Do not infer this from mocked tests.
5. Update `CHANGELOG.md` and the version in `pyproject.toml`; publish a tagged
   GitHub release describing known limitations. Do not publish credentials or
   plaintext authenticator exports as release assets.

The project is a fresh implementation under the MIT license. Do not copy code
from the GPL-licensed WinAuth application without addressing license compatibility.
