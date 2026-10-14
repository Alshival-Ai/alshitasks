---
name: setup
description: Verify the Alshival Local desktop connection and select a local workspace after installation.
---

# Set up Alshival Local

This plugin uses the installed Alshival desktop app on this computer. It works
without an alshival.ai account. The app's backend must be running. On Windows
and Linux, prepare the package using its setup.ps1 or setup.sh before installing
it; these locate the existing runtime and need no tokens or new server.

Call `workspace_list` with the current tool schema, then let the user select a
returned workspace if ambiguous. Read its board using `project_boards` and
summarize the connection. Do not create sample records during setup. Respect
read-only connections; changing access requires regenerating the package with
the user's chosen access and reinstalling it through the host.

If tools fail, ask the user to open the desktop app and retry. For a moved or
reinstalled app, regenerate the package. Do not read credential stores, copy
the local service token, expose a network port, or fall back to the portal.

For direct access to team workspaces, use the separate **Alshival** portal
plugin and its OAuth Connect flow. Desktop sync is configured in the desktop
app; neither plugin starts sync or copies records. If both plugins are enabled,
respect the user's selected target; clarify ambiguity before reading or writing.
Never apply the same write to both connections.
