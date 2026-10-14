# Alshival.Ai

Standalone Windows and Linux desktop boards, notes, resources, logs and monitoring. No account, Docker, PostgreSQL server or cloud configuration is needed for local work. Supports Windows 10/11 x64 and Ubuntu 26.04 amd64. Licensed Apache-2.0.

Workspace icons and colors are bundled and saved locally, including for synced
workspaces. Settings work offline. Authorized shared edits synchronize in the
background; portal outages never block local saves or task and note sync.
Users without workspace management permission can customize their device's icon.
These personal choices stay local until explicitly shared with **Sync to workspace**.
Concurrent portal edits are preserved for review in workspace settings, with
options to use the portal icon, keep the local choice, or sync it when permitted.

## Assistant plugins

Use **Alshival Local** to connect a desktop assistant to this app without a portal
account. The [plugin setup guide](plugins/alshival-local/README.md) includes quick
Windows and Linux setup, an optional read-only connection, and a local Codex
marketplace. Use the separate **Alshival** portal plugin for OAuth team access.
Neither plugin enables synchronization automatically.

## Windows

Install `alshitasks_<version>_x64_setup.exe` from the GitHub release. It installs for the current user without administrator privileges and includes Python and its dependencies. Launch **Alshival.Ai** from Start. Upgrades and uninstall preserve local data in `%LOCALAPPDATA%\Alshival.Ai`.

Closing the window keeps the app in the notification area and leaves its background service running. Settings controls startup at login, restart and stop. Monitoring checks and forwarding destinations have individual controls. Windows uses per-user ACLs, a random authenticated loopback endpoint, and user-bound DPAPI for saved credentials. Browser requests and unauthenticated local requests are rejected. Linux keeps Unix peer authentication, Secret Service and systemd.

Use the MCP configuration shown in Settings; Windows supplies absolute paths to the bundled Python and CLI. For terminal commands, run `resources\alshitasks.cmd` inside the installation directory. Windows account connections use browser OAuth; managed Ubuntu enrollment remains Linux-only. SSH requires Windows OpenSSH Client.

To build on Windows: install Node 22.19+ and Python 3.13, run `npm ci`, `python -m pip install -r service/windows-requirements.txt`, `npm run validate`, then `npm run package:windows`. Run `node scripts/windows-desktop-test.mjs` to exercise the packaged app with isolated data. Release CI tests both platforms before publishing either installer.

## Development

Linux development requires Node 22.19+, Python 3.12+, python3-jsonschema and a Linux desktop.

```
npm ci
npm run validate
npm start
npm run package:deb
```

Install the generated `.deb` with `sudo apt install ./build/alshitasks_0.1.3_amd64.deb`. Run `alshitasks` from your application menu. First launch provisions SQLite WAL databases and a per-user service, then enables it at login. On Ubuntu with AppIndicator support, closing the window hides the desktop app in the top bar. The dock icon or tray menu reopens the same window. Tray → Quit desktop app exits the desktop while background sync continues. Without a tray host, closing exits the desktop normally. Device settings → Startup → Start at login minimized optionally starts the desktop in the tray at Ubuntu sign-in (off by default, per user and computer); ordinary dock launches always show the window. Stop service is available in Settings and `alshitasks stop`. The former `pause` and `resume` commands return an actionable compatibility error; disable monitoring checks or forwarding destinations individually, or disconnect the optional portal account. Systemd lingering is never enabled. Without user systemd, launch starts a detached service for the current login session.

Each Linux user has separate `$XDG_DATA_HOME/alshitasks`, `$XDG_CONFIG_HOME/alshitasks`, and `$XDG_RUNTIME_DIR/alshitasks` directories (0700), with a private Unix socket (0600) and peer-UID checks. Credentials require a working, unlocked Linux Secret Service. No plaintext fallback exists.

## API and MCP

See [local API](docs/api.md), [architecture](docs/architecture.md), [acceptance](docs/acceptance.md), and [source provenance](docs/provenance.json).

`alshitasks mcp` runs stdio MCP with full local-owner project access by default.
Use `alshitasks mcp --read-only` to restrict it, or create a scoped grant:

```
alshitasks grant --scopes project:read,project:write --name 'My agent'
alshitasks mcp --grant GRANT_ID_FROM_PREVIOUS_COMMAND
```

The token lives in Secret Service; the command prints only its entry ID. To ingest logs, create a resource-scoped token in board settings or through the CLI with `--resource UUID --scopes logs:write`. Enable a loopback HTTP port in Settings, then restart the service. Every HTTP request requires a scoped Bearer token; loopback is not authorization.

## Optional portal connection

Settings → Alshival.Ai account opens system-browser OAuth with PKCE and a native loopback callback. The matching portal implementation uses the distinct `/desktop/v1` resource audience. Existing workspace MCP tokens cannot access it. The workspace switcher includes Local and every authorized workspace. Local boards upload only through an explicit Publish board action. Offline task changes retain revision bases and idempotency keys; overlapping changes become conflicts, permission-rejected work becomes visible drafts, and incomplete snapshots never delete local records. Account sync requires the matching portal release and individual browser consent.

## Recovery

Settings exports SQLite-consistent backups plus all managed attachments and logs with SHA-256 checks. `alshitasks backup PATH.zip` does the same. Stop the service before `alshitasks restore PATH.zip`. Restore verifies all files and SQLite integrity before replacing state; prior files remain in `before-restore-*`. Restored connections are disconnected and local execution settings require setup again. Credentials stay in Secret Service and are not exported. Uninstall removes application code only; XDG user data is retained.

Public release requires clean-machine installation and a data-preserving upgrade rehearsal. Do not update the team provisioning package before pilot acceptance. Portal billing, tickets, private chat and administration are outside this app.

Sign-in uses the fixed Alshival.Ai portal origin. No URL configuration is needed; every user authorizes their own account in the system browser.

### Automatic stable releases

Every push to `main` builds and tests Windows and Debian packages before publishing a release. CI uses the package version when it exceeds all existing release versions; otherwise it allocates the next patch version. Generated package/lock versions stay in CI, and the immutable release points to the exact source commit. Rerunning an already published commit skips publication. Failed builds do not publish. Alshival machines install published releases through their system updater; open desktop windows load the new UI when reopened.

### Default local-owner MCP access

`alshitasks mcp` can manage the current user's tasks, notes, resources, boards, taxonomy and relationships through the private Unix socket. It uses the service's peer-UID ownership check and needs no keyring or additional token. It exposes the existing MCP catalog only, not arbitrary HTTP routes or command execution. No HTTP port is opened. Portal workspace permissions, revision checks and sync conflicts still apply. `--read-only` explicitly restricts the connection; `--grant ID` uses that credential's scopes instead of owner access. These options are mutually exclusive. Run the per-user service before launching MCP.
# Browser/server distribution

Run the shared workspace UI and MCP on a headless machine with an independent
owner login and optional portal sync. See [Alshitask Server setup](docs/server.md)
for Windows development, Docker, private SSH access, HTTPS, and backups.
