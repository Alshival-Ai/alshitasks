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

The desktop polls discovery and complete revision snapshots every 30 seconds, with
queued writes checked every two seconds and bounded retries after failures. This
requires no inbound port or webhook on a user's computer. Portal database triggers
continue to feed the managed Neural Labs mirror; desktop sync uses individual user
permissions and a separate OAuth audience. Membership is rechecked on each request.

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
