import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clients import daa_client


class ClientConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name, 'client.env')
        self.config.write_text(
            'DAA_API_BASE_URL=https://daa.example.test:8443\n'
            'DAA_API_TOKEN=' + 'x' * 40 + '\n', encoding='ascii')
        self.config.chmod(0o600)
        self.patch = patch.object(daa_client, 'CONFIG', self.config)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_https_origin_and_private_config_are_accepted(self):
        self.assertEqual(daa_client.settings()['DAA_API_BASE_URL'],
                         'https://daa.example.test:8443')

    def test_http_or_url_credentials_are_rejected(self):
        for origin in ('http://daa.example.test:8443',
                       'https://user:password@daa.example.test:8443',
                       'https://daa.example.test:8443/secret-path',
                       'https://daa.example.test:not-a-port'):
            self.config.write_text('DAA_API_BASE_URL=' + origin + '\n'
                                   'DAA_API_TOKEN=' + 'x' * 40 + '\n',
                                   encoding='ascii')
            with self.assertRaises(RuntimeError):
                daa_client.settings()

    def test_world_readable_config_is_rejected(self):
        self.config.chmod(0o644)
        with self.assertRaises(RuntimeError):
            daa_client.settings()

    def test_redirect_is_rejected(self):
        self.assertIsNone(daa_client.NoRedirect().redirect_request(
            None, None, 302, 'Found', {}, 'https://another.example.test'))


if __name__ == '__main__':
    unittest.main()
