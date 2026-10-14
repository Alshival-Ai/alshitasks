"""Independent graph, attachment and notification workers with bounded concurrency."""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .store import Problem
from .portal import Portal, remote, sync_connection


def connections(store):
    with store.lock:
        return [
            dict(r)
            for r in store.db.execute("SELECT * FROM connections WHERE state='connected' AND workspace!=''")
        ]


def due(store, connection):
    """Called under the store lock. Backoff applies to both push and polling."""
    from datetime import datetime
    ident = connection["id"]
    schedule = store.setting("sync:" + ident, {})
    if schedule.get("next", 0) > time.time():
        return None
    pending = store.db.execute("SELECT 1 FROM outbox WHERE connection=? AND state='queued'", (ident,)).fetchone()
    generation = int(store.setting("sync_notice:" + ident, 0))
    if pending or generation > store.setting("sync_ack:" + ident, 0):
        return "urgent"
    last = datetime.fromisoformat(connection["cursor"]).timestamp() if connection["cursor"] else 0
    if time.time() >= schedule.get("poll_at", last + 30):
        return "poll"
    return None


def sync_one(store, connection, force=False):
    ident = connection["id"]
    with store.lock:
        if not force and not due(store, connection):
            return
        schedule = store.setting("sync:" + ident, {})
        generation = int(store.setting("sync_notice:" + ident, 0))
    try:
        with store.tx():
            store.set_setting("sync:" + ident, {"state": "syncing"})
        sync_connection(store, connection)
        with store.tx():
            store.set_setting("sync_ack:" + ident, generation)
            store.set_setting("sync:" + ident, {"state": "up-to-date", "poll_at": time.time() + random.uniform(27, 30)})
        store.sync_wake.set()
    except Exception as error:
        status = error.status if isinstance(error, Problem) else 503
        with store.tx():
            attempt = schedule.get("attempt", 0) + 1
            delay = max(getattr(error, "retry_after", 0), random.uniform(0.75, 1.25) * min(240, 2 ** min(attempt, 8)))
            store.set_setting("sync:" + ident, {
                "state": "attention" if status in (400, 401, 403, 409, 413) else "rate-limited" if status == 429 else "offline",
                "status": status, "attempt": attempt, "next": time.time() + delay,
                "error": str(error) if isinstance(error, Problem) else "Sync interrupted; retrying",
            })
            if status in (401, 403):
                store.db.execute("UPDATE connections SET state='revoked' WHERE id=?", (ident,))
                store.db.execute("UPDATE outbox SET state='draft',reason='Authorization removed' WHERE connection=? AND state='queued'", (ident,))


class Scheduler:
    """Two urgent slots then one reconciliation slot; oldest dispatched first."""
    def __init__(self):
        self.turn = 0
        self.last = {}

    def choose(self, store, rows, active):
        groups = {"urgent": [], "poll": []}
        for row in rows:
            if row["id"] not in active:
                reason = due(store, row)
                if reason:
                    groups[reason].append(row)
        preferred = "poll" if self.turn % 3 == 2 else "urgent"
        candidates = groups[preferred] or groups["urgent" if preferred == "poll" else "poll"]
        if not candidates:
            return None
        chosen = min(candidates, key=lambda row: self.last.get(row["id"], -1))
        self.last[chosen["id"]] = self.turn
        self.turn += 1
        return chosen


def graph_loop(api, stop):
    store = api.store
    from .accounts import discover
    active, discovery_at, discovery = {}, 0, None
    scheduler = Scheduler()
    store.sync_active = set()

    def run(connection):
        try:
            sync_one(store, connection)
        finally:
            with store.lock:
                store.sync_active.discard(connection["id"])
            store.sync_wake.set()

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph-sync") as pool, ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery") as discovery_pool:
        while not stop.is_set():
            store.sync_wake.wait(0.25)
            store.sync_wake.clear()
            active = {key: future for key, future in active.items() if not future.done()}
            if stop.is_set():
                continue
            if time.monotonic() >= discovery_at and (discovery is None or discovery.done()):
                discovery = discovery_pool.submit(discover, store)
                discovery_at = time.monotonic() + 2
            if not store.sync_lock.acquire(blocking=False):
                continue
            try:
                with store.lock:
                    rows = connections(store)
                    while len(active) < 4:
                        connection = scheduler.choose(store, rows, active)
                        if connection is None:
                            break
                        store.sync_active.add(connection["id"])
                        active[connection["id"]] = pool.submit(run, connection)
            finally:
                store.sync_lock.release()


def file_loop(api, stop):
    from .sync_files import download_attachments, upload_attachments
    from .publishing import resume_all

    store = api.store
    while not stop.wait(2):
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
                from .managed import guarded_transport
                transport, check = guarded_transport(store, connection, remote)
                upload_attachments(store, connection, transport, token)
                download_attachments(store, connection, files, transport, token)
                with store.tx():
                    store.set_setting("files:" + connection["id"], {"state": "synced"})
            except Exception:
                with store.tx():
                    store.set_setting(
                        "files:" + connection["id"],
                        {"state": "attention", "error": "Attachment transfer interrupted; retrying"},
                    )


# Kept as the service entrypoint; listener lifecycle is isolated from graph work.
from .live_sync import notification_loop
