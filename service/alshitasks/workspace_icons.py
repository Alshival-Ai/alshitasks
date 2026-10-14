"""Workspace icon settings: local for standalone workspaces, portal-owned for shared ones."""

import json
from pathlib import Path
from .store import Problem
from .workspaces import LOCAL
from .workspace_art import appearance, color, defaults

ICON_IDS = {
    "",
    *(row["id"] for row in json.loads((Path(__file__).parent / "assets/workspace-icons.json").read_text())),
}


def local_selection(store):
    value = store.setting('local_workspace_icon', {'revision': 0})
    resolved = dict(value, **appearance(LOCAL, value.get('icon'), value.get('icon_color')))
    return resolved


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
    if method == 'POST' and 'icon_color' in body:
        try:
            body = dict(body, icon_color=color(body['icon_color']))
        except ValueError as error:
            raise Problem(str(error)) from error
    if selected == LOCAL:
        value = local_selection(store)
        if method == "POST":
            if body.get("revision") != value["revision"]:
                raise Problem("The icon changed. Reopen settings and try again.", 409)
            artwork = defaults(LOCAL) if body.get('reset') or body['icon'] == '' else {
                'icon': body['icon'], 'icon_color': body.get('icon_color', value['icon_color'])}
            value = dict(artwork, revision=value['revision'] + 1)
            store.set_setting("local_workspace_icon", value)
        return dict(value, can_manage=True, default=defaults(LOCAL))
    from .portal import Portal, remote

    portal = Portal(store)
    connection = portal.connection(selected)
    meta = store.setting("workspace:" + selected, {})
    if method == "GET" and connection["state"] != "connected":
        return {**appearance(connection['workspace'], meta.get('icon'), meta.get('icon_color')),
                "revision": meta.get("icon_revision", 0), "can_manage": False, 'default': defaults(connection['workspace'])}
    if connection["state"] != "connected":
        raise Problem("Reconnect to change this shared workspace icon", 409)
    value = remote(
        connection["origin"],
        "/desktop/v1/workspaces/" + connection["workspace"] + "/icon",
        portal.token(connection),
        body if method == "POST" else None,
    )
    value.update(appearance(connection['workspace'], value.get('icon'), value.get('icon_color', meta.get('icon_color'))))
    value['default'] = defaults(connection['workspace'])
    store.set_setting(
        "workspace:" + selected, dict(meta, icon=value["icon"], icon_color=value['icon_color'], icon_revision=value["revision"])
    )
    return value
