# Task board interaction parity

The desktop uses the portal's responsive board, styles, taxonomy and timeline
assets, with React adapters and local revisioned writes. The task board adapter
now follows the pointer and touch interaction model in
`website/customer_hub/static/customer_hub/shared-project.js` (reviewed at
`4f1b24234b082a0684877645b6d4494c5c860720`).

| Interaction | Desktop behavior |
| --- | --- |
| Move between columns | Mouse/pen movement starts after a 6px threshold; touch uses a 350ms hold. Native HTML drag is suppressed to avoid pointer-cancel clearing the active task. |
| Drop feedback | Card preview, highlighted destination, status announcement, edge scrolling and compact status-strip targets. |
| Cancel | Escape, window blur, touch cancellation, layout changes and release outside the board leave status unchanged. |
| Move without dragging | Top-layer status chooser supports keyboard navigation, Escape, outside dismissal and focus return; current status is disabled. |
| Narrow windows | Status tabs select columns; swipe/stacked layout choices remain accessible and persist. |
| Custom and legacy statuses | Custom status IDs are saved unchanged. Tasks with only a legacy state render in the matching column, falling back to the board default. |
| Save failures | Status changes use existing revisions and idempotency keys. Conflicts are shown with Reload board; failed writes do not move the task. Duplicate moves are blocked during a save. |

Existing desktop features include assignment, type/label/assignment/search filters,
archive/Trash, task details and comments, checklists, attachments, relationships,
notes/resources, and timeline editing. This change does not assert parity with
portal-only billing, support tickets, private chat or administration.

## Validation

`npm run validate` runs the production build and service tests. With a graphical
session and Electron installed, run `node scripts/task-board-test.mjs` for an
isolated desktop fixture. It covers real mouse movement, native touch events,
keyboard movement, custom/legacy statuses, persistence, cancellation, and a real
revision conflict. `ALSHITASKS_TEST_DISPLAY=wayland` selects Wayland;
`ALSHITASKS_TEST_APP=/usr/lib/alshitasks` tests the installed package.

`node scripts/desktop-test.mjs` covers existing editing, project views, Trash,
application navigation and sandbox preferences. Fixtures never use live user data.
