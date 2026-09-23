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
        self.assertEqual(client.get(url, headers=headers).data, b'# Revised')
        self.assertEqual(Path(self.temp.name, 'News/example.md').read_text(), '# Revised')
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

    def test_rename_and_move_preserve_content_but_change_reader_url(self):
        client = self.app.test_client()
        auth = {'Authorization': 'Bearer ' + os.environ['DAA_API_TOKEN']}
        source = '/api/v1/articles/News/to-move.md'
        renamed = '/api/v1/articles/News/renamed.md'
        moved = '/api/v1/articles/Research/renamed.md'
        put_headers = {**auth, 'Content-Type': 'text/markdown'}
        created = client.put(source, data='# Move me', headers=put_headers)
        self.assertEqual(created.status_code, 201)
        old_hash = created.json['hash']

        rename_result = client.post(source + '/move',
                                    json={'destination': 'News/renamed.md'},
                                    headers=auth)
        self.assertEqual(rename_result.status_code, 200)
        self.assertEqual(rename_result.json['previous_path'], 'News/to-move.md')
        self.assertNotEqual(rename_result.json['hash'], old_hash)
        self.assertEqual(client.get(source, headers=auth).status_code, 404)
        self.assertEqual(client.get(renamed, headers=auth).data, b'# Move me')
        self.assertEqual(client.get('/' + old_hash).status_code, 404)

        move_result = client.post(renamed + '/move',
                                  json={'destination': 'Research/renamed.md'},
                                  headers=auth)
        self.assertEqual(move_result.status_code, 200)
        self.assertEqual(client.get(renamed, headers=auth).status_code, 404)
        self.assertEqual(client.get(moved, headers=auth).data, b'# Move me')
        self.assertFalse(Path(self.temp.name, 'News/to-move.md').exists())
        self.assertTrue(Path(self.temp.name, 'Research/renamed.md').is_file())

        updated = client.put(moved, data='# Updated after move', headers=put_headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json['hash'], move_result.json['hash'])
        self.assertEqual(client.get(moved, headers=auth).data, b'# Updated after move')
        self.assertEqual(client.get('/' + updated.json['hash']).status_code, 200)
        self.assertEqual(client.delete(moved, headers=auth).status_code, 204)

    def test_move_validation_and_collision(self):
        client = self.app.test_client()
        auth = {'Authorization': 'Bearer ' + os.environ['DAA_API_TOKEN']}
        put_headers = {**auth, 'Content-Type': 'text/markdown'}
        source = '/api/v1/articles/News/move-validation.md'
        target = '/api/v1/articles/News/already-there.md'
        self.assertEqual(client.put(source, data='source', headers=put_headers).status_code, 201)
        self.assertEqual(client.put(target, data='target', headers=put_headers).status_code, 201)
        self.assertEqual(client.post(source + '/move',
                                     json={'destination': 'News/already-there.md'}).status_code, 401)
        self.assertEqual(client.post(source + '/move',
                                     data='{}', headers=auth).status_code, 415)
        for payload in ({}, {'destination': 17}, {'destination': 'News/move-validation.md'},
                        {'destination': 'News/.hidden.md'},
                        {'destination': 'News/nested/file.md'},
                        {'destination': 'News/new.md', 'extra': True}):
            self.assertEqual(client.post(source + '/move', json=payload,
                                         headers=auth).status_code, 400)
        self.assertEqual(client.post(source + '/move',
                                     json={'destination': 'News/already-there.md'},
                                     headers=auth).status_code, 409)
        self.assertEqual(client.get(source, headers=auth).data, b'source')
        self.assertEqual(client.get(target, headers=auth).data, b'target')
        self.assertEqual(client.post('/api/v1/articles/News/absent.md/move',
                                     json={'destination': 'News/new.md'},
                                     headers=auth).status_code, 404)
        self.assertEqual(client.delete(source, headers=auth).status_code, 204)
        self.assertEqual(client.delete(target, headers=auth).status_code, 204)


if __name__ == '__main__':
    unittest.main()
