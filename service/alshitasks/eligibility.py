"""Account and authorization-generation fences for optional portal work."""

from .store import Problem


def capture(store, connection):
    with store.lock:
        generation = store.setting('authorization_generation', 0)
        check(store, connection, generation)
        return generation


def check(store, connection, generation):
    with store.lock:
        account = store.setting('active_account')
        current = store.db.execute('SELECT * FROM connections WHERE id=?', (connection['id'],)).fetchone()
        if (not account or not current
                or store.setting('authorization_generation', 0) != generation
                or current['state'] not in ('connected', 'syncing')
                or (account['origin'], account['id']) != (current['origin'], current['account'])
                or (connection['origin'], connection['account']) != (current['origin'], current['account'])):
            raise Problem('Account disconnected or changed; work retained locally', 409)
