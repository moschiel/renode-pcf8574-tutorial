"""Rebuild the exercise from README blocks, not from the finished model files."""
import argparse
import json
import os
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

    command_blocks = re.findall(r'<!-- tutorial-model-monitor -->\s*```text\n(.*?)```',
                                document, re.S)
    commands = [line for block in command_blocks for line in block.strip().splitlines()]
    assert len(commands) == 8, commands
    output_commands = re.search(r'<!-- tutorial-output-monitor -->\s*```text\n(.*?)```',
                                document, re.S).group(1).strip().splitlines()
    with Renode(executable, firmware=False) as renode:
        topology = renode.execute('peripherals').lower()
        assert 'cpu' not in topology and 'i2c1' not in topology, topology
        output = [renode.execute(command).strip() for command in commands]
        assert output[0] == '[255]' and output[1].lower() == 'false', output
        assert output[4] == '[239]' and output[7] == '[255]', output
        output = [renode.execute(command).strip() for command in output_commands]
        assert output[0] == '[254]' and output[1].lower() == 'true', output
        assert output[2] == '[255]' and output[3].lower() == 'false', output
    print('PASS tutorial standalone: no CPU or I2C master, button input, LED output and reset', flush=True)


def check_stm32_monitor(executable, document):
    from renode_client import Renode

    blocks = re.findall(r'<!-- tutorial-stm32-monitor -->\s*```text\n(.*?)```',
                        document, re.S)
    common = [line for block in blocks for line in block.strip().splitlines()]
    platform = 'windows' if os.name == 'nt' else 'linux'
    documented_load = re.search(
        r'<!-- tutorial-stm32-monitor-%s -->\s*```text\n(.*?)```' % platform,
        document, re.S).group(1).strip()
    # ExecuteCommand accepts the portable separator on Windows, while the
    # interactive Windows Monitor documented in the README requires a backslash.
    load = documented_load.replace('\\', '/') if os.name == 'nt' else documented_load
    assert len(common) == 2 and load, (common, documented_load)
    commands = [common[0], load, common[1]]
    with Renode(executable, firmware=False) as renode:
        # The documented mach create selects a new machine, separate from the helper's initial one.
        output = [renode.execute(command).strip() for command in commands]
        topology = output[-1]
        assert 'i2c1 (' in topology and 'pcf8574' not in topology, output
        assert '<0x40005400,' in topology, output
    print('PASS tutorial STM32 inspection: I2C1 exists without PCF8574', flush=True)


def check_i2c_monitor(executable, document):
    from renode_client import Renode

    blocks = re.findall(r'<!-- tutorial-i2c-monitor -->\s*```text\n(.*?)```',
                        document, re.S)
    commands = [line for block in blocks for line in block.strip().splitlines()]
    assert len(commands) == 2, commands
    with Renode(executable, firmware=False) as renode:
        output = [renode.execute(command).strip() for command in commands]
        assert 'i2c1' in output[0] and 'pcf8574' in output[0], output
        assert 'Address: 32' in output[0], output
        assert output[1] == '[255]', output
    print('PASS tutorial I2C: PCF8574 registered under STM32 I2C1 before firmware', flush=True)


