# Architecture and trust boundaries

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
