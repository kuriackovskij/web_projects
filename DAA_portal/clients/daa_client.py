#!/usr/bin/env python3
"""DAA article-management CLI over a private HTTPS API."""
import argparse
import json
import os
import ssl
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get('DAA_CLIENT_CONFIG',
                             Path.home() / '.config/daa/client.env'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the bearer token to a redirect destination."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def settings():
    if stat.S_IMODE(CONFIG.stat().st_mode) & 0o077:
        raise RuntimeError('client config must not be readable by group or others')
    values = dict(line.split('=', 1) for line in CONFIG.read_text(encoding='ascii').splitlines()
                  if line and not line.startswith('#') and '=' in line)
    if len(values.get('DAA_API_TOKEN', '')) < 32:
        raise RuntimeError('DAA API credential is missing')
    base = values.get('DAA_API_BASE_URL', '').rstrip('/')
    try:
        parsed = urllib.parse.urlsplit(base)
        parsed.port  # Invalid ports must fail during config validation.
    except ValueError as exc:
        raise RuntimeError('DAA_API_BASE_URL is invalid') from exc
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise RuntimeError('DAA_API_BASE_URL must be a bare HTTPS origin')
    values['DAA_API_BASE_URL'] = base
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    for name in ('get', 'put'):
        p = sub.add_parser(name)
        p.add_argument('article', help='Category/filename.md')
        p.add_argument('file', help='local destination for get, source for put')
    p = sub.add_parser('delete')
    p.add_argument('article', help='Category/filename.md')
    p = sub.add_parser('move', help='rename an article or move it to another category')
    p.add_argument('article', help='current Category/filename.md')
    p.add_argument('destination', help='new Category/filename.md')
    args = parser.parse_args()
    config = settings()

    base = config['DAA_API_BASE_URL']
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )
    try:
        path = '/api/v1/articles'
        if args.command != 'list':
            path += '/' + urllib.parse.quote(args.article, safe='/')
        if args.command == 'move':
            path += '/move'
        headers = {'Authorization': 'Bearer ' + config['DAA_API_TOKEN']}
        method = {'list': 'GET', 'get': 'GET', 'put': 'PUT',
                  'delete': 'DELETE', 'move': 'POST'}[args.command]
        data = None
        if args.command == 'put':
            data = Path(args.file).read_bytes()
            headers['Content-Type'] = 'text/markdown; charset=utf-8'
        elif args.command == 'move':
            data = json.dumps({'destination': args.destination}).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base + path, data=data,
                                         headers=headers, method=method)
        with opener.open(request, timeout=30) as response:
            if args.command == 'get':
                target = Path(args.file)
                with target.open('xb') as output:
                    output.write(response.read())
            elif args.command != 'delete':
                print(json.dumps(json.load(response), ensure_ascii=False))
            else:
                print('deleted')
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'DAA API returned HTTP {exc.code}') from exc


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print(f'daa-client: {exc}', file=sys.stderr)
        sys.exit(1)
