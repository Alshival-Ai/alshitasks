# Local API and MCP

The service exposes the same JSON `/v1` routes over a private Unix socket and, when explicitly enabled, `127.0.0.1:PORT`. The socket validates the peer UID. Packaged desktop IPC is restricted to the main frame, request method, route and bounded payload. HTTP clients require a scoped Bearer token on every request; browser-origin and chunked requests are rejected. There is no unauthenticated health exemption or remote execution tool.

| Route | Purpose | Authorization |
|---|---|---|
| GET/POST `/v1/projects/items` | Paginated listing / create | project:read / project:write |
| GET/PATCH `/v1/projects/items/UUID` | Read / revision-checked edit | project:read / project:write |
| POST `/v1/projects/items/UUID/actions` | Archive, Trash, restore, approval | project:write |
| GET `/v1/projects/items/UUID/history` | Activity | project:read |
| GET `/v1/projects/boards`, `/members`, `/statuses`, `/edges` | Catalogs | project:read |
| POST `/v1/projects/statuses`, `/edges` | Status / relationship changes | project:write |
| DELETE `/v1/projects/edges/UUID` | Relationship tombstone | project:write |
| POST `/v1/logs` | Bounded ingestion | logs:write, exact resource grant |
| GET `/v1/logs` | Bounded query | logs:read, optional resource grant |
| GET `/v1/monitoring` | Confirmed health and incidents | monitor:read |
| POST `/v1/heartbeat` | Collector heartbeat | monitor:heartbeat, exact resource grant |

Item mutations require `idempotency_key`; edits/actions also require the observed `revision`. Reusing a key with different content returns 409. REST errors return `{ "error": { "message": "..." } }` with 400 malformed, 401 authentication, 403 authorization, 404 missing, 409 revision conflict or 413 too large.

Ingestion accepts at most 100 events and 1 MiB per batch:

```json
{"resource_id":"RESOURCE_UUID","events":[{"id":"stable-event-id","timestamp":"2026-10-10T12:00:00Z","severity":"info","message":"Worker started","attributes":{"component":"worker"}}]}
```

IDs deduplicate per resource. Timestamps require a timezone; future timestamps beyond five minutes are rejected. Severities: debug, info, warning, error, critical. Messages are bounded to 16,000 characters and events to 32,000 encoded bytes. The full batch validates before insertion. Query parameters: resource_id, since, until, severity, text, limit (maximum 100). Seven-day/1-GiB retention is the default, configurable in Settings.

Remote collectors need an operator-managed authenticated reverse proxy or SSH tunnel. Keep the app listener on loopback; never automatically add public listeners or firewall rules. Forwarding uses HTTPS without redirects, a resource-scoped credential held in Secret Service, event IDs, exponential retries and a 16-MiB queue. Full queues stop enqueueing remote copies while local ingestion continues.

Owner-only Unix socket operations include settings, credential/grant management, attachments, backups, SSH aliases, monitoring configuration, forwarding, and portal connections. No HTTP grant can invoke these.

`alshitasks mcp` implements newline JSON-RPC stdio with initialize, ping, tools/list and tools/call. The shared project contract is in `contracts/project-tools.v1.json`; log, health and notification queries add read-only tools. Default access is read-only; explicit write grants are stored in Secret Service with `alshitasks grant`. SSH resource metadata never adds a command-execution capability.

Connected resource logs: `GET /v1/portal/logs?resource_id=LOCAL_UUID` uses the signed-in user’s live permissions and maps the cached resource ID. `GET /v1/portal/monitoring?resource_id=LOCAL_UUID` reads permitted portal health metadata. These are desktop-owner operations and fail offline rather than borrowing a managed principal.

## Standalone log collection

No portal login, ClickHouse, or database server is needed. Resource logs live in
`$XDG_DATA_HOME/alshitasks/logs.sqlite` (normally `~/.local/share/alshitasks/logs.sqlite`).
The desktop, API, and local MCP read this same store. Project synchronization does
not upload logs; explicitly configured forwarding remains a separate operation.

In Settings, enable a loopback HTTP port and choose **Save and restart service**.
Open a resource's **Logs → Send logs here** section to create a named, write-only
sender token. Copy it before dismissing it; only its hash is kept for validation.
Revoke a token to stop a sender, or create a replacement and revoke the old one.
Agent MCP grants are managed separately in Settings → Integrations.

`GET /v1/logs` and the `logs_query` MCP tool accept `resource_id`, `workspace_id`,
`since`, `until`, `severity`, `text`, `limit` and `cursor`. Responses contain
`events` and `next_cursor`. Continue with the same filters and the returned
cursor until it is null. Queries are ordered by descending timestamp, then event
ID and resource ID. Refresh to see newer arrivals; pagination is not a frozen
snapshot. Workspace filters are independent of the selected desktop tab.
Resource-scoped grants require their resource ID and cannot query other resources.
`GET /v1/logs/setup?resource_id=UUID` is desktop-owner-only setup metadata.

### Sending from another machine

For example, if the desktop listener is port 8765, run this on the sending machine:

```sh
ssh -N -L 18765:127.0.0.1:8765 desktop-user@desktop-host
```

Send JSON batches to `http://127.0.0.1:18765/v1/logs`, using the resource-specific
Bearer token. The SSH server and access must already be configured by the operator.
The resource's setup panel provides a sender example without embedding its token.

Alternatively, terminate HTTPS at an operator-managed reverse proxy on the desktop
host. A minimal Nginx location inside a TLS-enabled server is:

```nginx
location = /v1/logs {
    limit_except POST { deny all; }
    client_max_body_size 1m;
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Authorization $http_authorization;
    proxy_http_version 1.1;
    proxy_request_buffering on;
}
```

