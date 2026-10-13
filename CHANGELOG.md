# Changelog

## Unreleased

- Fixed PWA update races when a waiting worker activates during the confirmation
  dialog, preserving explicit consent and reloading only once.
- Fixed stale PWA caches reappearing during activation by claiming clients before
  deleting older app caches.
- Fixed Compose validation in fresh GitHub Actions checkouts by creating a local
  configuration fixture from `.env.example`; no private credentials are needed.

## 0.4.0

- Added a separate backend-free browser artifact reusing the modern UI, with
  an encrypted IndexedDB vault, local password unlock, Web Crypto OTP generation,
  compatible imports/exports, and cross-tab atomic HOTP counter advancement.
- Added installable PWAs, original app icons, scoped service workers, explicit
  app updates, and offline browser-mode use. The server worker caches only
  public resources and never private vault responses.
- Added GHCR publishing for AMD64/ARM64 images, downloadable browser artifacts
  on main pushes, and permanent browser ZIP assets on version-tagged releases.
  Publishing workflows do not deploy to a server or GitHub Pages.
- Added a pull-only Compose file and manual deployment/HTTPS/update instructions
  for both artifacts.
- Added browser regressions for cross-language crypto compatibility, encrypted
  persistence, offline reloads, multi-tab counters, local edits, and cache safety.

## 0.3.0

- Added a friendly Basic Auth-backed login page, signed browser sessions, and
  explicit vault locking while preserving Basic Auth for API clients.
- Added optional encrypted browser credential storage with 6-12 digit PIN
  unlock, password fallback, and forgetting saved logins.
- Filled pinned-card stars and added a visually separated pinned group.
- Moved Steam recovery details into the card settings dialog.
- Added click-to-reveal for individual hidden codes without copying.
- Added regression coverage for login/session boundaries, actual encrypted PIN
  persistence and unlock, filled stars, spacing, and individual privacy reveals.

## 0.2.0

- Added editable account names, issuers, token parameters, and optional secret
  replacement without resetting HOTP counters or Steam sessions.
- Added locally bundled Lucide icons, validated raster icon uploads, custom card
  colors, persistent pins, and a pinned-only filter.
- Added encrypted owner preferences for themes, compact density, ordering,
  default code privacy, and idle hiding, plus keyboard shortcuts.
- Added dark, light, and live system-following themes.
- Added explicitly revealed Steam Guard secrets and recovery/revocation codes,
  with copying and automatic clearing after 60 seconds or when hidden.
- Added compatible backup extensions for card appearance and pin state, and
  regression coverage for editing, settings, themes, and timed recovery clearing.

## 0.1.0

Initial alpha release:

- Responsive, dependency-free browser UI with search, privacy mode, countdowns,
  one-click code copying, and explicit destructive-action prompts.
- WinAuth plain-text URI imports and exports, including Steam metadata, HOTP
  counters, Battle.net serials, custom periods, and SHA1/SHA256/SHA512.
- HTTP Basic Auth, CSRF-protected writes, no-store responses, and encrypted
  SQLite persistence.
- Modern Steam credential/email login, existing-session connection, JSON
  confirmations, and manual approve/reject flows.
- Docker Compose deployment and deterministic backend/browser regression tests.

Steam live account compatibility remains unverified; see README limitations.
