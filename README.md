# Alshival.Ai

Standalone Linux desktop boards, notes, resources, logs and monitoring. No account, Docker, PostgreSQL server or cloud configuration is needed for local work. Ubuntu 26.04 amd64 is the initial target. Licensed Apache-2.0.

## Development

Requires Node 22.19+, Python 3.12+, python3-jsonschema and a Linux desktop.

```
npm ci
npm run validate
npm start
npm run package:deb
```

Install the generated `.deb` with `sudo apt install ./build/alshitasks_0.1.3_amd64.deb`. Run `alshitasks` from your application menu. First launch provisions SQLite WAL databases and a per-user service, then enables it at login. Closing the window leaves it running. Pause/stop controls are in Settings and `alshitasks pause`, `resume`, `stop`. Systemd lingering is never enabled. Without user systemd, launch starts a detached service for the current login session.

Each Linux user has separate `$XDG_DATA_HOME/alshitasks`, `$XDG_CONFIG_HOME/alshitasks`, and `$XDG_RUNTIME_DIR/alshitasks` directories (0700), with a private Unix socket (0600) and peer-UID checks. Credentials require a working, unlocked Linux Secret Service. No plaintext fallback exists.

## API and MCP

See [local API](docs/api.md), [architecture](docs/architecture.md), [acceptance](docs/acceptance.md), and [source provenance](docs/provenance.json).

`alshitasks mcp` runs stdio MCP, read-only by default. Grant explicit write access:

```
alshitasks grant --scopes project:read,project:write --name 'My agent'
alshitasks mcp --grant GRANT_ID_FROM_PREVIOUS_COMMAND
```

The token lives in Secret Service; the command prints only its entry ID. To ingest logs, create a resource-scoped token in board settings or through the CLI with `--resource UUID --scopes logs:write`. Enable a loopback HTTP port in Settings, then restart the service. Every HTTP request requires a scoped Bearer token; loopback is not authorization.

## Optional portal connection

Connections opens system-browser OAuth with PKCE and a native loopback callback. The matching portal implementation uses the distinct `/desktop/v1` resource audience. Existing workspace MCP tokens cannot access it. Choose workspaces explicitly; local boards never upload automatically. Offline task changes retain revision bases and idempotency keys; overlapping changes become conflicts, permission-rejected work becomes visible drafts, and incomplete snapshots never delete local records. Connections require the matching portal release and individual browser consent.

## Recovery

Settings exports SQLite-consistent backups plus all managed attachments and logs with SHA-256 checks. `alshitasks backup PATH.zip` does the same. Stop the service before `alshitasks restore PATH.zip`. Restore verifies all files and SQLite integrity before replacing state; prior files remain in `before-restore-*`. Restored connections are disconnected and local execution settings require setup again. Credentials stay in Secret Service and are not exported. Uninstall removes application code only; XDG user data is retained.

Public release requires clean-machine installation and a data-preserving upgrade rehearsal. Do not update the team provisioning package before pilot acceptance. Portal billing, tickets, private chat and administration are outside this app.

Team installations may supply a non-secret, readable `/etc/alshitasks/team.json` containing `{"portal_url":"https://alshival.ai"}`. Each user still authorizes their own account in the system browser. No keys or shared credentials belong in this file.