Configure a valid certificate and network access separately. Authentication remains
mandatory at the local API; do not log Authorization headers. The application never
opens firewall ports or creates a public listener. Use a sender that transmits a
Content-Length, as the API rejects chunked requests.

Senders should persist unsent batches and retry connection failures, 5xx responses,
and uncertain acknowledgments with backoff, preserving event IDs. Correct 4xx errors
before retrying. A successful acknowledgment follows a local database commit;
retention can still expire accepted events, including already-old timestamps.
Sleeping, powering off, and logging out can interrupt availability.

The per-user systemd service starts at login after initial setup and keeps running
when the window closes. Settings reports service status and offers restart controls.
Without systemd, the detached fallback has no automatic restart or login startup.
This version does not enable lingering or run a system-wide service.

Application diagnostics are distinct from resource logs:

```sh
journalctl --user -u alshitasks.service
```

The resource viewer defaults to **On this device**. **Portal** appears only for linked
resources and requires a working portal connection; portal failures never silently
substitute local results. Local backups include SQLite resource logs and attachments;
credentials remain outside backups.

### Local monitoring and notifications

Resource → Monitoring manages named local HTTPS, TCP, ICMP, database and heartbeat
checks. `POST /v1/monitoring/checks` accepts `resource_id`, `name`, `required`,
`config`, and (for edits) `id` plus `revision`. Owner-only
`POST /v1/monitoring/actions` accepts `id`, `revision`, and `action`: `run`,
`pause`, `resume`, `maintenance` (with `minutes`), or `end_maintenance`.
`GET /v1/monitoring?resource_id=UUID` returns checks, aggregate resource health,
incidents and transition history; agent readers require `monitor:read`.
Configurations, credentials and local incidents never enter project sync.

Checks default to 120 seconds. Healthy/unhealthy changes require two consecutive
fresh observations. Missing heartbeats, unavailable adapters and stale observations
produce unknown health. Required enabled checks determine resource health; optional
checks retain their own results. Maintenance suppresses local incident notifications.
One incident remains open until confirmed recovery, with no repeated failure toast.
Resuming from a long gap resets confirmation. Probe processes have execution limits,
and revision/lease fencing rejects results from edited or paused checks.

A resource heartbeat token has only `monitor:heartbeat`. POST `/v1/heartbeat` with
`{"resource_id":"UUID","check_id":"UUID"}` and its Bearer token. A sender can
reach the loopback listener through its own SSH tunnel or HTTPS proxy, as for logs.
Database passwords are referenced from Secret Service, never stored in check JSON.
ICMP needs iputils-ping; database adapters are optional package recommendations.

`notifications_read` uses the portal tool's query contract: `query`,
`notification_id`, `filter` (`all`, `unread`, `archived`), `page` (default 1),
`limit` (default 20, maximum 50). It returns `items` and `next_page`, and **never
marks anything read**. HTTP agents use GET `/v1/notifications` with a personal
`notifications:read` grant; resource-scoped grants cannot read the personal inbox.
The default local read-only MCP also exposes the reader without a portal account.
Portal results require a successful current permission refresh; offline agent reads
return local notifications only. The desktop may display its saved portal cache
with a stale indicator and disabled portal actions.

Owner UI operations: POST `/v1/notifications/actions` with `action` and `id`
(`read`, `unread`, `archive`, `restore`), or `read_all`. Opening an alert marks it
read and opens the local resource or its portal destination. POST
`/v1/notifications/refresh` refreshes the optional portal cache. Owner GET responses
also include source metadata, `unread_count`, and portal freshness. Settings exposes
`notification_toasts`; disabling toasts preserves the inbox.

The service persists alerts in the local SQLite database and delivers Linux D-Bus
toasts even with the window closed, while the user session/service remains running.
Each delivery is claimed before calling D-Bus: restart never replays it; a crash or
unavailable notification daemon can lose a toast, but not its inbox entry. A first
portal import is silent. History expires after 90 days, preserving open incidents.
Backups retain inbox/history; restoration suppresses old toasts and disables restored
execution until reconfigured.

Portal notifications use the desktop OAuth audience (`notifications:read` and
`notifications:write`) and `/desktop/v1/notifications`, not the public MCP quota.
Existing manually connected users must reconnect to consent to these scopes. Managed
account credentials gain them when their enrollment refreshes. Portal checks and
email/SMS remain governed by portal settings. Local alerts never generate email/SMS
or upload themselves to the portal. Both sources remain explicitly labeled in the UI.

## Task types and labels

`GET /v1/projects/taxonomy?workspace_id=WORKSPACE_ID` reads the workspace catalogue.
`POST` at that path accepts the `project_taxonomy_write` arguments: `kind` (`type` or
`label`), `operation` (`create`, `update`, `archive`, `default`, `reorder`), current
catalogue `revision`, and an `idempotency_key`. Supply `name`, `icon`, `id`, or
`order` as appropriate. The local MCP exposes `project_taxonomy` and
`project_taxonomy_write`. Writes require a project write grant; connected workspace
catalogues additionally require workspace management permission.

Tasks accept one `type_id` and a list of `label_ids`. Omitted fields preserve the
current selection; `label_ids: []` clears labels. Labels are created in workspace
settings. Archived choices remain visible on existing tasks but cannot be newly
assigned. `project_list` accepts `type_ids` and `label_ids` arrays; the local items
HTTP endpoint accepts comma-separated values. Each filter matches any selected
value, and the two filters combine with AND before pagination.

The portal negotiates `task-taxonomy-v1` via `X-Alshival-Desktop-Features`.
Catalogue snapshots precede tasks. Offline catalogue changes use revisioned outbox
operations. Concurrent catalogue edits require review in workspace settings;
conflicting task edits retain the existing field review. Old portal connections
retain their existing behavior until the capability is available.
