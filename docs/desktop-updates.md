# Desktop updates

Windows x64 and Ubuntu amd64 installations check the public stable
Alshival-Ai/alshitasks GitHub release feed weekly. Personal desktops check while
running, including in the tray, and catch up after the next launch. Device
settings can disable automatic checks; manual checks remain available in the
Account/Settings and tray menus. No portal credential or new service API is used.

The main process persists last/next check times and verified pending metadata in
its private user-data `updates` directory. Downloads use temporary files, bounded
sizes/timeouts, expected asset names and SHA256SUMS. Cached bytes are rechecked on
startup and before installation. Retries use one hour, six hours, then daily,
respecting server Retry-After/rate-reset instructions. Concurrent checks coalesce.
This is the existing HTTPS release/checksum trust model, not a claim of code
signing. Prereleases, downgrades and unsupported architectures are refused.

A downloaded update offers Restart to update / Later. Explicit desktop quit can
install it without reopening; closing to the tray does not install. The normal
renderer navigation guard protects task/note drafts, and editing is disabled
during installation. OS shutdown/logoff does not initiate a Windows installation.
Windows uses the per-user NSIS installer in silent update mode, after the local
backend stops. Restart requests use NSIS's force-run flag. Existing data and
startup preferences are retained.

Ubuntu invokes the package-owned update-install wrapper via PolicyKit with fresh
administrator authorization. The Python helper obtains trusted release metadata
independently, copies a caller-owned regular file to root-owned staging and
verifies its checksum and Debian identity before APT installation. It honors
holds and package locks. Another open desktop defers installation; stopped
systemd user backends are restarted even if APT fails. Running backends outside
systemd must be stopped before installing. Cancellation keeps the cached package.
A missing authorization agent is reported without launching a terminal or
requesting passwords inside the app.

## Managed Alshival host

`/etc/alshitasks-managed-updates` selects a single machine-wide updater. Its
public, non-sensitive status is `/var/lib/alshitasks-updates/status.json`; root
owns the staging, lock and pending manifest. The desktop reads this status rather
than running a duplicate network schedule. A fixed PolicyKit check action lets
active sessions request a managed check/download, but cannot install packages or
accept caller arguments. Installation still requires administrator authorization
unless performed by the existing root-managed idle installer.

The files in `tools/managed-updates` override the existing fleet units. The check
timer wakes a small local scheduler hourly; the persisted deadline permits GitHub
checks only once every seven days (or the documented failure retry). A separate
five-minute pending timer checks **local** state and installs only after all
desktop sessions have exited. Neither timer force-closes other users' desktops.
The fleet helper is pinned separately at
`/usr/local/lib/alshival-desktop-updates/update-linux.py`, copied from the reviewed
release; this also bootstraps older installations that lack the packaged helper.
Keep it reconciled with future helper changes. Existing provisioned base units
remain intact beneath the drop-ins.

## Release and acceptance

The first updater-enabled installer must be installed once through the existing
installer/fleet mechanism. Later personal installs can update themselves. The
release workflow still publishes only after both Windows and Ubuntu jobs pass.
It runs `node --test scripts/updates-test.cjs`, Python helper tests, packaged UI
checks and `scripts/package-upgrade-test.py`. The latter is restricted to
disposable runners/containers and upgrades the previous stable installer, checking
that task IDs/revisions, queued offline edits, preferences, attachments and SQLite
integrity survive. `scripts/updates-ui-test.mjs` checks the update UI, draft-exit
gate and centered/truncation-safe workspace chevron at multiple window sizes.

Development/test-root desktops never access the update feed or install packages.
Do not run upgrade acceptance against the live host or real user data. Pending
installation on a shared host is a distinct state from publication or staging;
report it as such until the installed version and restarted services are verified.
