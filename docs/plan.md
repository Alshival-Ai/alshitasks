# Alshival Tasks ? Linux Desktop App

## Summary

Build **Alshival Tasks** in the existing empty public repository https://github.com/Alshival-Ai/alshitasks, licensed Apache-2.0.

The app works independently without an Alshival account: installation and first launch provision its local database, task boards, resources, and background service. An optional connection synchronizes selected workspaces with alshival.ai. Our team installation supplies the portal URL; users authenticate individually.

This document is a plan, not an implementation or deployment record.

## Desktop and local storage

- Use **Electron, React, and TypeScript**, reusing the Neural Labs Projects interface and graph semantics through extracted components and storage adapters. Package the UI locally; do not embed the live portal.
- Use **SQLite with WAL**, automatic migrations, and managed attachment storage. No Docker, PostgreSQL installation, cloud account, or Entra configuration is required.
- Separate desktop UI, background service, domain logic, storage, and integration adapters within the new repository. Reuse the existing project-tool contract with provenance and conformance tests.
- Keep each Linux user's data under their XDG data directory, configuration under their XDG configuration directory, and runtime sockets under their private runtime directory. Store credentials in Linux Secret Service; never silently fall back to plaintext credential storage.
- Run one background service per user. Start it at login after initial setup; closing the window leaves sync, ingestion, and monitoring running. Provide explicit pause and stop controls. Do not enable systemd lingering automatically.
- Ship Ubuntu 26.04 amd64 `.deb` packages first. The application package is `alshitasks`, independent of the existing `alshival` provisioning package. Broader Linux packaging and architectures follow after the pilot.
- Use sandboxed renderers, context isolation, restrictive IPC, and packaged content. Open external websites in the system browser, following [Electron's security guidance](https://www.electronjs.org/docs/latest/tutorial/security).

## Features and interfaces

### Projects

- Multiple boards, configurable statuses, assignments, task checklists, comments, dependencies and related links.
- Board, graph, timeline, activity, deliverables, notes/resources canvas, and recoverable Trash.
- Preserve stable identities, revisions, authorship, and explicit deletion markers. Dependencies reject cycles; personal card positions remain local.
- Local search, attachment import, and complete backup/export and restore. Creating a standalone board requires no sign-in.

### Resources, logs, and monitoring

- Resource types, provider/environment metadata, host/database details, URLs, tags, notes, attachments, relationships, health, and activity.
- A documented local REST API under `/v1`, with authenticated, resource-scoped log ingestion at `/v1/logs`. Accept bounded JSON batches with event IDs, timestamps, severity, message, and structured attributes.
- Search and filter logs by resource, time, severity, and text. Keep log storage separate from task storage; default retention is seven days or 1 GiB, whichever is reached first. Expose retention settings and storage usage.
- Support HTTPS, TCP, heartbeat, and database monitoring adapters for PostgreSQL, MySQL/MariaDB, Redis, and MongoDB. Default checks run every 120 seconds; require two consecutive results before changing confirmed health. Distinguish service failure from missing collectors.
- Provide local incident history and desktop/in-app alerts. Defer email, SMS, repository cloning, and wiki indexing.
- Support optional outbound HTTPS log forwarding with scoped credentials, bounded queues, retries, and event IDs for deduplication. Local operation must remain independent of any forwarding destination.
- Bind ingestion to loopback by default. Document authenticated reverse-proxy or tunnel deployment for remote collectors; do not automatically expose a public listener or change firewall rules.

### SSH aliases

- Let users attach an existing local key or SSH-agent identity to a resource.
- Generate aliases in an application-managed SSH configuration file, included from the user's existing configuration. Preserve unrelated aliases and refuse collisions.
- Open connections through the user's terminal and standard OpenSSH host verification.
- Never synchronize private keys, passphrases, or machine-local key paths. Revoking a connection removes only application-managed configuration.

### Local MCP

- Provide `alshitasks mcp` using stdio, backed by the same service and authorization logic as the UI.
- Reuse the project tool contract for boards, tasks, notes, resources, comments, and relationships. Add bounded log queries and monitoring-status tools.
- Support scoped read and write grants. Resource SSH configuration does not grant agents unrestricted remote command execution.
- Use a private Unix socket for desktop/service communication. Authenticate local HTTP clients; loopback alone is not authorization.

## Optional portal connection and synchronization

- Use the system browser for portal OAuth Authorization Code with PKCE, state verification, and a native loopback callback.
- Extend the portal's existing OAuth infrastructure with a **desktop API audience** and appropriate scopes. Existing workspace-MCP tokens must not automatically authorize the new API.
- Add a versioned desktop integration API providing capabilities, authorized workspace discovery, paginated snapshots/change cursors, revision-checked mutations, resource operations, and permitted log/monitoring reads.
- Reuse portal service-layer authorization and workspace permissions. The new adapter must not copy the managed Neural Labs sync principal or grant desktop users server-level authority.
- Let users select workspaces to cache. Keep standalone boards separate; connecting an account never uploads existing local boards automatically.
- Store offline edits in a transactional outbox with stable IDs, observed revisions, and idempotency keys. Replay after authentication and permission checks.
- Merge non-overlapping changes automatically. Retain both versions of conflicting edits and present a resolution queue; do not use desktop clock timestamps to silently overwrite shared work.
- Handle deletion through tombstones. Missing records or incomplete pages never mean deletion.
- Sync shared task content and resource metadata. Keep layout, credentials, SSH aliases, monitoring execution configuration, and unshared files local. File uploads require explicit sharing.
- Connected operational changes require current online authorization. Rejected offline changes stay visible as local drafts rather than being repeatedly submitted.
- On disconnect or revocation, stop remote activity and clearly mark retained local data as disconnected. Explain that offline copies cannot be remotely guaranteed erased.
- Coordinate with existing portal?Neural Labs replication using revisions and idempotency; the desktop is an authorized client of the portal, not another privileged mirror.

## Delivery, validation, and acceptance

1. Establish the repository, Apache-2.0 license, asset provenance, reusable contracts, and isolated fixtures. Verify rights to extracted source/assets before public publication.
2. Deliver standalone desktop, SQLite provisioning, boards, resources, attachments, migrations, backups, and background lifecycle.
3. Add local MCP, log ingestion/forwarding, monitoring, and SSH aliases.
4. Implement and test the optional portal adapter, OAuth audience, offline synchronization, and conflict UI.
5. Pilot under Samuel's account on Alshival, then other team accounts. Only after acceptance, update the `alshival` package to install `alshitasks`, supply the portal URL, and register its local MCP connection without copying shared credentials.

Acceptance tests must cover:

- Clean installation and offline first launch without accounts or database setup.
- UI parity for task editing, notes, resources, graph relationships, timeline, and Trash.
- Isolation between Linux users, boards, portal accounts, and workspaces.
- Restart, upgrade, migration failure, backup restoration, and uninstall without user-data deletion.
- Offline create/edit/delete, duplicate retries, conflicting edits, interrupted snapshots, permission removal, and concurrent Neural Labs synchronization.
- Log authentication, malformed/oversized batches, retention limits, forwarding outages, and bounded disk usage.
- Monitoring transitions, credential failures, unavailable collectors, and recovery.
- SSH alias collisions, paths containing spaces, host-key changes, and preservation of existing SSH configuration.
- MCP contract conformance and read-only enforcement.
- Desktop rendering, keyboard accessibility, Wayland/X11 behavior, and background service operation after closing the window.

Public release requires a clean-machine installation test and a successful data-preserving upgrade rehearsal. Existing portal billing, tickets, private agent chat, and account administration remain outside the desktop task application.