def check_monitor_and_panel(work, executable, document):
    # Import the copied helpers so ROOT resolves to the new exercise directory.
    sys.path.insert(0, str(work / 'tools'))
    from renode_client import Renode
    from lab import Lab, ThreadingHTTPServer, handler_for

    blocks = re.findall(r'<!-- tutorial-monitor -->\s*```text\n(.*?)```',
                        document, re.S)
    commands = [line for block in blocks for line in block.strip().splitlines()]
    assert len(commands) == 8, commands
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
            page = request('/')
            assert b'<html lang="en">' in page and b'Renode Lab' in page
            assert b'Pause' in page and b'Advance 1 s' in page and b'Button ' in page
            assert request()['leds'] == [False] * 4
            state = request('/api/control', {'action': 'step'})
            assert state['leds'] == [True, False, False, False], state
            for pin in (4, 7):
                request('/api/control', {'action': 'button', 'pin': pin, 'pressed': True})
            state = request('/api/control', {'action': 'step'})
            assert 'INPUT P7..P4=0x6' in state['uart'], state
            for pin in (4, 7):
                request('/api/control', {'action': 'button', 'pin': pin, 'pressed': False})
            state = request('/api/control', {'action': 'step'})
            assert state['uart'].count('INPUT P7..P4=0xF') >= 2, state
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
    portuguese = (ROOT / 'README.pt-BR.md').read_text(encoding='utf-8')
    tagged_block_pattern = r'(<!-- tutorial-[^>]+ -->)\s*```([^\n]*)\n(.*?)```'
    assert re.findall(tagged_block_pattern, document, re.S) == re.findall(
        tagged_block_pattern, portuguese, re.S
    ), 'Executable tutorial blocks differ between README.md and README.pt-BR.md'
    blocks = re.findall(r'<!-- tutorial-file: ([^ ]+) -->\s*```[^\n]*\n(.*?)```',
                        document, re.S)
    assert len(blocks) == len(AUTHORED) and {name for name, _ in blocks} == AUTHORED
    updates = re.findall(r'<!-- tutorial-update: ([^ ]+) -->\s*```[^\n]*\n(.*?)```',
                         document, re.S)
    assert len(updates) == 2 and {name for name, _ in updates} == {
        'scripts/platform.resc', 'platforms/stm32.repl',
    }
    registration = re.search(r'<!-- tutorial-registration: platforms/pcf8574.repl -->\s*```text\n(.*?)```',
                             document, re.S).group(1).strip()
    assert registration == 'pcf8574: Tutorial.PCF8574 @ i2c1 0x20'
    initial = dict(blocks)
    updated = dict(updates)
    with tempfile.TemporaryDirectory(prefix='pcf-readme-') as temp:
        work = Path(temp)
        for name in SUPPLIED:
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        for name, code in blocks:
            if name in ('platforms/stm32.repl', 'scripts/demo.resc'):
                continue
            destination = work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(code, encoding='utf-8')
        renode_arg = ['--renode', args.renode] if args.renode else []
        print('Testing an empty directory rebuilt from README + six declared assets', flush=True)
        check_model_monitor(work, args.renode, document)
        # Section 4 adds the STM32 and moves the PCF8574 registration to I2C1.
        (work / 'platforms/stm32.repl').write_text(initial['platforms/stm32.repl'], encoding='utf-8')
        check_stm32_monitor(args.renode, document)
        pcf = initial['platforms/pcf8574.repl']
        assert pcf.splitlines()[0] == 'pcf8574: Tutorial.PCF8574 @ sysbus'
        pcf = registration + '\n' + pcf.split('\n', 1)[1]
        (work / 'platforms/pcf8574.repl').write_text(pcf, encoding='utf-8')
        (work / 'scripts/platform.resc').write_text(updated['scripts/platform.resc'], encoding='utf-8')
        check_i2c_monitor(args.renode, document)
        subprocess.run([sys.executable, '-u', 'tests/check.py', 'model', *renode_arg],
                       cwd=work, check=True)
        # Section 5 aligns SysTick with the firmware before loading the ELF.
        (work / 'platforms/stm32.repl').write_text(updated['platforms/stm32.repl'], encoding='utf-8')
        (work / 'scripts/demo.resc').write_text(initial['scripts/demo.resc'], encoding='utf-8')
        subprocess.run([sys.executable, '-u', 'tests/check.py', 'firmware', *renode_arg],
                       cwd=work, check=True)
        check_monitor_and_panel(work, args.renode, document)
    print('PASS tutorial: isolated reproduction completed', flush=True)


if __name__ == '__main__':
    main()
