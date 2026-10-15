"""Account event health controls recovery cadence; local edits are always urgent."""
import random
import time
from .live_sync import live_key

FEATURE = "efficient-sync-v1"


def live(store, connection):
    value = store.setting(live_key(store, connection), {})
    return value.get("state") == "live" and time.time() - value.get("heartbeat", 0) < 40


def interval(store, connection):
    return random.uniform(270, 300) if live(store, connection) else random.uniform(45, 55)


def discovery_due(store, connection, schedule):
    deadline = schedule.get("next", 0)
    if schedule.get("state") == "connected" and not live(store, connection):
        deadline = min(deadline, schedule.get("last_success", 0) + 55)
    return time.time() >= deadline
