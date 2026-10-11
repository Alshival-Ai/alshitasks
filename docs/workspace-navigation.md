# Workspace navigation

Portal and desktop share `workspace-directory.css`; the desktop records its source hash in the portal asset provenance manifest. Keep both copies together when changing this design.

The portal `/portal/workspaces/` directory is membership-scoped for clients and uses the existing staff grants for employees. Directory pages contain 30 cards; sidebar search requests at most 50 summaries per page. Staff operational filters and details retain their existing capability checks. Settings destinations keep their existing server authorization.

AlshiTasks uses cached discovery summaries for its directory and sidebar, with 30 cards per page and 50 navigation results per batch. It does not download every remote board for navigation. Account, notifications and administration live in the header. A workspace settings cog opens that workspace in the authenticated portal browser; device settings remain native.

Startup defaults to the directory with multiple available workspaces, or the sole workspace. Users can choose the last workspace instead. Desktop preferences are namespaced by account in local device storage; portal preferences are stored in that user's resources database. A removed workspace cannot be restored as an active startup destination. Retained disconnected/revoked copies are available separately in Device settings; account sign-out and discovery continue to enforce synchronization permissions.

## Validation

Run `npm run build` and the Python tests. Serve `dist` on port 8383 and run `node scripts/workspace-ui-test.mjs` with Playwright Chromium installed, or set `BROWSER_PATH` to a local Chromium browser. `UI_BASE_URL` and `UI_ARTIFACTS` override the fixture URL and screenshot folder. The browser fixture covers 1,000 summaries, bounded rendering, search, dismissal, settings destinations, workspace headings and startup restoration. `scripts/startup-test.mjs` verifies the installed Linux package with an isolated test root.
