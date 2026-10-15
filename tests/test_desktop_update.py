import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if sys.platform != 'linux':
    raise unittest.SkipTest('The Debian helper is Linux-only')

spec = importlib.util.spec_from_file_location('desktop_update', Path(__file__).resolve().parents[1] / 'desktop/update-linux.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class DesktopUpdateTests(unittest.TestCase):
    def release(self):
        return {'tag_name': 'v1.2.3', 'assets': [{'name': name, 'browser_download_url': f'https://github.com/Alshival-Ai/alshitasks/releases/download/v1.2.3/{name}'} for name in ['alshitasks_1.2.3_amd64.deb', 'SHA256SUMS']]}

    def test_strict_stable_asset_selection(self):
        self.assertEqual(update.select(self.release(), 'amd64')[0], '1.2.3')
        for change in ({'draft': True}, {'prerelease': True}, {'tag_name': 'v1.2.3-beta'}, {'assets': []}):
            with self.assertRaises((ValueError, KeyError)):
                update.select({**self.release(), **change}, 'amd64')
        release = self.release()
        release['assets'][0]['browser_download_url'] = 'https://example.test/package.deb'
        with self.assertRaises(ValueError):
            update.select(release, 'amd64')

    def test_hash_and_duplicate_checksums(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'fixture.deb'
            package.write_bytes(b'package')
            line = hashlib.sha256(b'package').hexdigest() + '  fixture.deb'
            update.verify(package, line)
            for text in [line + '\n' + line, 'bad fixture.deb', line.replace('fixture', 'other')]:
                with self.assertRaises(ValueError):
                    update.verify(package, text)
            package.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                update.verify(package, line)

    def test_other_user_desktop_defers_install_without_stopping_services(self):
        pending = {'version': '1.2.3', 'name': 'alshitasks_1.2.3_amd64.deb', 'sums': ''}
        with patch.object(update, 'read', return_value=pending), patch.object(update, 'installed', return_value='1.2.2'), patch.object(update, 'newer', return_value=True), patch.object(update, 'run', return_value=''), patch.object(update, 'desktop_pids', return_value=[(100, 1000), (200, 1001)]), patch.object(update, 'status') as status, patch.object(update, 'user_services') as services:
            self.assertFalse(update.install(100, 1000))
            services.assert_not_called()
            self.assertIn('other', status.call_args.kwargs['error'])

    def test_requester_identity_and_package_holds(self):
        pending = {'version': '1.2.3', 'name': 'alshitasks_1.2.3_amd64.deb', 'sums': ''}
        with patch.object(update, 'read', return_value=pending), patch.object(update, 'installed', return_value='1.2.2'), patch.object(update, 'newer', return_value=True), patch.object(update, 'run', return_value=''), patch.object(update, 'desktop_pids', return_value=[(100, 1000)]):
            with self.assertRaises(ValueError):
                update.install(100, 1001)
        with patch.object(update, 'read', return_value=pending), patch.object(update, 'installed', return_value='1.2.2'), patch.object(update, 'newer', return_value=True), patch.object(update, 'run', return_value='alshitasks'):
            with self.assertRaisesRegex(RuntimeError, 'paused'):
                update.install()

    def test_package_manager_failure_restarts_previously_running_services(self):
        pending = {'version': '1.2.3', 'name': 'alshitasks_1.2.3_amd64.deb', 'sums': ''}
        with patch.object(update, 'read', return_value=pending), patch.object(update, 'installed', return_value='1.2.2'), patch.object(update, 'newer', return_value=True), patch.object(update, 'run', return_value='') as run, patch.object(update, 'desktop_pids', return_value=[]), patch.object(update, 'status'), patch.object(update, 'verify'), patch.object(update, 'user_services', return_value=[['runuser', '-u', 'root']]), patch.object(update, 'backend_uids', return_value=set()), patch.object(update.subprocess, 'run', side_effect=RuntimeError('Package lock timed out')):
            with self.assertRaisesRegex(RuntimeError, 'lock'):
                update.install()
            self.assertIn(unittest.mock.call(['runuser', '-u', 'root', 'start', 'alshitasks.service']), run.call_args_list)

    def test_no_root_no_mutation(self):
        with patch.object(update.os, 'geteuid', return_value=1000), patch.object(update, 'STATE') as state:
            with self.assertRaises(PermissionError):
                update.main(['--pending'])
            state.mkdir.assert_not_called()

    def test_electron_flattened_argv_does_not_count_renderers(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid, args in [('101', b'/usr/lib/alshitasks/electron/electron /usr/lib/alshitasks\0'), ('102', b'electron --type=renderer\0'), ('103', b'electron\0--type=zygote\0')]:
                (proc / pid).mkdir()
                (proc / pid / 'cmdline').write_bytes(args)
            with patch.object(update.os, 'readlink', return_value='/usr/lib/alshitasks/electron/electron'):
                self.assertEqual([pid for pid, uid in update.desktop_pids(proc)], [101])

    def test_partial_package_is_not_reported_as_installed(self):
        for state in ['install ok unpacked', 'install ok half-configured']:
            with patch.object(update, 'run', return_value=state + '\t1.2.3'):
                with self.assertRaisesRegex(RuntimeError, 'repair'):
                    update.installed()
        with patch.object(update, 'run', return_value='install ok installed\t1.2.3'):
            self.assertEqual(update.installed(), '1.2.3')
