import io
import json
from pathlib import Path
import sys
import time
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.portal import Portal, RECONNECT, remote
from alshitasks.security import Secrets
from alshitasks.store import Problem


class OAuthReconnect(unittest.TestCase):
    def setUp(self):
        # These unit tests exercise credential receipts; account fences have
        # dedicated Store-backed concurrency coverage in test_optional_portal.
        self.fence = patch('alshitasks.eligibility.check')
        self.fence.start()
        self.addCleanup(self.fence.stop)

    def request_error(self, status, body, route="/oauth/token/"):
        error = urllib.error.HTTPError("https://fixture.example" + route, status, "error", {}, io.BytesIO(body))
        opener = Mock()
        opener.open.side_effect = error
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(Problem) as caught:
            remote("https://fixture.example", route, body={"grant_type": "refresh_token"}, form=True)
        return caught.exception

    def test_terminal_oauth_errors_require_sign_in_without_leaking_body(self):
        for code in ("invalid_grant", "invalid_client", "unauthorized_client"):
            with self.subTest(code=code):
                error = self.request_error(400, json.dumps({"error": code, "error_description": "secret-fixture"}).encode())
                self.assertEqual(error.status, 401)
                self.assertEqual(str(error), RECONNECT)
                self.assertNotIn("secret-fixture", str(error))

    def test_only_token_errors_trigger_reconnect(self):
        for status, payload, route in (
            (400, b'{"error":"invalid_grant"}', '/desktop/v1/workspaces/x/sync'),
            (503, b'{"error":"invalid_grant"}', '/oauth/token/'),
            (400, b'{"error":"invalid_target"}', '/oauth/token/'),
            (400, b'not-json', '/oauth/token/'),
            (400, b'[]', '/oauth/token/'),
            (400, b'{"error":"invalid_grant","padding":"' + b'x' * 4096 + b'"}', '/oauth/token/'),
        ):
            with self.subTest(status=status, route=route, payload=payload[:30]):
                self.assertEqual(self.request_error(status, payload, route).status, status)

    def test_rejected_refresh_is_not_replayed_by_other_workspace_or_restart(self):
        values = {'credential': json.dumps({'access_token': 'expired', 'refresh_token': 'old', 'client_id': 'fixture', 'expires_at': time.time() - 1})}
        connection = {'state': 'connected', 'origin': 'https://fixture.example'}
        with (
            patch.object(Secrets, 'get', side_effect=lambda key: values[key]),
            patch.object(Secrets, 'put', side_effect=lambda key, value: values.update({key: value})),
            patch('alshitasks.portal.remote', side_effect=Problem(RECONNECT, 401)) as transport,
        ):
            for _ in range(3):
                with self.assertRaises(Problem) as caught:
                    Portal(None)._token(connection, 'credential', 0)
                self.assertEqual(caught.exception.status, 401)
            self.assertEqual(transport.call_count, 1)
        self.assertTrue(json.loads(values['credential'])['reauthorization_required'])

    def test_transient_refresh_failure_does_not_disable_connection(self):
        value = json.dumps({'access_token': 'expired', 'refresh_token': 'valid', 'client_id': 'fixture', 'expires_at': 0})
        with patch.object(Secrets, 'get', return_value=value), patch.object(Secrets, 'put') as save, patch('alshitasks.portal.remote', side_effect=Problem('Unavailable', 503)):
            with self.assertRaises(Problem):
                Portal(None)._token({'state': 'connected', 'origin': 'https://fixture.example'}, 'credential', 0)
            self.assertEqual(save.call_count, 1)
            pending = json.loads(save.call_args.args[1])
            self.assertIn('desktop_refresh_id', pending)
            self.assertNotIn('reauthorization_required', pending)

    def test_lost_response_reuses_durable_attempt_after_restart(self):
        values = {'credential': json.dumps({'access_token': 'expired', 'refresh_token': 'old', 'client_id': 'fixture', 'expires_at': 0})}
        connection = {'state': 'connected', 'origin': 'https://fixture.example'}
        with patch.object(Secrets, 'get', side_effect=lambda key: values[key]), patch.object(Secrets, 'put', side_effect=lambda key, value: values.update({key: value})), patch('alshitasks.portal.remote') as transport:
            transport.side_effect = [Problem('Lost response', 503), {'access_token': 'new', 'refresh_token': 'rotated', 'expires_in': 900}]
            with self.assertRaises(Problem):
                Portal(None)._token(connection, 'credential', 0)
            first = transport.call_args.kwargs['body'].copy()
            self.assertEqual(json.loads(values['credential'])['desktop_refresh_id'], first['desktop_refresh_id'])
            self.assertEqual(Portal(None)._token(connection, 'credential', 0), 'new')
            self.assertEqual(transport.call_args.kwargs['body'], first)
            self.assertNotIn('desktop_refresh_id', json.loads(values['credential']))

    def test_lost_credential_save_recovers_and_expired_receipt_renews_again(self):
        values = {'credential': json.dumps({'access_token': 'expired', 'refresh_token': 'old', 'client_id': 'fixture', 'expires_at': 0})}
        fail_once = [True]
        def save(key, value):
            if json.loads(value).get('refresh_token') == 'rotated' and fail_once[0]:
                fail_once[0] = False
                raise OSError('Simulated interrupted save')
            values[key] = value
        with patch.object(Secrets, 'get', side_effect=lambda key: values[key]), patch.object(Secrets, 'put', side_effect=save), patch('alshitasks.portal.remote') as transport:
            transport.side_effect = [
                {'access_token': 'first', 'refresh_token': 'rotated', 'expires_in': 900},
                {'access_token': 'first', 'refresh_token': 'rotated', 'expires_in': 0},
                {'access_token': 'current', 'refresh_token': 'latest', 'expires_in': 900},
            ]
            connection = {'state': 'connected', 'origin': 'https://fixture.example'}
            with self.assertRaises(OSError):
                Portal(None)._token(connection, 'credential', 0)
            self.assertEqual(Portal(None)._token(connection, 'credential', 0), 'current')
            bodies = [call.kwargs['body'] for call in transport.call_args_list]
            self.assertEqual(bodies[0], bodies[1])
            self.assertNotEqual(bodies[1]['desktop_refresh_id'], bodies[2]['desktop_refresh_id'])
            self.assertEqual(bodies[2]['refresh_token'], 'rotated')

    def test_failed_attempt_save_never_rotates_server_token(self):
        value = json.dumps({'access_token': 'expired', 'refresh_token': 'old', 'client_id': 'fixture', 'expires_at': 0})
        with patch.object(Secrets, 'get', return_value=value), patch.object(Secrets, 'put', side_effect=OSError('Disk unavailable')), patch('alshitasks.portal.remote') as transport:
            with self.assertRaises(OSError):
                Portal(None)._token({'state': 'connected', 'origin': 'https://fixture.example'}, 'credential', 0)
            transport.assert_not_called()

    def test_reconnect_keeps_identity_and_rejects_another_account(self):
        from urllib.parse import parse_qs, urlencode, urlsplit
        import urllib.request
        from alshitasks.paths import Paths
        from alshitasks.store import Store, uid
        for owner in ('existing', 'different'):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as directory:
                store = Store(Paths(directory))
                ident, workspace = uid(), uid()
                origin = 'https://alshival.ai'
                account = {'id': 'existing', 'origin': origin, 'connection': ident, 'name': 'Fixture'}
                with store.tx():
                    store.db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?)', (ident, origin, 'existing', workspace, 'connected', None, '{}'))
                    store.set_setting('active_account', account)
                values = {}
                def transport(origin, path, token=None, body=None, form=False):
                    if path == '/oauth/register/':
                        return {'client_id': 'fixture'}
                    if path == '/oauth/token/':
                        return {'access_token': 'fixture', 'refresh_token': 'fixture-refresh', 'expires_in': 900}
                    return {'resource': origin + '/desktop/v1', 'account_id': owner}
                try:
                    with patch.object(Secrets, 'put', side_effect=lambda key, value: values.update({key: value})), patch.object(Secrets, 'delete', side_effect=lambda key: values.pop(key, None)), patch('alshitasks.portal.remote', side_effect=transport):
                        result = Portal(store).connect(origin)
                        query = parse_qs(urlsplit(result['url']).query)
                        callback = query['redirect_uri'][0] + '?' + urlencode({'state': query['state'][0], 'iss': origin, 'code': 'fixture-code'})
                        if owner == 'existing':
                            with urllib.request.urlopen(callback, timeout=3) as response:
                                self.assertEqual(response.status, 200)
                            self.assertEqual(store.setting('active_account')['id'], 'existing')
                            self.assertEqual(store.setting('credential:' + ident), result['connection'])
                            self.assertEqual(store.db.execute('SELECT workspace FROM connections WHERE id=?', (ident,)).fetchone()[0], workspace)
                        else:
                            with self.assertRaises(urllib.error.HTTPError):
                                urllib.request.urlopen(callback, timeout=3)
                            self.assertEqual(store.setting('active_account'), account)
                        time.sleep(0.6)
                finally:
                    store.db.close()


if __name__ == '__main__':
    unittest.main()
