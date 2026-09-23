#!/usr/bin/env python3
"""Hermes CLI for DAA article management through a restricted SSH tunnel."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path.home() / '.config/daa/client.env'


def settings():
    values = dict(line.split('=', 1) for line in CONFIG.read_text(encoding='ascii').splitlines()
                  if line and not line.startswith('#') and '=' in line)
    if len(values.get('DAA_API_TOKEN', '')) < 32:
        raise RuntimeError('DAA API credential is missing')
    for name in ('DAA_SSH_DESTINATION', 'DAA_FORWARD_TARGET', 'DAA_SSH_IDENTITY'):
        if not values.get(name):
            raise RuntimeError(f'{name} is missing from client config')
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

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    tunnel = subprocess.Popen([
        'ssh', '-N', '-L', f'127.0.0.1:{port}:{config["DAA_FORWARD_TARGET"]}',
        '-i', config['DAA_SSH_IDENTITY'], '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=10', config['DAA_SSH_DESTINATION'],
    ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        base = f'http://127.0.0.1:{port}'
        for _ in range(50):
            if tunnel.poll() is not None:
                raise RuntimeError('SSH tunnel failed: ' + tunnel.stderr.read().decode().strip())
            try:
                urllib.request.urlopen(base + '/', timeout=0.2)
                break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError('SSH tunnel did not become ready')

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
        with urllib.request.urlopen(request, timeout=30) as response:
            if args.command == 'get':
                target = Path(args.file)
                if target.exists():
                    raise RuntimeError('destination already exists')
                target.write_bytes(response.read())
            elif args.command != 'delete':
                print(json.dumps(json.load(response), ensure_ascii=False))
            else:
                print('deleted')
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'DAA API returned HTTP {exc.code}') from exc
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=3)
        except subprocess.TimeoutExpired:
            tunnel.kill()


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print(f'daa-client: {exc}', file=sys.stderr)
        sys.exit(1)
