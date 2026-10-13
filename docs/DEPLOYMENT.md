# Deploy the published artifacts

GitHub Actions **builds and publishes only**. It does not SSH to a server, deploy
containers, configure Pages, or change your live installation. All deployment
steps below are manual.

## What appears on GitHub

Push to `main` to run **Publish Docker and browser PWA**. Both editions must pass
the regression suite before publication.

| Artifact | Location | Contents |
| --- | --- | --- |
| Server container | `ghcr.io/owner/repository` | Python server and web UI, for `linux/amd64` and `linux/arm64` |
| `winauth-browser` | Workflow run > Artifacts | Static PWA files with `index.html` at the archive root |

Replace `owner/repository` with your **lowercase** GitHub owner and repository.
There is no hardcoded registry account or deployment target.

On `main`, images receive `latest`, `main`, and `sha-<commit>` tags. A version tag
such as `v0.4.0` also publishes `0.4.0` and `0.4` image tags and attaches
`winauth-browser.zip` to a GitHub Release for a durable download:

```sh
git tag v0.4.0
git push origin v0.4.0
```

Workflow artifacts follow GitHub's retention policy and normally require GitHub
sign-in to download. Release assets in a public repository can be downloaded by
anyone. Use a version tag or image digest for a reproducible deployment.

### One-time GitHub setup

Enable Actions and ensure repository/organization policy permits package
publication. The workflow uses `GITHUB_TOKEN`, with package write permission
restricted to the image job; no Docker Hub password or server secret is needed.

**For anonymous image pulls:** after the first publish, open the GHCR package's
**Package settings > Change visibility > Public**. A new package may be private
even when its repository is public. Public repository visibility alone is not
an assurance of public package visibility. Private packages require
`docker login ghcr.io` using a token with `read:packages` and appropriate access.
Changing package visibility is a manual GitHub setting, not a deployment action.

## 1. Server edition: pull and run the Docker image

This edition stores the encrypted vault on the server. Your authenticated devices
share accounts and settings. It supports Steam confirmations and requires the
server to be reachable, including when installed as a PWA.

Requirements: Docker Engine with Compose, a domain, and an HTTPS reverse proxy.
The same image reference works on ordinary x86-64 servers and ARM64 machines.

1. Download `compose.ghcr.yaml` and `.env.example` from the same repository release,
   and place them in a private deployment directory.
2. Copy `.env.example` to `.env` **only for a new deployment**. Set a long, unique
   `WINAUTH_PASSWORD` (or `WINAUTH_PASSWORD_HASH`), `WINAUTH_USERNAME`, and
   `WINAUTH_HTTPS_ONLY=true` for HTTPS deployment. Add:

   ```dotenv
   WINAUTH_IMAGE=ghcr.io/owner/repository:0.4.0
   ```

3. Protect the file and start the published image:

   ```sh
   chmod 600 .env
   docker compose -f compose.ghcr.yaml pull
   docker compose -f compose.ghcr.yaml up -d
   ```

Compose binds to `127.0.0.1:8080`, runs the application unprivileged, and keeps the
vault in the `vault` named volume. No source checkout or image build is needed.

### Optional outbound Steam SOCKS proxy

If Steam rate-limits the VPS's public IP, you can route the app's Steam traffic
through a SOCKS5h proxy running on a trusted machine, such as a Raspberry Pi at
home. Establish a private network tunnel such as WireGuard between the VPS and
the Pi, and make the proxy reachable only over that tunnel. **Do not expose an
unauthenticated SOCKS port to the public internet.**

Add the proxy's tunnel address to the server's private `.env`:

```dotenv
WINAUTH_STEAM_PROXY=socks5h://10.10.0.2:1080
```

Use `socks5h` (not `socks5`) so Steam hostnames are resolved by the proxy side.
The setting applies to Steam login, time sync, and confirmation requests; normal
browser traffic to WinAuth is unaffected. Recreate the container after changing
`.env`. A proxy does not bypass Steam limits, and requests can still be throttled
if the home IP or Steam account is rate-limited.

### HTTPS with Caddy

On the same server, use:

```caddyfile
auth.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Point DNS to the server, allow ports 80/443 for Caddy, and keep port 8080 private.
Serve the server edition at the origin root, not a subpath. Open
`https://auth.example.com`, sign in using your configured Basic credentials,
and optionally enable encrypted browser PIN unlock.

`WINAUTH_HTTPS_ONLY=true` enables Secure cookies and HSTS; it does not create TLS
certificates. Leave it false only for localhost HTTP testing. Add ingress rate
limiting or a VPN appropriate to your environment.

### Update an existing installation without losing its vault

Keep its deployment directory, `.env`, Compose project name, and named volume.
Update `WINAUTH_IMAGE`, then repeat `pull` and `up -d`. If switching from the
source-build `compose.yaml`, use `compose.ghcr.yaml` **in the same directory with
the same project name** so Compose reuses the existing `vault` volume. If you
previously used `-p`, continue using that same name.

