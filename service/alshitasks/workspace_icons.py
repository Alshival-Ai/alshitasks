"""Workspace icon settings: local for standalone workspaces, portal-owned for shared ones."""

import json
from pathlib import Path
from .store import Problem
from .workspaces import LOCAL

ICON_IDS = {
    "",
    *(row["id"] for row in json.loads((Path(__file__).parent / "assets/workspace-icons.json").read_text())),
}


def dispatch(store, method, body, owner):
    if not owner:
        raise Problem("Desktop owner authorization required", 403)
    selected = store.workspace
    if selected is None:
        raise Problem("Choose a workspace")
    if method not in ("GET", "POST"):
        raise Problem("Unsupported icon operation", 405)
    if method == "POST" and (not isinstance(body.get("icon"), str) or body["icon"] not in ICON_IDS):
        raise Problem("Choose an icon from the workspace icon pack")
    if selected == LOCAL:
        value = store.setting("local_workspace_icon", {"icon": "", "revision": 0})
        if method == "POST":
            if body.get("revision") != value["revision"]:
                raise Problem("The icon changed. Reopen settings and try again.", 409)
            value = {"icon": body["icon"], "revision": value["revision"] + 1}
            store.set_setting("local_workspace_icon", value)
        return dict(value, can_manage=True)
    from .portal import Portal, remote

    portal = Portal(store)
    connection = portal.connection(selected)
    meta = store.setting("workspace:" + selected, {})
    if method == "GET" and connection["state"] != "connected":
        return {"icon": meta.get("icon", ""), "revision": meta.get("icon_revision", 0), "can_manage": False}
    if connection["state"] != "connected":
        raise Problem("Reconnect to change this shared workspace icon", 409)
    value = remote(
        connection["origin"],
        "/desktop/v1/workspaces/" + connection["workspace"] + "/icon",
        portal.token(connection),
        body if method == "POST" else None,
    )
    if value.get("icon") not in ICON_IDS:
        value["icon"] = ""
    store.set_setting(
        "workspace:" + selected, dict(meta, icon=value["icon"], icon_revision=value["revision"])
    )
    return value
