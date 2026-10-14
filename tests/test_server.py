import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
try:
    from starlette.testclient import TestClient
    from cryptography.fernet import Fernet
    from alshitasks.web import create_app, base_url
except ImportError:
    raise unittest.SkipTest('Install service/server-requirements.txt to test server mode')
from alshitasks.paths import Paths
from alshitasks.store import Store, Problem
from alshitasks.logs import Logs
from alshitasks.api import API
from alshitasks.server_auth import Auth, digest
from alshitasks.server_secrets import Vault


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = Paths(Path(self.temp.name) / 'instance')
        self.store = Store(self.paths)
        self.logs = Logs(self.paths)
        self.api = API(self.store, self.logs)
        self.auth = Auth(self.paths)
        self.password = 'fixture-only-password-123'
        self.auth.password(self.password)
        self.origin = 'http://127.0.0.1:43120'
        self.assets = Path(self.temp.name) / 'assets'
        self.assets.mkdir()
        (self.assets / 'index.html').write_text('<!doctype html><title>Server fixture</title>')
        self.client = TestClient(create_app(self.api, self.origin, self.assets), base_url=self.origin)

    def tearDown(self):
        self.client.close()
        self.store.db.close()
        self.logs.db.close()
        self.temp.cleanup()

    def login(self):
        r = self.client.post('/auth/login', json={'password': self.password}, headers={'Origin': self.origin})
        self.assertEqual(r.status_code, 200, r.text)
        self.csrf = r.json()['csrf']
        return {'X-CSRF-Token': self.csrf, 'Origin': self.origin}

    def test_login_csrf_host_and_no_cookie_mcp(self):
        self.assertEqual(self.client.get('/v1/workspaces').status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'password': self.password}).status_code, 403)
        headers = self.login()
        self.assertEqual(self.client.get('/v1/workspaces').status_code, 200)
        self.assertEqual(self.client.get('/v1/workspaces', headers={'Host': 'evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/v1/workspaces', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.patch('/v1/settings', json={'local_members': []}).status_code, 403)
        self.assertEqual(self.client.patch('/v1/settings', json={'local_members': []}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/mcp', json={}).status_code, 401)
        self.assertEqual(self.client.post('/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/auth/session').status_code, 401)

    def test_reset_expiry_and_rate_limit(self):
        self.login()
        self.auth.password(self.password + 'new')
        self.assertEqual(self.client.get('/auth/session').status_code, 401)

        for _ in range(10):
            self.assertEqual(self.client.post('/auth/login', json={'password': 'wrong'}, headers={'Origin': self.origin}).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'password': 'wrong'}, headers={'Origin': self.origin}).status_code, 429)
        self.auth.password(self.password)
        self.login()
        with self.auth.db() as db:
            db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.client.get('/auth/session').status_code, 401)

    def test_cross_site_document_navigation_keeps_api_protected(self):
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        for path in ('/', '/index.html'):
            self.assertEqual(self.client.get(path, headers=navigation).status_code, 200)
            self.assertEqual(self.client.get(path, headers={**navigation, 'Sec-Fetch-Mode': 'cors'}).status_code, 403)
        headers = self.login()
        self.assertEqual(self.client.get('/v1/workspaces', headers=navigation).status_code, 403)
        self.assertEqual(self.client.post('/v1/portal/connect', json={}, headers={**headers, **navigation}).status_code, 403)
        self.assertEqual(self.client.post('/auth/login', json={'password': self.password}, headers={**headers, **navigation}).status_code, 403)

    def test_browser_cannot_use_local_privileged_operations(self):
        headers = self.login()
        for route in ['/v1/stop', '/v1/backup', '/v1/ssh/key', '/v1/ssh/open', '/v1/mcp']:
            self.assertEqual(self.client.post(route, json={'path': '/etc/passwd'}, headers=headers).status_code, 403, route)
        self.assertEqual(self.client.patch('/v1/settings', json={'http_port': 8888}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/v1/ssh', json={'identity_file': '/etc/passwd'}, headers=headers).status_code, 400)

    def mcp(self, grant, method, params=None):
        return self.client.post('/mcp', json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}},
            headers={'Authorization':'Bearer '+grant['token'],'Accept':'application/json, text/event-stream'})

    def test_mcp_shared_catalog_scopes_revocation_expiry(self):
        headers = self.login()
        grant = self.client.post('/v1/agent-grants', json={'name':'test','scopes':['project:read']}, headers=headers).json()
        self.assertEqual(self.mcp(grant, 'initialize').status_code, 200)
        tools = self.mcp(grant, 'tools/list').json()['result']['tools']
        self.assertGreaterEqual(len(tools), 19)
        result = self.mcp(grant, 'tools/call', {'name':'workspace_list','arguments':{}}).json()['result']
        self.assertFalse(result.get('isError'), result)
        with self.store.tx():
            self.store.set_setting('grant_expiry:' + grant['id'], time.time() - 1)
        self.assertEqual(self.mcp(grant, 'initialize').status_code, 401)
        with self.store.tx():
            self.store.set_setting('grant_expiry:' + grant['id'], None)
        self.client.request('DELETE', '/v1/projects/keys/'+grant['id'], json={}, headers=headers)
        self.assertEqual(self.mcp(grant, 'initialize').status_code, 401)
        self.assertNotIn(grant['token'], self.paths.data.joinpath('projects.sqlite').read_bytes().decode(errors='ignore'))

    def test_attachments_backup_and_traversal(self):
        headers = self.login()
        with self.store.tx():
            item = self.store.create({'kind':'task','data':{'title':'Server fixture'},'idempotency_key':'server-test-create'})
        ident = item['id']
        response = self.client.post('/v1/attachments', json={'item_id':ident,'name':'hello.txt','content':base64.b64encode(b'hello').decode()}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get('/v1/attachments/'+response.json()['id']).json()
        self.assertEqual(base64.b64decode(saved['content']), b'hello')
        self.assertEqual(self.client.get('/v1/attachments/%2e%2e%2fconfig').status_code, 403)
        response = self.client.post('/browser/backup', headers=headers)
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else '')
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('projects.sqlite', archive.namelist())
            self.assertFalse(any('vault' in n or 'auth' in n for n in archive.namelist()))

    def test_vault_restart_and_wrong_key(self):
        key = Path(self.temp.name) / 'key'
        key.write_bytes(Fernet.generate_key())
        vault = Vault(self.paths.config.parent, key)
        vault.put('fixture', 'private fixture secret')
        self.assertNotIn(b'private fixture secret', vault.path('fixture').read_bytes())
        self.assertEqual(Vault(self.paths.config.parent, key).get('fixture'), 'private fixture secret')
        key.write_bytes(Fernet.generate_key())
        with self.assertRaises(Problem):
            Vault(self.paths.config.parent, key)

    def test_callback_bound_to_browser_session(self):
        headers = self.login()
        fake = {'url':'https://alshival.ai/oauth/authorize/?state=fixture', 'connection':'fixture', 'local_callback':'http://127.0.0.1:12345/callback'}
        with patch('alshitasks.portal.Portal.connect', return_value=fake):
            r = self.client.post('/v1/portal/connect', json={}, headers=headers)
            self.assertNotIn('local_callback', r.json())
        self.client.cookies.clear()
        self.login()
        self.assertEqual(self.client.get('/oauth/callback?state=fixture').status_code, 400)

    def test_complete_portal_callback_uses_configured_origin_and_pkce(self):
        from urllib.parse import urlsplit, parse_qs, urlencode
        values, calls = {}, []
        def transport(origin, path, token=None, body=None, form=False):
            calls.append((path, body))
            if path == '/oauth/register/': return {'client_id': 'server-fixture'}
            if path == '/oauth/token/': return {'access_token':'access-fixture','refresh_token':'refresh-fixture','expires_in':900}
            return {'resource':origin+'/desktop/v1','account_id':'server-fixture-owner'}
        from alshitasks.security import Secrets
        headers = self.login()
        with patch.object(Secrets, 'put', side_effect=lambda key,value: values.update({key:value})), patch.object(Secrets, 'delete', side_effect=lambda key: values.pop(key,None)), patch('alshitasks.portal.remote', side_effect=transport):
            response = self.client.post('/v1/portal/connect', json={}, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            query = parse_qs(urlsplit(response.json()['url']).query)
            self.assertEqual(query['redirect_uri'], [self.origin+'/oauth/callback'])
            self.assertEqual(query['code_challenge_method'], ['S256'])
            callback = '/oauth/callback?' + urlencode({'state':query['state'][0], 'iss':'https://alshival.ai', 'code':'fixture-code'})
            navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
            completed = self.client.get(callback, headers=navigation, follow_redirects=False)
            self.assertEqual(completed.status_code, 303, completed.text)
            self.assertEqual(self.client.get(completed.headers['location'], headers=navigation).status_code, 200)
            self.assertEqual(self.store.setting('active_account')['id'], 'server-fixture-owner')
            self.assertEqual(self.client.get(callback).status_code, 400)
            self.assertEqual(calls[1][1]['redirect_uri'], self.origin+'/oauth/callback')
            time.sleep(0.6)

    def test_https_cookies_and_readonly_mcp_write_denied(self):
        with TestClient(create_app(self.api, 'https://tasks.example.com'), base_url='https://tasks.example.com') as client:
            response = client.post('/auth/login', json={'password':self.password}, headers={'Origin':'https://tasks.example.com'})
            self.assertIn('Secure', response.headers['set-cookie'])
            self.assertIn('HttpOnly', response.headers['set-cookie'])
        headers = self.login()
        grant = self.client.post('/v1/agent-grants', json={'name':'read only','scopes':['project:read']}, headers=headers).json()
        result = self.mcp(grant,'tools/call',{'name':'project_create','arguments':{'kind':'task','data':{'title':'Denied'},'idempotency_key':'read-only-test'}}).json()['result']
        self.assertTrue(result['isError'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM items WHERE json_extract(data,'$.title')='Denied'").fetchone()[0], 0)

    def test_base_url_rules_and_oversize(self):
        for url in ['http://example.com', 'https://user:pass@example.com', 'https://example.com/subpath']:
            with self.assertRaises(ValueError): base_url(url)
        headers = self.login()
        self.assertEqual(self.client.post('/v1/secret', content='x'*(1024**2+1), headers={**headers,'Content-Type':'application/json'}).status_code, 413)


if __name__ == '__main__':
    unittest.main()
