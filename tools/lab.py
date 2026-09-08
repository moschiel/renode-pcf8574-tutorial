"""Launch the local web panel or an interactive Renode Monitor."""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse
import webbrowser

from renode_client import ROOT, Renode, find_renode


class Lab:
    def __init__(self, renode):
        self.renode = renode
        self.lock = threading.RLock()
        self.running = True
        self.error = None
        self.stop = threading.Event()
        renode.advance(.05)
        self.snapshot = renode.state()

    def work(self):
        while not self.stop.is_set():
            begin = time.monotonic()
            try:
                with self.lock:
                    if self.running:
                        self.renode.advance(.025)
                        self.snapshot = self.renode.state()
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                    self.running = False
                self.stop.set()
            self.stop.wait(max(0, .025 - (time.monotonic() - begin)))

    def state(self):
        with self.lock:
            return dict(self.snapshot, running=self.running, error=self.error)

    def control(self, body):
        with self.lock:
            if self.error:
                raise RuntimeError(self.error)
            action = body.get('action')
            if action == 'button':
                self.renode.button(body.get('pin'), body.get('pressed'))
            elif action == 'pause':
                self.running = False
            elif action == 'resume':
                self.running = True
            elif action == 'step':
                self.running = False
                self.renode.advance(.25)
            else:
                raise ValueError('Unknown action')
            self.snapshot = self.renode.state()
            return self.state()


def handler_for(lab):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type='application/json'):
            payload = json.dumps(data).encode() if content_type == 'application/json' else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == '/api/state':
                self.reply(200, lab.state())
            elif self.path in ('/', '/index.html'):
                self.reply(200, (ROOT / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
            else:
                self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                self.reply(403, {'error': 'Use the local panel origin'})
                return
            if self.path != '/api/control':
                self.reply(404, {'error': 'Not found'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected an object')
                self.reply(200, lab.control(body))
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception as exc:
                self.reply(503, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renode', help='Renode executable path')
    parser.add_argument('--port', type=int, default=8000, help='Local HTTP port')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--monitor', action='store_true', help='Open the text Monitor instead')
    parser.add_argument('--script', help='File to load with --monitor (default: scripts/demo.resc)')
    args = parser.parse_args()
    if args.script and not args.monitor:
        parser.error('--script requires --monitor')
    executable = find_renode(args.renode)
    if args.monitor:
        with tempfile.TemporaryDirectory(prefix='pcf-monitor-') as tmp:
            return subprocess.call([executable, '--config', str(Path(tmp) / 'renode.config'),
                                    '--console', '--disable-gui', '--plain', args.script or 'scripts/demo.resc'],
                                   cwd=ROOT, env=dict(os.environ, TEMP=tmp, TMP=tmp, TMPDIR=tmp))
    print('Starting Renode and compiling the C# model...', flush=True)
    with Renode(executable) as renode:
        lab = Lab(renode)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(lab))
        worker = threading.Thread(target=lab.work, daemon=True)
        worker.start()
        url = 'http://127.0.0.1:%d' % server.server_port
        print('Lab ready: %s (Ctrl+C to stop)' % url, flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            pass
        finally:
            lab.stop.set()
            server.server_close()
            worker.join(timeout=25)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
