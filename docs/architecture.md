# Architecture and trust boundaries

Windows keeps the shared Python/SQLite service and React renderer. `platform_support.py` restricts state directories to the current user SID and SYSTEM using protected inheritable DACLs. Permanent credentials are DPAPI encrypted for the current user outside backups. The Windows owner transport binds only `127.0.0.1` on an ephemeral port, requires a random 384-bit token published in the private runtime directory, rejects browser-origin requests, and retains scoped grant checks for agent traffic. The optional exporter port remains grant-authenticated and does not accept the owner token. The runtime token is replaced on each service start; the descriptor is removed on clean shutdown.

The Windows installer includes a checksum-pinned Python runtime and native dependencies. A hidden per-user process owns the backend; Electron provides the tray, native toasts, and optional login startup. Quitting the desktop leaves the service running; native Windows toasts require the desktop/tray process. There is no Windows system service or automatic backend crash restart. Managed Ubuntu enrollment stays Linux-only; Windows uses browser OAuth. The Linux transport, keyring, and systemd paths remain in place.

- `desktop/renderer`: extracted Alshival-authored Neural Labs Projects components, served as packaged content. Layout remains in per-device storage. No portal is embedded.
- `desktop/main.cjs` and `preload.cjs`: Electron main process, sandboxed renderer, isolated context and a narrow IPC bridge. External links and OAuth use the system browser. Native dialogs own attachment/backup file selection.
- `service/alshitasks`: per-user Python background service. Standard-library SQLite avoids a native Node ABI dependency. Separate task and log databases use WAL. Migration transactions reject newer schemas rather than downgrading.
- `store.py`: graph identities/revisions, authorship, explicit tombstones, dependency-cycle checks, replay receipts and transactional offline outbox.
  - `security.py`: hashed local grants and Linux Secret Service access. No plaintext credential fallback.
- `portal.py`: optional native OAuth PKCE, exact state/issuer callback validation, authorization-code and refresh resource binding, account/workspace-isolated caches, bounded complete snapshots and three-way merge. Clock timestamps do not determine shared edit winners. Rejected changes remain drafts.
- `monitoring.py`, `logs.py`, `ssh.py`, `files.py`: local execution, incident history, bounded ingestion/forwarding, app-managed aliases and verified backups. These settings and private file paths never enter graph sync.

Signing in never uploads standalone records. All authorized portal workspaces use independent namespaces and stable remote/local ID mappings. User scopes and workspace permissions are checked at the portal service layer on each mutation, including replayed receipts. Desktop credentials have the `/desktop/v1` audience; workspace MCP credentials cannot cross this boundary. The adapter never borrows managed Neural Labs principals.

Installation writes application code only. Initial user launch provisions private XDG state and a user service; it does not enable systemd lingering. Uninstall leaves user data and credentials intact. Backup restore stages and verifies before replacement, preserves prior state, disconnects copied connections and requires local execution/grant setup again.

The matching portal source is maintained separately in the Alshival website repository. Publishing desktop source or building a `.deb` does not deploy that API or register a user's consent.

## Workspace sync and publishing

Discovery refreshes workspace access and server capabilities every 30 seconds.
With `graph-sync-v2`, the desktop consumes consistent, paginated checkpoints and
incremental differences. SSE change notices wake independent workspace workers;
polling and bounded backoff recover missed notices and offline periods. Up to four
workspaces receive SSE simultaneously; additional workspaces retain polling.
No inbound port or webhook is required on the computer. Portal database triggers
maintain section revisions and an invalidation journal. The portal currently
projects authorized snapshots to calculate differences; this is not a per-record
journal replay implementation. Large-workspace latency is not yet benchmarked.
The existing Neural Labs integration is unchanged.

Each queued write persists its exact request before transport. Lost responses retry
that request before calculating a new merge. Independent field changes merge;
overlapping edits require per-field review, with both versions retained. Graph
workers do not wait for attachment transfers. Relationships can cross boards in a
workspace; dependencies require tasks and cannot form cycles. Resource mentions
translate between local and portal identities and maintain their relationships.

