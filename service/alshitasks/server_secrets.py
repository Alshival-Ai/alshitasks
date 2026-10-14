"""Headless instance vault. The master key is supplied separately from data."""
import hashlib
import os
import secrets
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from .paths import private_dir
from .platform_support import protect
from .store import Problem


class Vault:
    def __init__(self, root, key_file):
        self.directory = private_dir(Path(root) / 'config' / 'vault')
        self.cipher = Fernet(Path(key_file).read_bytes().strip())
        marker = self.directory / 'key-check'
        if marker.exists():
            try:
                if self.cipher.decrypt(marker.read_bytes()) != b'alshitasks-server-v1':
                    raise ValueError('Invalid vault')
            except InvalidToken:
                raise Problem('The server master key does not match this instance', 503) from None
        else:
            if any(self.directory.iterdir()):
                raise Problem('Vault key check is missing; restore the original vault', 503)
            self.write(marker, b'alshitasks-server-v1')

    def write(self, path, value):
        temporary = path.with_suffix('.' + secrets.token_hex(12) + '.tmp')
        try:
            with temporary.open('xb') as stream:
                protect(temporary)
                stream.write(self.cipher.encrypt(value))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def path(self, ident):
        return self.directory / (hashlib.sha256(ident.encode()).hexdigest() + '.enc')

    def put(self, ident, value):
        self.write(self.path(ident), value.encode())

    def get(self, ident):
        try:
            return self.cipher.decrypt(self.path(ident).read_bytes()).decode()
        except FileNotFoundError:
            return ''
        except InvalidToken:
            raise Problem('Server credential could not be decrypted', 503) from None

    def delete(self, ident):
        self.path(ident).unlink(missing_ok=True)


_vault = None


def vault():
    global _vault
    if _vault is None:
        _vault = Vault(os.environ['ALSHITASKS_SERVER_ROOT'], os.environ['ALSHITASKS_MASTER_KEY_FILE'])
    return _vault
