"""One native Linux toast producer, owned by the background service."""

import subprocess
import threading
import time
import os
from . import notifications


class NativeNotifications:
    def __init__(self):
        from gi.repository import Gio, GLib

        self.GLib = GLib
        self.Gio = Gio
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.ids = {}
        self.subscription = self.bus.signal_subscribe(
            "org.freedesktop.Notifications",
            "org.freedesktop.Notifications",
            "ActionInvoked",
            "/org/freedesktop/Notifications",
            None,
            Gio.DBusSignalFlags.NONE,
            self.action,
        )

    def action(self, _bus, _sender, _path, _interface, _signal, parameters):
        ident, action = parameters.unpack()
        if action in {"default", "open"} and ident in self.ids:
            subprocess.Popen(
                ["/usr/bin/alshitasks", "desktop", "--notification", self.ids[ident]],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def show(self, row):
        import html

        args = self.GLib.Variant(
            "(susssasa{sv}i)",
            (
                "Alshival.Ai",
                0,
                "alshitasks",
                row["title"],
                html.escape(row["body"]),
                ["default", "Open"],
                {},
                8000,
            ),
        )
        result = self.bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            args,
            self.GLib.VariantType.new("(u)"),
            self.Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        self.ids[result.unpack()[0]] = row["id"]
        if len(self.ids) > 500:
            self.ids.pop(next(iter(self.ids)))

    def poll(self):
        while self.context.pending():
            self.context.iteration(False)

    def close(self):
        self.bus.signal_unsubscribe(self.subscription)
        self.context.pop_thread_default()


def inbox_worker(api, stop):
    native = None
    retry = 0
    poller = None
    next_poll = 0
    next_cleanup = 0
    try:
        while not stop.wait(0.25):
            if api.stopping:
                return
            if time.time() >= next_poll and (poller is None or not poller.is_alive()):
                next_poll = time.time() + 2
                poller = threading.Thread(target=notifications.sync_portal, args=(api.store,), daemon=True)
                poller.start()
            if native is None and time.time() >= retry and api.store.paths.managed and os.name != "nt":
                try:
                    native = NativeNotifications()
                except Exception:
                    retry = time.time() + 60
            if time.time() >= next_cleanup:
                next_cleanup = time.time() + 60
                if native is None and os.name != "nt":
                    for row in notifications.pending(api.store):
                        with api.store.tx():
                            api.store.db.execute(
                                "UPDATE app_notifications SET toast='unavailable' WHERE id=?", (row["id"],)
                            )
            if native:
                try:
                    native.poll()
                    for row in notifications.pending(api.store):
                        try:
                            native.show(row)
                            state = "sent"
                        except Exception:
                            state = "unavailable"
                        with api.store.tx():
                            api.store.db.execute(
                                "UPDATE app_notifications SET toast=? WHERE id=?", (state, row["id"])
                            )
                except Exception:
                    try:
                        native.close()
                    except Exception:
                        pass
                    native = None
                    retry = time.time() + 60
    finally:
        if native:
            native.close()