Local publication uploads a deterministic archive in resumable 256 KiB chunks. The
portal imports the complete board, statuses, relationships and files atomically,
and saves an idempotent receipt. The desktop rebinds existing local item IDs only
after confirmation. A lost response resumes from the receipt. Edits are locked while
publication is pending; cancellation unlocks only after the portal acknowledges it.
Each publication creates a separate portal board. Historical consolidated board
URLs remain aliases of their original default board.

The bundled MCP server exposes `workspace_list` and accepts `workspace_id` on
project tools. Its selected scope is per request, independent of the desktop UI.
Settings can create a revocable agent grant and copy the local MCP configuration.

## Ordinary attachments and local SSH

Ordinary attachments are private local copies. Local-only boards do not upload them.
On a connected board, new files, replacements and deletions sync with the portal
when `graph-files-v2` is available. Transfers use resumable 256 KiB chunks, SHA-256
verification, stable completion receipts and revision checks. The portal enforces
item access, file size and workspace quota, and scans uploaded bytes before sharing.
A partial download never replaces the completed local copy. Pending uploads expire
after seven days and restart automatically. Conflicting replacements stop for review;
Keep both files publishes a separate local copy and downloads the portal version.
Conflicting deletions offer to retain the changed portal file.

Resource SSH settings belong to this computer. The native file chooser copies a
private key into the user's private application configuration directory with mode
0600. Keys imported through SSH setup never become graph attachments, outbox entries,
portal uploads or app backup contents. Files explicitly chosen under Attachments
are treated as ordinary files, including SSH keys: there is no extension or
private-key-content rejection, and connected-board attachments sync normally.
Users may instead use their SSH agent. Configuration manages `alt-*` aliases in
`~/.ssh/alshitasks.conf` and includes it from `~/.ssh/config`, preserving unrelated
configuration. OpenSSH performs host-key verification. Removing an alias does not
delete imported key material. Another computer requires its own SSH setup.

Source updates alone do not activate the matching portal endpoints. An older portal
keeps ordinary file jobs queued locally until it advertises the file capability.

## Managed Ubuntu account connection

### Browser OAuth renewal

Ordinary Windows/Linux OAuth connections renew automatically with 15-minute access
tokens and a rolling 45-day desktop refresh lifetime. Active servers remain
connected; a device returning within that offline window renews without browser
sign-in. Explicit revocation and account eligibility checks still apply.

Before rotating, the app saves a random `desktop_refresh_id` alongside the current
credential in DPAPI/Secret Service. The portal atomically issues the successor
and an encrypted receipt for that exact request. Lost responses or interrupted
credential saves retry the same attempt, including across restarts. A recovered
expired access token is renewed again before use. The app clears the attempt ID
only after saving the successor. A failure to save the initial attempt prevents
any rotation request. No worker silently discards local records on auth failure.

OAuth `invalid_grant`/`invalid_client` responses stop replay of the rejected
credential and display a reconnect action. Reconnecting the same account keeps
workspace identities and drafts; a different account still requires sign-out.
Already revoked tokens issued before receipt support require one fresh sign-in.

### Managed enrollment

Version 0.1.8 adds an explicit enrolled-computer credential provider. Ordinary
installations still use browser OAuth and Linux Secret Service. On enrolled
Alshival computers the root provisioner owns `/var/lib/alshival/desktop/<uid>`.
The app verifies every directory's ownership and mode, a root-owned identity
descriptor, and the SHA-256 of its private credential file. It accepts no
user-supplied account or environment-variable override for this provider.

The app checks capabilities for the expected account and desktop audience before
activation, then discovers current permitted workspaces. Reconciliation runs at
startup and every 30 seconds while sync is enabled. Token rotation preserves
connection IDs, cursors, conflicts and drafts. Account changes disconnect the old
connections and retain queued work as drafts; local boards never publish merely
because the account connects. Worker authorization generations fence old work.
Settings show Connected through your Ubuntu account; Retry and Pause remain
available, while account switching and permanent sign-out are managed by IT.

