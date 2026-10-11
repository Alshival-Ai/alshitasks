# Pilot acceptance record

Target: Ubuntu 26.04 amd64. App name: **Alshival.Ai**. Package/CLI: `alshitasks` 0.1.3.

This is a pilot build, not a public binary release or team rollout acceptance.

## Automated validation

- TypeScript/Vite production build and Python Ruff checks pass.
- 39 isolated service/protocol tests cover provisioning, persistence, boards, cycles, revisions, replay receipts, backups/attachments, damaged backups, newer-schema rejection, authorization/revocation, MCP schema and read-only enforcement, loopback HTTP authentication and batch limits, retention including SQLite pages, monitor transitions, forwarding outages, incomplete snapshots, account isolation, permission drafts, offline create/edit/delete, lost create responses, sequential edits, assignments, imported graph timestamps, and SSH configuration/key paths containing spaces.
- Portal fixture suite covers native OAuth PKCE, distinct audiences, code/refresh audience binding, live discovery, workspace isolation, revocation, read-only rejection, and existing project-tool/OAuth behavior. All 38 portal tests pass. Production API publication and real user consent remain separate acceptance steps.

## Pilot checks and release gates

Verified on Alshival on 2026-10-10 (America/Chicago):

- Installed package `alshitasks` **0.1.3** on Ubuntu 26.04 amd64, displayed as **Alshival.Ai**, using the supplied icon pack.
- Exact installed Electron package passed task creation, note title/body persistence, resource creation, all project views, Trash restore, all app tabs and sandbox/context-isolation checks on **X11 and Wayland**. Keyboard Escape closes the editor. Fixture prompts explicitly accept discarding already-verified saved content; broader keyboard/accessibility review remains part of operator acceptance.
- Ordinary user first launch provisions the login service and an empty local board. Window close leaves the service active. Pause/resume and restart pass. `loginctl` reports `Linger=no`.
- Data-preserving upgrade from the initial 0.1.0 trial through 0.1.3 compares a fingerprint of tasks, notes, resources, comments, checklists, statuses, relationships, history, replay receipts, attachments and logs. All remain identical.
- Fresh pinned Ubuntu 26.04 container installs package dependencies, repeats the preserving upgrade, starts/stops the service, denies a second Linux user access to the first user's data/socket, and preserves data after package removal. Image digest: `sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7`.
- The pilot supplies `/etc/alshitasks/team.json` with the non-secret portal URL. No shared credentials are installed in the application. A real Secret Service store/read/delete probe passes without printing its value.

Package SHA-256: `dcc0de9d14a7ee20b6b0451c37affb21f22ddfbb95e5cefdaee02e05e7ae6ee7`.
Upgrade baseline 0.1.0 SHA-256: `a14b80150c0126fdbc868f217924dfd9804ffb30adb30d140f6d95396798a0e1`.

Isolated fixtures never import live databases or credentials. The reusable preserving-upgrade fixture is `scripts/upgrade-fixture.py`; desktop checks are `scripts/desktop-test.mjs`. Set `ALSHITASKS_TEST_APP=/usr/lib/alshitasks` to test the installed package, and `ALSHITASKS_TEST_DISPLAY=wayland` for Wayland.

Pending operator acceptance: Samuel’s ordinary desktop use, individual browser consent after the matching portal API release, live concurrent Neural Labs synchronization, and other team accounts. Standard OpenSSH owns host-key verification; an actual changed-key endpoint has not been exercised in this pilot.

A clean Ubuntu container validates package dependencies and CLI provisioning; it does not substitute for a clean graphical-machine installation test. Public binary publication remains gated on that graphical-machine test and pilot acceptance. The existing `alshival` provisioning package remains unchanged until acceptance.
