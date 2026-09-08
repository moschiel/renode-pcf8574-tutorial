"""Small client of Renode's Robot Framework remote server (XML-RPC).

Only Python's standard library is needed; Robot keywords are served by Renode.
All access is serialized because one simulation has one mutable state.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import xmlrpc.client

ROOT = Path(__file__).resolve().parents[1]


def find_renode(explicit=None):
    candidate = explicit or os.environ.get('RENODE') or shutil.which('renode')
    if not candidate and os.name == 'nt':
        candidate = str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Renode/renode.exe')
    if not candidate or not (shutil.which(candidate) or Path(candidate).is_file()):
        raise RuntimeError('Renode not found. Use --renode with the executable path.')
    return candidate


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class TimeoutTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = 20
        return connection


class Renode:
    def __init__(self, executable=None, firmware=True):
        self.lock = threading.RLock()
        self.seconds = 0.0
        self.buttons = [False] * 4
        self.process = None
        self.temp = tempfile.TemporaryDirectory(prefix='pcf-lab-')
        self.log = open(Path(self.temp.name) / 'renode.log', 'w+', encoding='utf-8')
        try:
            rpc_port = free_port()
            monitor_port = free_port()
            env = dict(os.environ, TEMP=self.temp.name, TMP=self.temp.name, TMPDIR=self.temp.name)
            self.process = subprocess.Popen(
                [find_renode(executable), '--disable-gui', '--plain', '--hide-log',
                 '--config', str(Path(self.temp.name) / 'renode.config'),
                 '-P', str(monitor_port), '--robot-server-port', str(rpc_port)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=self.log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.rpc = xmlrpc.client.ServerProxy('http://localhost:%d/' % rpc_port,
                                                 transport=TimeoutTransport())
            deadline = time.monotonic() + 30
            while True:
                if self.process.poll() is not None:
                    self.log.seek(0)
                    raise RuntimeError('Renode exited during startup:\n' + self.log.read())
                try:
                    self.rpc.get_keyword_names()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Renode remote server did not start within 30 seconds.')
                    time.sleep(.15)
            script = 'demo' if firmware else 'platform'
            self.execute('include @scripts/%s.resc' % script)
            if firmware:
                self.execute('include @scripts/bridge.py')
        except BaseException:
            self.close()
            raise

    def keyword(self, name, *args):
        with self.lock:
            result = self.rpc.run_keyword(name, list(args))
            if result.get('status') != 'PASS':
                raise RuntimeError(result.get('error', str(result)))
            return result.get('return', '')

    def execute(self, command):
        return self.keyword('ExecuteCommand', command)

    def advance(self, seconds):
        with self.lock:
            self.execute('emulation RunFor "%.6f"' % seconds)
            self.seconds += seconds

    def button(self, pin, pressed):
        if pin not in range(4, 8) or type(pressed) is not bool:
            raise ValueError('Expected pin 4..7 and a boolean pressed state.')
        with self.lock:
            self.execute('sysbus.button%d %s' % (pin, 'Press' if pressed else 'Release'))
            self.buttons[pin - 4] = pressed

    def state(self):
        with self.lock:
            state = json.loads(self.execute('lab_state').strip())
            state.update(seconds=round(self.seconds, 3), buttons=list(self.buttons))
            return state

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
