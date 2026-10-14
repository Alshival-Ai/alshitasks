# Workspace navigation

Portal and desktop share `workspace-directory.css`; the desktop records its source hash in the portal asset provenance manifest. Keep both copies together when changing this design.

The portal `/portal/workspaces/` directory is membership-scoped for clients and uses the existing staff grants for employees. Directory pages contain 30 cards; sidebar search requests at most 50 summaries per page. Staff operational filters and details retain their existing capability checks. Settings destinations keep their existing server authorization.

AlshiTasks uses cached discovery summaries for its directory and sidebar, with 30 cards per page and 50 navigation results per batch. It does not download every remote board for navigation. Settings and notifications live in the header. Linked accounts also have an account-settings link. Administrators use the same desktop interface as other users; administration stays in the portal. The workspace settings cog offers icons, colors, types and labels, with a browser link for other portal settings; device settings remain native.

Startup defaults to the directory with multiple available workspaces, or the sole workspace. Users can choose the last workspace instead. Desktop preferences are namespaced by account in local device storage; portal preferences are stored in that user's resources database. A removed workspace cannot be restored as an active startup destination. Retained disconnected/revoked copies are available separately in Device settings; account sign-out and discovery continue to enforce synchronization permissions.

## Validation

Run `npm run build` and the Python tests. Serve `dist` on port 8383 and run `node scripts/workspace-ui-test.mjs` with Playwright Chromium installed, or set `BROWSER_PATH` to a local Chromium browser. `UI_BASE_URL` and `UI_ARTIFACTS` override the fixture URL and screenshot folder. The browser fixture covers 1,000 summaries, bounded rendering, search, dismissal, settings destinations, workspace headings and startup restoration. `scripts/startup-test.mjs` verifies the installed Linux package with an isolated test root.

## Shared sidebar

`sidebar.css`, `sidebar.js` and `sidebar-icons.svg` are vendored from the portal (see `docs/portal-shell-provenance.json`). They require no portal connection. Both products use a click-only 256px sidebar / 72px icon rail, always-visible workspace badges, one vertical scroll area, board choices nested under the active workspace and a fixed Neural Labs footer when discovery supplies an eligible destination. Single-board workspaces omit redundant board navigation; management stays on Workspaces. Mobile uses a drawer with focus trapping and Escape. Desktop expansion preferences are local and account-scoped.

Browser validation also covers both themes, rail icon visibility, horizontal overflow, hover stability, board popovers, Neural Labs links and mobile dismissal.

## Portal controls and assignment

`portal-actions.css` is copied unchanged from the portal. `portal-controls.css` supplies the scoped portal typography, palette and control dimensions that otherwise come from `hub.css`, without importing the portal's application layout or requiring a connection. Asset revisions and hashes are recorded in the provenance manifests. Keep these foundations and the copied action styles together when updating portal parity.

Task-card assignment uses the portal's searchable quick picker (`sp-assignee` / `sp-quick-popover`) with the desktop's local member catalog and existing revisioned mutations. Failed saves retain the previous assignment and show an error; selecting Unassigned clears it. Escape restores focus to the trigger. The workspace browser fixture covers assignment, clearing, search, failure recovery and light/dark button styles, and exports rendered control styles for comparison with portal fixtures.
