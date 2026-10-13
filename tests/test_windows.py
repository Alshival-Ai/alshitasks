"""Exercise real Windows security and transport, not mocked POSIX behavior."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from alshitasks.paths import Paths


@unittest.skipUnless(os.name == "nt", "Windows security and transport")
class WindowsSupport(unittest.TestCase):
    def test_private_acl(self):
        import win32security as security
        from alshitasks.platform_support import user_sid
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(directory)
            for path in (paths.data, paths.config, paths.runtime):
                sd = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
                self.assertTrue(sd.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED)
                acl = sd.GetSecurityDescriptorDacl()
                allowed = {str(user_sid()), str(security.CreateWellKnownSid(security.WinLocalSystemSid))}
                self.assertEqual({str(acl.GetAce(i)[2]) for i in range(acl.GetAceCount())}, allowed)

    def test_real_transport_auth_browser_rejection_lock_and_restart(self):
        script = Path(__file__).resolve().parents[1] / "service/alshitasks-cli.py"
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(directory)
            command = [sys.executable, str(script), "--root", directory]
            process = subprocess.Popen(command + ["service"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                for _ in range(150):
                    if paths.endpoint.exists():
                        break
                    if process.poll() is not None:
                        self.fail(process.stderr.read().decode())
                    time.sleep(.05)
                endpoint = json.loads(paths.endpoint.read_text())
                def request(headers=None, route="/v1/health", method="GET"):
                    connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=5)
                    connection.request(method, route, headers=headers or {})
                    response = connection.getresponse()
                    response.read()
                    connection.close()
                    return response.status
                owner = {"X-Alshival-Local": endpoint["token"]}
                self.assertEqual(request(), 401)
                self.assertEqual(request({"X-Alshival-Local": "wrong"}), 401)
                self.assertEqual(request(owner), 200)
                self.assertEqual(request({**owner, "Origin": "https://example.com"}), 403)
                self.assertEqual(request({**owner, "X-Alshival-Agent": "1"}), 401)
                duplicate = subprocess.run(command + ["service"], capture_output=True, timeout=10)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(request(owner), 200)
                self.assertEqual(request(owner, "/v1/stop", "POST"), 200)
                process.wait(timeout=10)
                self.assertFalse(paths.endpoint.exists())
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
                process.stderr.close()
