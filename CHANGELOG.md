# Changelog

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
