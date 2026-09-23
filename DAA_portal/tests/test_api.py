import os
import tempfile
import unittest
from pathlib import Path


class ArticleApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ['CONTENT_DIR'] = cls.temp.name
        os.environ['DAA_API_TOKEN'] = 'test-token-with-at-least-thirty-two-characters'
        from app import app, limiter
        cls.app = app
        limiter.enabled = False

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_article_lifecycle_and_local_files(self):
        client = self.app.test_client()
        url = '/api/v1/articles/News/example.md'
        headers = {'Authorization': 'Bearer ' + os.environ['DAA_API_TOKEN'],
                   'Content-Type': 'text/markdown'}
        self.assertEqual(client.put(url, data='# First', headers=headers).status_code, 201)
        self.assertEqual(client.get(url, headers=headers).data, b'# First')
        self.assertEqual(Path(self.temp.name, 'News/example.md').read_text(), '# First')
        self.assertEqual(client.put(url, data='# Revised', headers=headers).status_code, 200)
        listing = client.get('/api/v1/articles', headers=headers).json['articles']
        self.assertEqual([x['path'] for x in listing], ['News/example.md'])
        self.assertEqual(client.delete(url, headers=headers).status_code, 204)
        self.assertEqual(client.get(url, headers=headers).status_code, 404)

    def test_auth_and_path_restrictions(self):
        client = self.app.test_client()
        url = '/api/v1/articles/News/blocked.md'
        self.assertEqual(client.put(url, data='bad', content_type='text/markdown').status_code, 401)
        headers = {'Authorization': 'Bearer ' + os.environ['DAA_API_TOKEN'],
                   'Content-Type': 'text/markdown'}
        for path in ('News/.secret.md', 'News/other/more.md', '../escape.md'):
            response = client.put('/api/v1/articles/' + path, data='bad', headers=headers)
            self.assertIn(response.status_code, (400, 404))
        self.assertFalse(Path(self.temp.name, 'News/blocked.md').exists())


if __name__ == '__main__':
    unittest.main()
