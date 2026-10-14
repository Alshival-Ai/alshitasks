# Alshival Local

The desktop companion to the **Alshival** portal plugin. Connect your assistant
to the standalone Windows or Linux app on the same computer. No portal account
or copied API token is required. Install and open the desktop app first.

## Quick setup

Download this repository (or clone it) and open a terminal in this directory.
On Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

This uses the Python runtime bundled with Alshival. For a custom installation,
add `-AppDirectory 'D:\Apps\Alshival.Ai'`. Add `-ReadOnly` to deny writes.
The execution-policy override applies only to this process; inspect the script
before running it.

On Linux:

```sh
sh ./setup.sh
# Optional read-only access:
sh ./setup.sh --read-only --output "$HOME/Alshival-Local-ReadOnly"
```

Setup creates `~/Alshival-Local-Plugin/alshival-local.zip`, plus a local Codex
marketplace. It never overwrites an existing output directory. Use `-Output`
(Windows) or `--output` (Linux) for another location.

Import the generated ZIP with your desktop host's supported local plugin flow.
For Codex, register the generated marketplace:

```sh
codex plugin marketplace add ~/Alshival-Local-Plugin
```

Install **Alshival Local** from that marketplace in Codex desktop, then run its
Setup skill to verify the connection and select a workspace. Setup does not
modify your host configuration or install itself automatically. Keep Alshival's
local service running. Regenerate and reinstall after moving the app.

The generated package uses absolute paths for this computer. Do not distribute
it as a universal installer; run setup separately on each computer. The source
`mcp.json` is a Linux PATH default, not a configured Windows package. macOS is
not currently supported by the desktop app; use the portal plugin there.

## Choose your connection

| Plugin | Connection | Requirements |
| --- | --- | --- |
| Alshival | alshival.ai portal via OAuth | Eligible account/workspace and approved permissions |
| Alshival Local | Installed app via stdio | Windows/Linux desktop host and running app |

Default local access is your desktop user's read/write access. Use read-only
setup for browsing. Neither plugin enables sync; the app controls existing sync
and may upload edits to workspaces already connected to the portal. Tell the
assistant which connection to use when both plugins are installed.

Local MCP plugins are desktop-only. Public directory submission currently needs
OpenAI partner approval; this is a local installation package, not a published
directory listing. See the [official requirements](https://developers.openai.com/plugins/deploy/desktop-only).
