"""Single-owner authentication, deliberately excluded from workspace exports."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from .store import Problem
from .platform_support import protect

LIFETIME = 30 * 86400


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, paths):
        self.path = paths.config / 'server-auth.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY CHECK(id=1), password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (peer TEXT PRIMARY KEY, count INTEGER, until REAL);
            ''')
        protect(self.path)
        self.hasher = PasswordHasher()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ready(self):
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM owner').fetchone())

    def password(self, value):
        if not isinstance(value, str) or not 12 <= len(value) <= 1024:
            raise Problem('Use a password between 12 and 1024 characters')
        encoded = self.hasher.hash(value)
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO owner VALUES(1,?)', (encoded,))
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM attempts')

    def login(self, password, peer):
        if not isinstance(password, str) or len(password) > 1024:
            raise Problem('Invalid password', 401)
        # Reserve attempts before hashing, across simultaneous requests.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE until<?', (time.time(),))
            row = db.execute('SELECT count FROM attempts WHERE peer=?', (digest(peer),)).fetchone()
            if row and row[0] >= 10:
                raise Problem('Too many sign-in attempts; retry in 15 minutes', 429)
            db.execute('INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(peer) DO UPDATE SET count=count+1',
                       (digest(peer), time.time() + 900))
            owner = db.execute('SELECT password FROM owner WHERE id=1').fetchone()
        try:
            if not owner:
                raise Problem('Initialize the owner using server-setup', 503)
            self.hasher.verify(owner[0], password)
        except VerificationError:
            raise Problem('Invalid password', 401) from None
        token = secrets.token_urlsafe(48)
        with self.db() as db:
            # Password reset racing a login must not resurrect its sessions.
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT password FROM owner WHERE id=1').fetchone() != owner:
                raise Problem('Password changed; sign in again', 401)
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('DELETE FROM attempts WHERE peer=?', (digest(peer),))
            db.execute('INSERT INTO sessions VALUES(?,?)', (digest(token), time.time() + LIFETIME))
        return token

    def session(self, token):
        if not token or len(token) > 128:
            raise Problem('Sign in to Alshitask Server', 401)
        with self.db() as db:
            cursor = db.execute('UPDATE sessions SET expires=? WHERE digest=? AND expires>?',
                                (time.time() + LIFETIME, digest(token), time.time()))
            if not cursor.rowcount:
                raise Problem('Sign in to Alshitask Server', 401)
        return digest('csrf:' + token)

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (digest(token),))
