"""Loopback-only dashboard, explicit route allowlist and CSRF token."""
import hmac
import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .runtime import Runtime
from .store import encode

WEB = Path(__file__).resolve().parent.parent / 'web'
STATIC = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/style.css': ('style.css', 'text/css'), '/scene.js': ('scene.js', 'text/javascript')}


def make_server(store, port=8765):
    runtime, token = Runtime(store), secrets.token_urlsafe(32)
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def handle(self):
            try:
                super().handle()
            except (ConnectionError, TimeoutError):
                # A browser may cancel an export or navigate away mid-response.
                pass

        def log_message(self, *args):
            pass

        def respond(self, status, payload, content_type='application/json'):
            body = payload if isinstance(payload, bytes) else encode(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            if self.headers.get('Host') not in allowed_hosts:
                self.respond(403, {'error': 'Loopback Host required'})
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in allowed_hosts}:
                self.respond(403, {'error': 'Cross-origin requests rejected'})
                return False
            return True

        def do_GET(self):
            if not self.valid_host():
                return
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            try:
                if parsed.path in STATIC:
                    file, content_type = STATIC[parsed.path]
                    self.respond(200, (WEB / file).read_bytes(), content_type)
                elif parsed.path == '/api/status':
                    self.respond(200, runtime.snapshot(params.get('session', [None])[0]) | {'csrf': token})
                elif parsed.path == '/api/events':
                    self.respond(200, store.events(params.get('session', [runtime.session or 'system'])[0],
                        params.get('kind', [None])[0], params.get('limit', [100])[0], params.get('before', [None])[0]))
                elif parsed.path == '/api/export':
                    session = params.get('session', [runtime.session or 'system'])[0]
                    # Stream instead of loading a potentially multi-GB journal in RAM.
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-ndjson')
                    self.send_header('Content-Disposition', 'attachment; filename="quantum-solana-trader-events.jsonl"')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    for line in store.export(session):
                        self.wfile.write(line.encode())
                else:
                    self.respond(404, {'error': 'Route not found'})
            except (ValueError, KeyError, TypeError):
                self.respond(400, {'error': 'Invalid request'})

        def do_POST(self):
            if not self.valid_host():
                return
            if not hmac.compare_digest(self.headers.get('X-QST-Token', '').encode(), token.encode()):
                self.respond(403, {'error': 'Missing or invalid CSRF token'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Request body must be 1–4096 bytes')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected JSON object')
                if self.path == '/api/run':
                    operation = data.get('operation')
                    if operation not in ('demo', 'download', 'train', 'evaluate', 'paper', 'scan'):
                        raise ValueError('Unknown operation')
                    runtime.launch(operation, data)
                elif self.path == '/api/control':
                    runtime.control(data.get('action'))
                else:
                    self.respond(404, {'error': 'Route not found'})
                    return
                self.respond(202, {'ok': True})
            except (ValueError, TypeError, KeyError) as error:
                self.respond(400, {'error': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.runtime = runtime
    return server
