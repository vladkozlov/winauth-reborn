# Security policy

This project is an early, single-owner authenticator and has not undergone an
independent security audit. Protect it as carefully as the accounts it unlocks.

## Reporting

If this repository has GitHub **private vulnerability reporting** enabled, use
**Security > Report a vulnerability**. Do not post exploitable details or real
secrets in a public issue. The repository owner should enable private vulnerability
reporting before inviting external users to deploy the application.

Never include authenticator exports, vault keys, databases, Steam cookies,
Authorization headers, passwords, or recovery codes in a report. Use synthetic
fixtures and a minimal reproduction instead.

## Deployment assumptions

- Use TLS and a long, unique Basic Auth password. HTTP Basic Auth is not safe on
  a public plain-HTTP connection.
- Keep the upstream port private. Add rate limiting and, preferably, VPN or
  network access restrictions at the reverse proxy.
- All clients share the same vault; this is not multi-tenant software.
- The friendly login still verifies Basic Auth credentials. Browser sessions
  use signed HttpOnly, SameSite Strict cookies, Secure in HTTPS mode, with a
  12-hour maximum age and restart invalidation. Login assets are public, not
  vault data; mutations remain CSRF-protected.
- Optional PIN unlock stores an AES-GCM-encrypted credential envelope in
  localStorage, using PBKDF2-SHA256 with 600,000 iterations and an origin binding.
  The PIN is never sent to the server or saved. Use it only on trusted devices:
  stolen envelopes allow offline PIN guessing, and a compromised browser can
  capture decrypted credentials. It is not two-factor authentication; client
  retry delays are not a security boundary.
- Lock signs out that browser, not other devices or API clients. It suppresses
  cached Basic credentials only while its signed lock cookie remains valid.
  Forgetting a saved login deletes the local envelope, not server credentials.
- The server PWA worker caches only public login/installation/offline assets.
  Private HTML, API responses, codes, credentials, and Steam sessions are not
  cached. Installing the server edition does not provide offline vault access.
- The separate browser edition encrypts accounts and preferences in IndexedDB
  using AES-GCM-256 and PBKDF2-SHA256 with 600,000 iterations. The strong local
  vault password and derived key are never persisted; the key stays in memory
  until that tab is locked or closed. This is distinct from remembered server
  PIN credentials. Locking one browser tab does not revoke other unlocked tabs.
- A stolen browser vault permits offline password guessing. A compromised
  browser, trusted origin, or static hosting supply chain can capture the
  password and decrypted secrets when the vault is unlocked. Use a dedicated
  trusted origin and keep application files read-only to the web server.
- Browser storage is not a backup: clearing site data, eviction, private mode,
  or losing the password can lose accounts. Maintain private WinAuth exports.
  No browser secrets are synced or sent to the static host. Browser code
  generation trusts the device clock, and Steam confirmations are unavailable.
- Code hiding and idle privacy are visual controls, not authentication locks.
  Steam recovery details require an explicit authenticated, CSRF-protected reveal
  and clear from the UI when closed, hidden, or timed out; copied clipboard values
  remain sensitive.
- The running host holds the encryption key. Encryption at rest does not protect
  against a compromised server, process, backup containing both files, or browser.
- Keep Python, containers, and dependencies patched. Use a non-root service user,
  a persistent private data directory, and encrypted off-host backups.
- Steam endpoints are unofficial and may change. No confirmation should ever be
  approved without the owner's deliberate action.

See the README for exact storage and deployment behavior.
