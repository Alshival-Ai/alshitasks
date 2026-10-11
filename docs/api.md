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

`alshitasks mcp` implements newline JSON-RPC stdio with initialize, ping, tools/list and tools/call. The extracted 13-tool project contract is in `contracts/project-tools.v1.json`; log and health queries add two read-only tools. Default access is read-only; explicit write grants are stored in Secret Service with `alshitasks grant`. SSH resource metadata never adds a command-execution capability.

Connected resource logs: `GET /v1/portal/logs?resource_id=LOCAL_UUID` uses the signed-in user’s live permissions and maps the cached resource ID. `GET /v1/portal/monitoring?resource_id=LOCAL_UUID` reads permitted portal health metadata. These are desktop-owner operations and fail offline rather than borrowing a managed principal.
