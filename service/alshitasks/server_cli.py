"""Explicit standalone instance setup and foreground server execution."""
import getpass
import os
from pathlib import Path
from .paths import Paths, private_dir
from .platform_support import lock_file, protect
from .store import Problem


def configure_parser(commands):
    for name in ('server', 'server-setup', 'server-password'):
        p = commands.add_parser(name)
        p.add_argument('--data-dir', required=True)
        if name != 'server-password':
            p.add_argument('--key-file', required=True)
        if name == 'server':
            p.add_argument('--host', default='127.0.0.1')
            p.add_argument('--port', type=int, default=43120)
            p.add_argument('--base-url', required=True)
            p.add_argument('--trusted-proxy', default='', help='Explicit trusted proxy IPs; never use *')


def execute(args):
    from .server_auth import Auth
    root = Path(args.data_dir).resolve()
    paths = Paths(root)
    auth = Auth(paths)
    if args.command in ('server-setup', 'server-password'):
        with (paths.runtime / 'service.lock').open('a') as lock:
            try:
                lock_file(lock)
            except BlockingIOError:
                raise Problem('Stop this server instance before changing its owner password', 409)
            if args.command == 'server-setup' and auth.ready():
                raise Problem('Instance already initialized; use server-password')
            while True:
                password = getpass.getpass('Owner password (12+ characters; typing is hidden): ')
                if not 12 <= len(password) <= 1024:
                    print('Use at least 12 characters. Try again here, or press Ctrl+C to cancel.')
                    continue
                if password == getpass.getpass('Repeat password (typing is hidden): '):
                    break
                print('Passwords did not match. Try again here, or press Ctrl+C to cancel.')
            if args.command == 'server-setup':
                from cryptography.fernet import Fernet
                key = Path(args.key_file).resolve()
                if key.is_relative_to(root):
                    raise Problem('Keep the master key outside the instance data directory')
                private_dir(key.parent)
                if not key.exists():
                    with key.open('xb') as stream:
                        protect(key)
                        stream.write(Fernet.generate_key())
                        stream.flush()
                        os.fsync(stream.fileno())
                from .server_secrets import Vault
                Vault(root, key)
            auth.password(password)
        print('Owner configured. Existing browser sessions have been revoked.')
        return
    from .web import base_url, create_app
    from .server_secrets import vault
    origin = base_url(args.base_url)
    if not auth.ready():
        raise Problem('Run server-setup before starting the server')
    if args.trusted_proxy == '*':
        raise Problem('Specify trusted proxy IP addresses explicitly')
    if not 1024 <= args.port <= 65535:
        raise Problem('Use a server port in 1024..65535')
    key = Path(args.key_file).resolve()
    if key.is_relative_to(root):
        raise Problem('Keep the master key outside the instance data directory')
    os.environ['ALSHITASKS_SERVER_ROOT'] = str(root)
    os.environ['ALSHITASKS_MASTER_KEY_FILE'] = str(key)
    vault()  # Fail before opening any network listener if key recovery is required.
    from .server import run
    def serve(api, stop):
        import uvicorn
        import threading
        app = create_app(api, origin)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, workers=1,
            proxy_headers=bool(args.trusted_proxy), forwarded_allow_ips=args.trusted_proxy,
            access_log=False, timeout_graceful_shutdown=30, limit_concurrency=64))
        def stopped():
            stop.wait()
            server.should_exit = True
        threading.Thread(target=stopped, daemon=True).start()
        server.run()
    run(str(root), web=serve)
