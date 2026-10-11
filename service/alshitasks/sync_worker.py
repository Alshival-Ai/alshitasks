"""Independent graph, attachment and notification workers with bounded concurrency."""

import json
import random
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from .store import Problem
from .portal import Portal, remote, sync_connection
from .monitoring import NoRedirect


def connections(store):
    with store.lock:
        return [
            dict(r)
            for r in store.db.execute("SELECT * FROM connections WHERE state='connected' AND workspace!=''")
        ]


def sync_one(store, connection, force=False):
    from datetime import datetime

    with store.lock:
        schedule = store.setting("sync:" + connection["id"], {})
        pending = store.db.execute(
            "SELECT 1 FROM outbox WHERE connection=? AND state='queued'", (connection["id"],)
        ).fetchone()
        pushed = store.setting("sync_notice:" + connection["id"], False)
    if not force and schedule.get("next", 0) > time.time():
        return
    if (
        not force
        and not pushed
        and not pending
        and connection["cursor"]
        and time.time() - datetime.fromisoformat(connection["cursor"]).timestamp() < 30
    ):
        return
    try:
        with store.tx():
            store.set_setting("sync:" + connection["id"], {"state": "syncing"})
            store.set_setting("sync_notice:" + connection["id"], False)
        sync_connection(store, connection)
        with store.tx():
            store.set_setting("sync:" + connection["id"], {"state": "up-to-date"})
    except Exception as error:
        status = error.status if isinstance(error, Problem) else 503
        with store.tx():
            attempt = schedule.get("attempt", 0) + 1
            store.set_setting(
                "sync:" + connection["id"],
                {
                    "state": "attention" if status in (400, 401, 403, 409, 413) else "offline",
                    "attempt": attempt,
                    "next": time.time() + random.uniform(0.75, 1.25) * min(240, 2 ** min(attempt, 8)),
                    "error": str(error) if isinstance(error, Problem) else "Sync interrupted; retrying",
                },
            )
            if status in (401, 403):
                store.db.execute("UPDATE connections SET state='revoked' WHERE id=?", (connection["id"],))
                store.db.execute(
                    "UPDATE outbox SET state='draft',reason='Authorization removed' WHERE connection=? AND state='queued'",
                    (connection["id"],),
                )


def graph_loop(api, stop):
    store = api.store
    from .accounts import discover

    active = {}
    discovery_at = 0
    store.sync_active = set()

    def run(connection):
        try:
            sync_one(store, connection)
        finally:
            with store.lock:
                store.sync_active.discard(connection["id"])

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph-sync") as pool:
        while not stop.is_set():
            store.sync_wake.wait(0.25)
            store.sync_wake.clear()
            active = {key: future for key, future in active.items() if not future.done()}
            with store.lock:
                paused = store.setting("paused", False)
            if paused or stop.is_set():
                continue
            if time.monotonic() >= discovery_at and "discovery" not in active and len(active) < 4:
                active["discovery"] = pool.submit(discover, store)
                discovery_at = time.monotonic() + 2
            if not store.sync_lock.acquire(blocking=False):
                continue
            try:
                for connection in connections(store):
                    if len(active) >= 4:
                        break
                    if connection["id"] in active:
                        continue
                    with store.lock:
                        from datetime import datetime

                        schedule = store.setting("sync:" + connection["id"], {})
                        if schedule.get("next", 0) > time.time():
                            continue
                        queued = store.db.execute(
                            "SELECT 1 FROM outbox WHERE connection=? AND state='queued'", (connection["id"],)
                        ).fetchone()
                        notice = store.setting("sync_notice:" + connection["id"], False)
                        if (
                            connection["cursor"]
                            and not queued
                            and not notice
                            and time.time() - datetime.fromisoformat(connection["cursor"]).timestamp() < 30
                        ):
                            continue
                        store.sync_active.add(connection["id"])
                    active[connection["id"]] = pool.submit(run, connection)
            finally:
                store.sync_lock.release()


def file_loop(api, stop):
    from .sync_files import download_attachments, upload_attachments
    from .publishing import resume_all

    store = api.store
    while not stop.wait(2):
        with store.lock:
            if store.setting("paused", False):
                continue
        try:
            # Publication and ordinary graph sync must not rebind identities concurrently.
            if store.sync_lock.acquire(blocking=False):
                try:
                    with store.lock:
                        idle = not getattr(store, "sync_active", set())
                    if idle:
                        resume_all(store)
                finally:
                    store.sync_lock.release()
        except Exception:
            pass
        for connection in connections(store):
            try:
                with store.lock:
                    files = store.setting("attachment_queue:" + connection["id"], [])
                token = Portal(store).token(connection)
                upload_attachments(store, connection, remote, token)
                download_attachments(store, connection, files, remote, token)
                with store.tx():
                    store.set_setting("files:" + connection["id"], {"state": "synced"})
            except Exception:
                with store.tx():
                    store.set_setting(
                        "files:" + connection["id"],
                        {"state": "attention", "error": "Attachment transfer interrupted; retrying"},
                    )


def notices(store, connection, stop):
    attempt = 0
    while not stop.is_set():
        try:
            with store.lock:
                active = Portal(store).connection(connection["id"])["state"] == "connected"
                paused = store.setting("paused", False)
            if not active:
                return
            if paused:
                stop.wait(2)
                continue
            token = Portal(store).token(connection)
            request = urllib.request.Request(
                connection["origin"] + "/desktop/v1/workspaces/" + connection["workspace"] + "/events",
                headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
            )
            with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as stream:
                for line in stream:
                    if stop.is_set():
                        return
                    if line.startswith(b"event: changed"):
                        with store.tx():
                            store.set_setting("sync_notice:" + connection["id"], True)
                        store.sync_wake.set()
                        attempt = 0
            attempt += 1
        except Exception:
            attempt += 1
        stop.wait(random.uniform(0.75, 1.25) * min(30, 2 ** min(attempt, 5)))


def notification_loop(api, stop):
    active = {}
    while not stop.wait(2):
        active = {key: thread for key, thread in active.items() if thread.is_alive()}
        for connection in connections(api.store):
            if len(active) >= 4:
                break
            if connection["id"] not in active and "graph-events-v2" in json.loads(
                connection["capabilities"]
            ).get("features", []):
                thread = threading.Thread(target=notices, args=(api.store, connection, stop), daemon=True)
                active[connection["id"]] = thread
                thread.start()
