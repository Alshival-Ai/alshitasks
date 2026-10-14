"""Authenticated, same-origin browser and Streamable HTTP MCP transport."""
import hmac
import asyncio
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import httpx
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, FileResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from .server_auth import Auth, LIFETIME, digest
from .store import Problem
from .requests import handle

COOKIE = 'alshitasks_session'
# Keep browser authority explicit. New internal APIs do not become public automatically.
READ = re.compile(r'/v1/(?:health|revision|drafts|conflicts|workspaces|workspaces/icon|projects(?:/[A-Za-z0-9/-]+)?|settings|attachments(?:/[a-f0-9-]+)?|notifications(?:/[A-Za-z0-9/-]+)?|portal/(?:account|connections|workspaces|publish|publish-offer|logs|monitoring)|logs(?:/setup)?|monitoring|forwarding|ssh|service-status)\Z')
WRITE = re.compile(r'/v1/(?:projects(?:/[A-Za-z0-9/-]+)?|workspaces/icon|settings|attachments(?:/[a-f0-9-]+(?:/review)?)?|notifications/(?:actions|refresh)|portal/(?:signout|retry|select|publish|publish-offer)|drafts/resubmit|conflicts/resolve|secret|monitoring(?:/(?:checks|actions))?|forwarding|ssh)\Z')


def base_url(value):
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('Use an absolute base URL without a path or credentials')
    if url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Remote browser access requires an HTTPS base URL')
    return value.rstrip('/')


