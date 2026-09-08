"""Rebuild the exercise from README blocks, not from the finished model files."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
SUPPLIED = (
    'firmware/demo.elf', 'tools/renode_client.py', 'tools/lab.py',
    'tests/check.py', 'scripts/bridge.py', 'web/index.html',
)
AUTHORED = {
    'models/PCF8574.cs', 'platforms/stm32.repl',
    'platforms/pcf8574.repl', 'scripts/platform.resc', 'scripts/demo.resc',
}


def check_model_monitor(work, executable, document):
    sys.path.insert(0, str(work / 'tools'))
    from renode_client import Renode

    commands = re.search(r'<!-- tutorial-model-monitor -->\s*```text\n(.*?)```',
                         document, re.S).group(1).strip().splitlines()
    with Renode(executable, firmware=False) as renode:
        output = [renode.execute(command).strip() for command in commands]
        assert output[1] == '[255]' and output[2] == '[254]', output
        assert output[3].lower() == 'true', output
        assert output[6] == '[238]' and output[9] == '[254]', output
    print('PASS tutorial model Monitor: reset, write, LED and button wire', flush=True)


def check_monitor_and_panel(work, executable, document):
    # Import the copied helpers so ROOT resolves to the new exercise directory.
    sys.path.insert(0, str(work / 'tools'))
    from renode_client import Renode
    from lab import Lab, ThreadingHTTPServer, handler_for

    commands = re.search(r'<!-- tutorial-monitor -->\s*```text\n(.*?)```',
                         document, re.S).group(1).strip().splitlines()
    with Renode(executable) as renode:
        output = [renode.execute(command).strip() for command in commands]
        assert output[1].lower() == 'true', output
        assert output[4] == '[238]' and output[7] == '[254]', output
    print('PASS tutorial Monitor: exact documented commands and results', flush=True)

    with Renode(executable) as renode:
        lab = Lab(renode)
        lab.running = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(lab))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:%d' % server.server_port

        def request(path='/api/state', data=None):
            body = None if data is None else json.dumps(data).encode()
            with urlopen(Request(base + path, data=body,
                                 headers={'Content-Type': 'application/json'}), timeout=30) as response:
                payload = response.read()
                return payload if path == '/' else json.loads(payload)

        try:
            assert b'<html' in request('/')
            assert request()['leds'] == [False] * 4
            state = request('/api/control', {'action': 'step'})
            assert state['leds'] == [True, False, False, False], state
            for pin in (4, 7):
                request('/api/control', {'action': 'button', 'pin': pin, 'pressed': True})
            state = request('/api/control', {'action': 'step'})
            assert state['uart'][-1] == 'INPUT P7..P4=0x6', state
            for pin in (4, 7):
                request('/api/control', {'action': 'button', 'pin': pin, 'pressed': False})
            state = request('/api/control', {'action': 'step'})
            assert state['uart'][-1] == 'INPUT P7..P4=0xF', state
            assert state['leds'] == [True, True, True, False], state
            try:
                request('/api/control', {'action': 'button', 'pin': 9, 'pressed': True})
            except HTTPError as exc:
                assert exc.code == 400, exc
            else:
                raise AssertionError('Invalid pin was accepted')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print('PASS tutorial panel API: HTML, stepping, buttons, LEDs and UART', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renode')
    args = parser.parse_args()
    document = (ROOT / 'README.md').read_text(encoding='utf-8')
    blocks = re.findall(r'<!-- tutorial-file: ([^ ]+) -->\s*```[^\n]*\n(.*?)```',
                        document, re.S)
    assert len(blocks) == len(AUTHORED) and {name for name, _ in blocks} == AUTHORED
    updates = re.findall(r'<!-- tutorial-update: ([^ ]+) -->\s*```[^\n]*\n(.*?)```',
                         document, re.S)
    assert len(updates) == 1 and updates[0][0] == 'platforms/stm32.repl'
    with tempfile.TemporaryDirectory(prefix='pcf-readme-') as temp:
        work = Path(temp)
        for name in SUPPLIED:
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        for name, code in blocks:
            if name == 'scripts/demo.resc':
                continue
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(code, encoding='utf-8')
        renode_arg = ['--renode', args.renode] if args.renode else []
        print('Testing an empty directory rebuilt from README + six declared assets', flush=True)
        check_model_monitor(work, args.renode, document)
        subprocess.run([sys.executable, '-u', 'tests/check.py', 'model', *renode_arg],
                       cwd=work, check=True)
        # Apply the firmware section's override only after the wire checks.
        for name, code in updates:
            (work / name).write_text(code, encoding='utf-8')
        (work / 'scripts/demo.resc').write_text(dict(blocks)['scripts/demo.resc'], encoding='utf-8')
        subprocess.run([sys.executable, '-u', 'tests/check.py', 'firmware', *renode_arg],
                       cwd=work, check=True)
        check_monitor_and_panel(work, args.renode, document)
    print('PASS tutorial: isolated reproduction completed', flush=True)


if __name__ == '__main__':
    main()