Expired, revoked, incomplete or modified provisioning state fails closed. Local
data remains available. A locked keyring does not affect this explicit managed
provider; a failed public OAuth keyring does not activate it. Credentials are never
stored in the app database or backup. Removing managed policy requires an
administrator to stop issuance, revoke its portal enrollment, and explicitly
clear the app's managed-account setting before interactive OAuth is used again.

Named health checks (`health.py`) use independent SQLite configuration, leases,
observations, aggregate health, incidents and transitions. Killable subprocess probes
run at most four at a time outside the database transaction. `notifications.py`
maintains the personal local inbox and account-partitioned portal cache. A single
service-owned D-Bus notifier delivers durable claimed alerts; Electron only receives
notification navigation IDs. `notifications_read` shares the portal's read-only
schema. Portal cache refresh is serialized per store and revalidates live visibility
before agent reads. Account changes fence remote responses. The portal desktop API
only reads/mutates personal notification state; it does not send email or SMS.

### Live delivery

Portals advertising `graph-account-events-v1` use one authenticated outbound SSE
connection at `/desktop/v1/events` for the entire account. No inbound listener,
webhook configuration or additional login is needed. Content-free workspace
revision hints wake checkpoint synchronization; graph data still comes from the
existing authenticated API. Older portals retain per-workspace SSE with fair
rotation and polling fallback.

Heartbeats arrive every 15 seconds; the client times out stalled reads after 45
seconds and reconnects with jitter capped at 30 seconds, respecting Retry-After.
Reconnect schedules every connected workspace for reconciliation. Pause/sign-out
interrupt readers and fence stale account generations. Four graph workers use
fair urgent/poll scheduling; notices received during a pass survive acknowledgement.
Each workspace reconciles every 27–30 seconds even without live delivery. File
transfers and account discovery run separately. Existing conflict review and
unsaved-editor protections remain authoritative.

Settings reads and writes take the store's reentrant lock, including background
credential checks, to prevent concurrent use of SQLite's prepared statements.
Credential refresh never acquires the store lock while holding the token lock.
Background workers are supervised: unexpected errors record retry status, log a
content-free warning, and restart after one second instead of silently stopping
sync. A recovering graph worker is exposed as offline/retrying in sync details.

Settings → Sync details shows delivery health, last sync/notification and retries.
A live-stream failure displays Reconnecting when HTTP sync still works; an HTTP
outage displays Offline—changes saved locally. Initial/full snapshots and binary
file transfers can take longer than a small incremental edit.


## Optional portal isolation

The active account, connection identity/state and authorization generation fence portal
requests and their commits. Disconnect invalidates in-flight results; credential cleanup
runs outside SQLite locks. OAuth registration, renewal and online shared-resource checks
also run outside the local HTTP transaction. SSH still requires current online permission
for a shared resource; a failed check blocks that action without blocking local work.

Monitoring, forwarding, retention, graph synchronization, attachments, inbox polling,
portal event listeners, icons and portal resource-cache refreshes run independently.
Forwarding and resource-cache workers each have at most four active requests. Forwarding
claims a short durable lease and validates the target before sending and committing.
Retention uses its own SQLite connection, leaving local workspace reads independent.

Notification read/unread and archive/restore actions commit locally before returning.
SQLite coalesces pending actions by notification and field, with a unique version for each
new action. A worker acknowledges only the version sent. Refreshes overlay pending local
fields and retain pending notifications when remote pagination omits them. Retry state
survives restart and account changes never replay another account's queue. Agent reads
use authorized portal cache no older than 90 seconds; otherwise they return local alerts
and schedule asynchronous refresh. Explicit permission failures invalidate authorization.

Portal logs and monitoring return account/connection-scoped cached payloads plus `cache`
metadata (`state`, `stale`, `updated_at`, `pending`, `error`). Reads schedule bounded refresh
work. Empty caches report loading/unavailable while local data remains usable.

Global pause is retired. Existing paused installations migrate enabled checks and forwarding
destinations to disabled, preserving configuration and queues. A persistent settings message
explains individual re-enablement. Connected accounts synchronize automatically, including
managed Ubuntu accounts. Legacy `paused` remains false; API mutations return HTTP 410.
