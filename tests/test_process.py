import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from cryptography.fernet import Fernet


LAUNCH = """
import sys
import autosign.__main__ as main
create = main.create_server
def local_server(app, **kwargs):
    kwargs.update(host='127.0.0.1', port=int(sys.argv[1]))
    return create(app, **kwargs)
main.create_server = local_server
main.main()
"""


class ProcessTests(unittest.TestCase):
    def test_single_instance_and_sigterm_with_keepalive_connection(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as data_dir:
            env = dict(os.environ, AUTOSIGN_DATA_DIR=data_dir,
                       AUTOSIGN_ADMIN_PASSWORD='process-test-password',
                       AUTOSIGN_SECRET_KEY=Fernet.generate_key().decode())
            command = [sys.executable, '-c', LAUNCH, str(port)]
            cwd = Path(__file__).resolve().parents[1]
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            connection = None
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                        connection.request('GET', '/healthz')
                        response = connection.getresponse()
                        response.read()
                        if response.status == 200:
                            break
                    except OSError:
                        if connection:
                            connection.close()
                        time.sleep(0.05)
                else:
                    self.fail('service did not become healthy')
                duplicate = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=5)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertIn('已有签到服务运行', duplicate.stderr.decode())
                # The HTTP/1.1 socket deliberately stays open during shutdown.
                process.terminate()
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertNotIn(b'Traceback', stderr)
            finally:
                if connection:
                    connection.close()
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
