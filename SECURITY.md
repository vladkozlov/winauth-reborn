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
- The running host holds the encryption key. Encryption at rest does not protect
  against a compromised server, process, backup containing both files, or browser.
- Keep Python, containers, and dependencies patched. Use a non-root service user,
  a persistent private data directory, and encrypted off-host backups.
- Steam endpoints are unofficial and may change. No confirmation should ever be
  approved without the owner's deliberate action.

See the README for exact storage and deployment behavior.
