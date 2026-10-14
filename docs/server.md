# Alshitask Server

The same workspace UI and MCP tools can run without Electron. Each server has
one owner. Portal sync is optional; team collaboration remains in Alshival.Ai.
Server instances have their own storage and must not share a live desktop data directory.

## Native setup (Windows or Linux)

From the repository, install Node dependencies, build the UI, and create a Python
3.13 virtual environment. On Windows substitute `.venv\Scripts\python.exe` for
`.venv/bin/python` below.

```sh
npm ci
npm run build
python -m venv .venv
.venv/bin/python -m pip install -r service/server-requirements.txt
.venv/bin/python service/alshitasks-cli.py server-setup --data-dir ./server-data --key-file ./server-keys/master.key
.venv/bin/python service/alshitasks-cli.py server --data-dir ./server-data --key-file ./server-keys/master.key --base-url http://127.0.0.1:43120
```

Setup prompts for an owner password of at least 12 characters. There is no default
password or public registration. Open `http://127.0.0.1:43120`, sign in, and use the
workspace. Stop the foreground process with Ctrl+C. The server also supports
the existing `--root ./server-data status`, `backup`, `stop`, and `mcp` commands.

On Windows, after installing dependencies, `powershell -File scripts/start-server.ps1`
combines interactive setup and startup. It reuses an existing owner and keeps the
server running in that terminal. Password input is hidden; mismatches stay at
the prompt so you can retry.

Browser sessions renew for 30 days of activity. To recover access, stop this
instance and run `server-password --data-dir ./server-data`; this invalidates all
browser sessions. It does not disconnect portal sync or revoke agent tokens.

## Docker on Ubuntu or an AWS VM

The image is `ghcr.io/alshival-ai/alshitasks-server`. Pin a release tag or digest
for deployments. `compose.server.yml` binds only to loopback by default.

```sh
docker compose -f compose.server.yml build
mkdir -p server-keys
sudo chown 10001:10001 server-keys
docker compose -f compose.server.yml run --rm -v "$PWD/server-keys:/run/secrets" alshitasks server-setup --data-dir /data --key-file /run/secrets/master.key
docker compose -f compose.server.yml up -d
```

The setup override mounts the key directory writable; the running service mounts
it read-only. The application runs as UID/GID 10001. Preserve the named data volume
and `server-keys` directory when replacing the container. Do not run replicas
against the same volume: SQLite and workers are owned by one backend process.

For the private Ubuntu acceptance instance, publish host port 43121 and set
`ALSHITASKS_BASE_URL=http://127.0.0.1:43121`. On the Windows machine:

```sh
ssh -N -L 127.0.0.1:43121:127.0.0.1:43121 Alshival
```

Open `http://127.0.0.1:43121`. The browser and portal callback use this same tunnel.
No public port, DNS change, or modification to installed desktop users is needed.

## HTTPS and proxy configuration

For internet access set the base URL to the exact HTTPS origin, e.g.
`https://tasks.example.com`, terminate TLS at Nginx, and keep the backend port
private. Mount valid certificates and configure:

```nginx
server {
    listen 443 ssl;
    server_name tasks.example.com;
    ssl_certificate /etc/letsencrypt/live/tasks.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tasks.example.com/privkey.pem;
    client_max_body_size 30m;
    location / {
        proxy_pass http://127.0.0.1:43120;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
```

Native deployments can set `--trusted-proxy 127.0.0.1`; containers must use the
actual fixed proxy address. Do not trust all forwarded IPs. Authentication,
same-origin checks, CSRF protection, and host validation remain in the app.
Public HTTP origins are rejected. TLS certificates are not managed by Alshitask.

## MCP, resource logs, and portal connection

Settings → Remote MCP access creates named, revocable bearer tokens. Read access
is the default; enable workspace edits explicitly. Expiry is optional for
unattended clients. Copy the generated configuration once and store the token
in the client's credential store. The endpoint is `<base-url>/mcp` and uses
Streamable HTTP. Local stdio remains available. Clients requiring OAuth-based
MCP sign-in are not supported by server v1.

Resource-scoped sender tokens support the existing workspace log/OTLP routes
and heartbeat API on this server origin. Agent tokens never grant browser-owner
settings access, and browser cookies never grant MCP access.

Choose Connect Alshival.Ai in Settings to authorize portal sync. The callback is
`<base-url>/oauth/callback`; the configured origin must match the browser address.
For a tunneled instance the tunnel must remain connected during sign-in. Hosted
instances use an exact HTTPS callback. PKCE and the owner browser session bind
the authorization; a restart cancels unfinished sign-ins. Connected instances
reuse the desktop's rolling 45-day refresh window and interrupted-refresh
recovery. Portal outages leave local work available.

## Backups, upgrades, and recovery

Browser Export downloads the existing validated workspace backup format: tasks,
attachments, and logs. Server passwords, sessions, portal secrets, and the master
key are excluded. Keep the master key separately from workspace exports and
back up the complete private instance directory for operator-level recovery.
Uploaded SSH keys remain protected files in instance configuration, as required
by OpenSSH; they are not included in workspace exports.

Before upgrading, export a backup and record the image digest. Stop the old
container, replace it, and verify `/healthz`, browser login, and sync before
removing recovery copies. Never restore an older database over new writes.

For workspace restore, stop the server and run:

```sh
.venv/bin/python service/alshitasks-cli.py --root ./server-data restore ./backup.zip
```

Restore validates and stages the backup, retains a recovery copy, and requires
connections and grants to be configured again. Losing the master key cannot be
fixed by resetting the owner password: restore the original key, or recover a
workspace export into a fresh instance and reconnect integrations.

## Validation

Install server requirements and run `python -m unittest discover -s tests -v`,
`npm run build`, and `node scripts/server-browser-test.mjs`. Set
`ALSHITASKS_TEST_PYTHON` to the virtual-environment interpreter. Install Playwright
Chromium or set `ALSHITASKS_BROWSER_CHANNEL=msedge` on Windows. The browser test
creates its own disposable instance and never adopts desktop enrollment.

The live sync acceptance workspace is **Ketzia's Styling Salon**, with owner
authorization. Use clearly labeled temporary records and remove only those
records after testing; do not modify existing customer content.