def create_app(api, origin, assets=None):
    origin = base_url(origin)
    public = urlsplit(origin)
    auth = Auth(api.store.paths)
    assets = Path(assets or Path(__file__).resolve().parents[2] / 'dist').resolve()
    version = json.loads((Path(__file__).resolve().parents[2] / 'package.json').read_text())['version']
    from .cloud_logs import ROUTE as LOG_ROUTE
    pending = {}
    pending_lock = threading.Lock()

    async def body(request, limit=1024**2):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise Problem('JSON content type required', 415)
        data = bytearray()
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > limit:
                    raise Problem('Request exceeds size limit', 413)
        value = json.loads(data or b'{}')
        if not isinstance(value, dict):
            raise Problem('JSON object required')
        return value

    def owner_call(path, method='GET', data=None):
        return handle(api, path, method, data or {}, True, '')

    async def api_request(request):
        route = request.url.path
        method = request.method
        if getattr(request.state, 'bearer', None):
            if route == '/v1/heartbeat' and method == 'POST':
                value = await body(request)
                return JSONResponse(await run_in_threadpool(handle, api, route, method, value, False, request.state.bearer))
            return await cloud_request(request)
        if not (READ if method == 'GET' else WRITE).fullmatch(route):
            raise Problem('Operation is not available in browser mode', 403)
        data = await body(request, 29 * 1024**2 if route == '/v1/attachments' else 1024**2) if method != 'GET' else {}
        if route == '/v1/settings' and method != 'GET' and set(data) - {'retention', 'local_members'}:
            raise Problem('This setting is managed by the server operator', 403)
        if route == '/v1/ssh' and method == 'POST':
            # Never accept a server filesystem path from the browser.
            if data.get('identity_file'):
                raise Problem('Upload a key using the file picker')
        result = await run_in_threadpool(owner_call, route + ('?' + request.url.query if request.url.query else ''), method, data)
        if route == '/v1/settings' and method == 'GET':
            result = dict(result, mcp_config={'mcpServers': {'alshitasks': {'type': 'streamable-http', 'url': origin + '/mcp'}}},
                          secret_service='Encrypted server vault', socket='', server=True, http_port=None)
        return JSONResponse(result)

    async def cloud_request(request):
        from . import cloud_logs
        from .cloud_contract.errors import APIError
        import uuid
        match = LOG_ROUTE.fullmatch(request.url.path)
        if not match:
            raise Problem('Token does not grant browser access', 403)
        raw = bytearray()
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > cloud_logs.MAX_BODY:
                    raise Problem('Log batch exceeds 4 MiB', 413)
        def execute():
            with api.store.tx():
                resource = cloud_logs.access(api, match, request.method, False, request.state.bearer)
                if request.method == 'GET':
                    return cloud_logs.query(api, match[1], resource, dict(request.query_params))
                return cloud_logs.ingest(api, resource, bytes(raw), request.headers.get('content-type','').split(';')[0],
                    request.headers.get('content-encoding','identity'), match[3] != 'logs', str(uuid.uuid4()))
        try:
            result = await run_in_threadpool(execute)
        except APIError as error:
            return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status)
        if match[3] != 'logs':
            content = request.headers.get('content-type','').split(';')[0]
            return Response(b'' if content == 'application/x-protobuf' else b'{}', media_type=content)
        return JSONResponse(result)

    async def session(request):
        return JSONResponse({'csrf': request.state.csrf, 'mode': 'server'})

    async def login(request):
        data = await body(request, 4096)
        token = await run_in_threadpool(auth.login, data.get('password'), request.client.host if request.client else 'unknown')
        response = JSONResponse({'csrf': digest('csrf:' + token)})
        cookie(response, token)
        return response

    def cookie(response, token):
        response.set_cookie(COOKIE, token, max_age=LIFETIME, httponly=True, secure=public.scheme == 'https', samesite='lax', path='/')

    async def logout(request):
        await run_in_threadpool(auth.logout, request.cookies.get(COOKIE, ''))
        response = JSONResponse({'signed_out': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    async def backup(request):
        from .files import backup as export
        directory = tempfile.TemporaryDirectory(dir=api.store.paths.runtime)
        path = Path(directory.name) / 'alshitasks-backup.zip'
        def make():
            with api.store.lock:
                export(api.store, api.logs, str(path))
        try:
            await run_in_threadpool(make)
            return FileResponse(path, filename=path.name, background=BackgroundTask(directory.cleanup))
        except BaseException:
            directory.cleanup()
            raise

    async def ssh_key(request):
        import base64
        data = await body(request, 180 * 1024)
        if set(data) - {'content', 'resource_id', 'alias', 'host', 'username', 'port'}:
            raise Problem('Invalid SSH key settings')
        content = base64.b64decode(data.pop('content'), validate=True)
        if len(content) > 128 * 1024:
            raise Problem('SSH key exceeds 128 KiB', 413)
        with tempfile.TemporaryDirectory(dir=api.store.paths.runtime) as directory:
            path = Path(directory) / 'key'
            path.write_bytes(content)
            from .platform_support import protect
            protect(path)
            return JSONResponse(await run_in_threadpool(owner_call, '/v1/ssh/key', 'POST', dict(data, path=str(path))))

    async def grant(request):
        from .security import grant_create
        data = await body(request)
        expiry = data.pop('expires_at', None)
        if expiry is not None and (isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not time.time() < expiry < time.time() + 3660 * 86400):
            raise Problem('Choose a future token expiry within ten years')
        if set(data) - {'name', 'scopes', 'resource_id'}:
            raise Problem('Unknown token setting')
        def make():
            with api.store.tx():
                result = grant_create(api.store, data)
                api.store.set_setting('grant_expiry:' + result['id'], expiry)
                return result
        result = await run_in_threadpool(make)
        return JSONResponse(dict(result, config={'mcpServers': {'alshitasks': {'type': 'streamable-http', 'url': origin + '/mcp', 'headers': {'Authorization': 'Bearer ' + result['token']}}}}))

    async def connect(request):
        from .portal import Portal
        from .accounts import ORIGIN
        result = await run_in_threadpool(Portal(api.store).connect, ORIGIN, origin + '/oauth/callback')
        state = parse_qs(urlsplit(result['url']).query)['state'][0]
        with pending_lock:
            pending.clear()  # Existing Portal guard permits only one in-flight authorization.
            pending[state] = (digest(request.cookies[COOKIE]), result.pop('local_callback'), time.time() + 300)
        return JSONResponse(result)

    async def callback(request):
        query = parse_qs(request.url.query)
        state = query.get('state', [''])[0]
        with pending_lock:
            entry = pending.get(state)
            if not entry or entry[2] < time.time() or entry[0] != digest(request.cookies[COOKIE]):
                raise Problem('Sign-in expired or belongs to a different browser session', 400)
            pending.pop(state)
        # Only the loopback URL created internally by Portal.connect can be used here.
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            result = await client.get(entry[1], params=list(request.query_params.multi_items()))
        if result.status_code != 200:
            raise Problem('Unable to finish portal sign-in. Return to Settings and reconnect.', 400)
        return RedirectResponse('/', status_code=303)

    async def mcp(request):
        if request.method != 'POST':
            return Response(status_code=405, headers={'Allow': 'POST'})
        if request.headers.get('mcp-protocol-version', '2025-03-26') not in ('2025-03-26', '2025-06-18', '2025-11-25'):
            raise Problem('Unsupported MCP protocol version')
        accepts = request.headers.get('accept', '')
        if 'application/json' not in accepts or 'text/event-stream' not in accepts:
            raise Problem('Accept application/json and text/event-stream', 406)
        message = await body(request)
        if message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            raise Problem('Invalid JSON-RPC request')
        from .mcp_protocol import respond
        token = request.state.bearer
        def execute():
            return respond(message, lambda path, method='GET', data=None: handle(api, path, method, data or {}, False, token), version)
        result = await run_in_threadpool(execute)
        return JSONResponse(result) if result is not None else Response(status_code=202)

    async def index(request):
        html = (assets / 'index.html').read_text(encoding='utf-8')
        # Desktop's CSP intentionally forbids network calls. The server policy is below.
        html = re.sub(r'<meta[^>]+http-equiv="Content-Security-Policy"[^>]*>', '', html)
        return HTMLResponse(html)

    async def health(request):
        return JSONResponse({'ok': not api.stopping, 'version': version})

    routes = [Route('/favicon.ico', lambda request: Response(status_code=204)), Route('/healthz', health), Route('/auth/login', login, methods=['POST']),
              Route('/auth/session', session), Route('/auth/logout', logout, methods=['POST']),
              Route('/browser/backup', backup, methods=['POST']), Route('/browser/ssh-key', ssh_key, methods=['POST']),
              Route('/v1/agent-grants', grant, methods=['POST']), Route('/v1/portal/connect', connect, methods=['POST']),
              Route('/oauth/callback', callback), Route('/mcp', mcp, methods=['GET', 'POST', 'DELETE']),
              Route('/v1/{path:path}', api_request, methods=['GET', 'POST', 'PATCH', 'DELETE']),
              Route('/', index), Route('/index.html', index), Mount('/assets', StaticFiles(directory=assets / 'assets', check_dir=False))]
    app = Starlette(routes=routes)

    async def security(request, call_next):
        token = None
        try:
            if request.headers.get('host') != public.netloc:
                raise Problem('Untrusted host', 403)
            supplied_origin = request.headers.get('origin')
            if supplied_origin is not None and supplied_origin != origin:
                raise Problem('Untrusted origin', 403)
            path = request.url.path
            # OAuth's redirect back to / remains cross-site in browser Fetch
            # Metadata. Public document navigation is safe; API access is not.
            public_navigation = (
                request.method in ('GET', 'HEAD') and path in ('/', '/index.html')
                and request.headers.get('sec-fetch-mode') == 'navigate'
                and request.headers.get('sec-fetch-dest') == 'document'
            )
            if request.headers.get('sec-fetch-site') == 'cross-site' and path != '/oauth/callback' and not public_navigation:
                raise Problem('Cross-site request denied', 403)
            is_public = path in ('/', '/index.html', '/auth/login', '/healthz', '/favicon.ico') or path.startswith('/assets/')
            if path == '/mcp' or LOG_ROUTE.fullmatch(path) or path == '/v1/heartbeat':
                from .security import authorize
                header = request.headers.get('authorization', '')
                if not header.startswith('Bearer '):
                    raise Problem('MCP bearer authentication required', 401)
                request.state.bearer = header[7:]
                def verify():
                    # Validate a token before initialize/list, without assuming a project scope.
                    with api.store.lock:
                        row = api.store.db.execute('SELECT scopes FROM grants WHERE digest=? AND revoked_at IS NULL', (digest(header[7:]),)).fetchone()
                        if not row:
                            raise Problem('Invalid MCP token', 401)
                        scope = json.loads(row[0])[0]
                        resource = api.store.db.execute('SELECT resource_id FROM grants WHERE digest=?', (digest(header[7:]),)).fetchone()[0]
                        authorize(api.store, header[7:], scope, resource)
                await run_in_threadpool(verify)
            elif not is_public:
                token = request.cookies.get(COOKIE, '')
                request.state.csrf = await run_in_threadpool(auth.session, token)
                if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), request.state.csrf):
                    raise Problem('CSRF token required', 403)
            if path == '/auth/login' and supplied_origin != origin:
                raise Problem('Same-origin login required', 403)
            response = await call_next(request)
            if token and path != '/auth/logout':
                cookie(response, token)
        except Problem as error:
            response = JSONResponse({'error': {'message': str(error)}}, status_code=error.status)
        except TimeoutError:
            response = JSONResponse({'error': {'message': 'Request timed out'}}, status_code=408)
        except (ValueError, TypeError, KeyError):
            response = JSONResponse({'error': {'message': 'Malformed request'}}, status_code=400)
        except Exception:
            response = JSONResponse({'error': {'message': 'Server operation failed; your records are retained'}}, status_code=500)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if public.scheme == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    from starlette.middleware.base import BaseHTTPMiddleware
    app.add_middleware(BaseHTTPMiddleware, dispatch=security)
    return app
