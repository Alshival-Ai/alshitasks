"""Portable graph links and local mention projections."""

import re
from urllib.parse import urlsplit
from .store import Problem, uid

LINK = re.compile(r"(\[[^\]\n]{1,300}\]\()([^\s)]+)(\))")
FRAGMENT = re.compile(r"#(resource|item)-([0-9a-f-]{36})$", re.I)
RESOURCE = re.compile(r"/(?:resources|resource)/([0-9a-f-]{36})/$", re.I)


def translate(body, mapping, *, outgoing=False):
    def replace(match):
        href = match[2]
        fragment = FRAGMENT.fullmatch(href)
        if fragment:
            kind, ident = fragment.groups()
        else:
            url = urlsplit(href)
            resource = RESOURCE.search(url.path)
            if (
                outgoing
                or not resource
                or url.netloc not in ("", "alshival.ai")
                or url.scheme not in ("", "https")
            ):
                return match[0]
            kind, ident = "resource", resource[1]
        mapped = mapping(ident)
        if not mapped:
            if outgoing:
                raise Problem("Linked item has not synchronized yet", 409)
            return match[0]
        href = f"/portal/resources/{mapped}/" if outgoing and kind == "resource" else f"#{kind}-{mapped}"
        return match[1] + href + match[3]

    return LINK.sub(replace, body)


def mentioned(body):
    return {
        m[2] for _, href, _ in LINK.findall(body) if (m := FRAGMENT.fullmatch(href)) and m[1] == "resource"
    }


def synchronize_mentions(store, note):
    if note["kind"] != "note":
        return
    desired = mentioned(note["data"]["body"])
    prior = set(store.setting("mentions:" + note["id"], []))
    for ident in desired - prior:
        resource = store.item(ident)
        if resource["kind"] != "resource":
            raise Problem("Mention must reference a resource")
        edge = store.edge_create(
            {"source_id": note["id"], "target_id": ident, "kind": "related", "idempotency_key": uid()},
            explicit=False,
        )
        store.set_setting("mentioned_edge:" + note["id"] + ":" + ident, edge["id"])
    store.set_setting("mentions:" + note["id"], sorted(desired))
    for ident in prior - desired:
        edge_id = store.setting("mentioned_edge:" + note["id"] + ":" + ident)
        edge = store.db.execute("SELECT * FROM edges WHERE id=? AND deleted=0", (edge_id,)).fetchone()
        if edge and not store.setting("explicit_edge:" + edge_id, True):
            store.edge_delete(edge_id, edge["revision"])


def migrate(store):
    if store.setting("graph_parity_v2"):
        return
    seen = {}
    for raw in store.db.execute("SELECT * FROM edges WHERE deleted=0 ORDER BY rowid").fetchall():
        edge = dict(raw)
        a = store.db.execute("SELECT kind,connection FROM items WHERE id=?", (edge["source_id"],)).fetchone()
        b = store.db.execute("SELECT kind,connection FROM items WHERE id=?", (edge["target_id"],)).fetchone()
        invalid = (
            not a
            or not b
            or a["connection"] != b["connection"]
            or a["kind"] not in ("task", "note", "resource")
            or b["kind"] not in ("task", "note", "resource")
            or (edge["kind"] == "depends_on" and (a["kind"] != "task" or b["kind"] != "task"))
        )
        if invalid:
            store.set_setting(
                "graph_draft:" + edge["id"],
                dict(edge, reason="Relationship no longer meets shared graph rules"),
            )
            store.db.execute("UPDATE edges SET deleted=1 WHERE id=?", (edge["id"],))
            store.db.execute(
                "UPDATE outbox SET state='draft',reason='Relationship requires review' WHERE json_extract(payload,'$.edge.id')=? AND state='queued'",
                (edge["id"],),
            )
            continue
        if edge["kind"] == "related":
            pair = tuple(sorted((edge["source_id"], edge["target_id"])))
            if pair in seen:
                store.set_setting(
                    "graph_draft:" + edge["id"],
                    dict(edge, reason="Merged reversed duplicate", canonical=seen[pair]),
                )
                store.db.execute("UPDATE edges SET deleted=1 WHERE id=?", (edge["id"],))
                store.db.execute(
                    "UPDATE outbox SET state='discarded',reason='Merged reversed duplicate' WHERE json_extract(payload,'$.edge.id')=? AND state='queued'",
                    (edge["id"],),
                )
            else:
                seen[pair] = edge["id"]
                store.db.execute("UPDATE edges SET source_id=?,target_id=? WHERE id=?", (*pair, edge["id"]))
    store.set_setting("graph_parity_v2", True)