Do not use `down -v`, rename the project, or overwrite `.env` with the example.
Back up `vault.sqlite3` and `vault.key` together while the server is stopped.
The new container uses the same encrypted vault format; rebuilding or pulling an
image does not migrate your accounts to another storage location.

## 2. Browser edition: serve the static PWA

This artifact is entirely HTML/CSS/JavaScript and images. There is **no Python
runtime, API, Basic Auth server, or backend database**. Your web server delivers
static files only. Authenticators and preferences are encrypted in IndexedDB
on each browser/profile; devices do not synchronize.

It supports WinAuth `.txt` import/export, TOTP/HOTP/Steam Guard generation, card
editing, icons, colors, pins, preferences, code copying, and Steam recovery data.
Steam trade/market confirmations remain server-only because Steam's endpoints
do not allow direct browser requests. Codes use the device's system clock:
enable automatic date/time.

### Download and publish the files

Download `winauth-browser.zip` from the GitHub Release, or the `winauth-browser`
artifact from a workflow run. Extract **the whole archive**, including `static/`,
`service-worker.js`, and the license, into a new web directory:

```sh
mkdir -p /srv/winauth-browser/releases/0.4.0
unzip winauth-browser.zip -d /srv/winauth-browser/releases/0.4.0
ln -sfn /srv/winauth-browser/releases/0.4.0 /srv/winauth-browser/current
```

Make those files readable by your web server, but do not give the web server
write access to the application files. No `.env`, server vault, or private export
belongs in this directory.

### HTTPS with Caddy

Use a **separate, trusted origin** from the server edition:

```caddyfile
codes.example.com {
    root * /srv/winauth-browser/current
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        X-Frame-Options DENY
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    }
    @app_entry path / /index.html /service-worker.js
    header @app_entry Cache-Control "no-cache"
    header /static/manifest.webmanifest Content-Type "application/manifest+json"
    file_server
}
```

If using another server, serve `.webmanifest` as `application/manifest+json` and `.js` as
`application/javascript` or `text/javascript`; serve actual missing files as 404,
not an HTML rewrite. The worker lives at the artifact root and needs that scope.

The artifact also works under a stable subdirectory such as
`https://example.com/winauth/`: extract the files there and open the URL **with a
trailing slash**. All app assets, manifest paths, and worker scope are relative.
Do not move just the worker or rewrite its scope to the entire host.

HTTPS is required except on localhost. Uploading the folder and opening
`index.html` with `file://` does not work. A simple local preview is:

```sh
cd /path/to/extracted/artifact
python3 -m http.server 8000 --bind 127.0.0.1
# Open http://localhost:8000/
```

Python here is only a convenient static file server, not the app's backend.

### First use and offline use

Open the app online and create a strong vault password of at least 12 characters.
This is the **local encryption password**, not your server credentials. It is not
saved, sent to the host, or recoverable. Import your original WinAuth `.txt`
export. Wait for **Offline app ready** before disconnecting.

After the service worker has installed, the app can reload, unlock, generate,
edit, and export codes without network access. Reloading or **Lock vault**
clears the in-memory unlock key and requires the password again. Locking one
tab does not lock other already-unlocked tabs. HOTP mutations are serialized
across tabs using Web Locks.

Export private `.txt` backups frequently. Clearing browser/site data, removing
a browser profile, private browsing, or storage eviction can remove the vault.
App installation is **not** a backup. If the password is lost, only a previously
exported backup can recover the accounts; there is no reset that preserves the
encrypted data.

Use a current browser with Web Crypto, IndexedDB, Web Locks, and service workers.
Storage is tied to the origin, browser/profile, and app path. A domain/path
change, a different browser, or a new device needs a separate import. Home-screen
apps may have a separate storage container on some platforms; verify your
accounts after installation and import your backup if necessary.

### Update the static app

Extract a new version into a **new** release directory, then switch the `current`
symlink to it. Keep the same public origin and app path: the IndexedDB vault is
not part of the static files and remains unchanged.

Open the app online. A newly installed service worker offers **Update available**;
accept when ready to discard open dialogs and re-unlock. Alternatively, closing
all app tabs/windows allows the waiting worker to activate. Do not clear site
data merely to update the app: that can delete your vault.

## Install either edition on a phone or PC

- **iPhone/iPad:** open the HTTPS URL in Safari, then **Share > Add to Home
  Screen** (or the corresponding Add to Home Screen option).
- **Android/desktop Chromium:** use **Install app** in the app or the browser's
  install menu.
- Other browsers/platforms vary in installation support; the app remains usable
  as a normal web page. Firefox desktop does not provide Chromium's native PWA
  installation UI.

The server PWA shows a safe offline notice when its server is unavailable; it
never caches private responses. The browser PWA caches only static resources,
while the vault stays encrypted in IndexedDB. Neither edition implements cloud
sync, background Steam approvals, push notifications, or automatic server deployment.
