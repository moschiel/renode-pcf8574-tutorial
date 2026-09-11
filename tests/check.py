"""Executable checkpoints: model semantics, GPIO wiring, firmware timing and UART."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from renode_client import Renode


def check_model(executable):
    with Renode(executable, firmware=False) as r:
        r.execute('cpu IsHalted true')
        r.execute("python \"from System import Array, Byte; dev = monitor.Machine['sysbus.i2c1.pcf8574']\"")
        def check(code):
            r.execute('python "' + code + '"')
        check("assert list(dev.Read(1)) == [255], 'power-on must release every pin'")
        check("dev.Write(Array[Byte]([0xF0])); assert list(dev.Read(1)) == [0xF0]")
        check("assert all(monitor.Machine['sysbus.led' + str(i)].State for i in range(4)), 'active-low LEDs'")
        check("dev.Write(Array[Byte]([0xF0, 0xFF])); assert list(dev.Read(2)) == [255, 255], 'last byte wins'")
        r.button(4, True)
        r.advance(.001)
        check("assert list(dev.Read(1)) == [0xEF], 'button wire must pull P4 low'")
        check("dev.Reset(); assert list(dev.Read(1)) == [0xEF], 'chip reset must preserve external low'")
        r.button(4, False)
        r.advance(.001)
        check("assert list(dev.Read(1)) == [255], 'button release must restore high'")
        for pin in range(8):
            check("dev.Write(Array[Byte]([255])); dev.OnGPIO(%d, False); assert dev.Read(1)[0] == %d" % (pin, 255 ^ (1 << pin)))
            check("assert not dev.Connections[%d].IsSet" % pin)
            check("dev.Write(Array[Byte]([%d])); dev.OnGPIO(%d, True); assert dev.Read(1)[0] == %d" % (255 ^ (1 << pin), pin, 255 ^ (1 << pin)))
        # A failing assertion must propagate through the remote protocol.
        try:
            check("assert False, 'intentional test of failure propagation'")
        except RuntimeError:
            pass
        else:
            raise AssertionError('Remote assertions were silently ignored')
    print('PASS model: reset, successive bytes, eight inputs, GPIO levels and real wiring')


def check_firmware(executable):
    with Renode(executable) as r:
        r.advance(.95)
        s = r.state()
        assert s['leds'] == [False] * 4, s
        assert 'INPUT P7..P4=0xF' in s['uart'], s
        r.advance(.1)
        s = r.state()
        assert s['leds'] == [True, False, False, False], s
        assert 'LED P0=ON' in s['uart'], s
        expected = [True, False, False, False]
        for pin in [1, 2, 3, 0, 1, 2, 3]:
            r.advance(1.0)
            expected[pin] = not expected[pin]
            s = r.state()
            assert s['leds'] == expected, (pin, s)
            expected_line = 'LED P%d=%s' % (pin, 'ON' if expected[pin] else 'OFF')
            assert expected_line in s['uart'], (expected_line, s)
        for pin, nibble in [(4, 'E'), (5, 'D'), (6, 'B'), (7, '7')]:
            r.button(pin, True)
            r.advance(.21)
            assert 'INPUT P7..P4=0x' + nibble in r.state()['uart'], r.state()
            # Hold through several firmware writes: input must not become latched low.
            r.advance(2.1)
            r.button(pin, False)
            previous = r.state()['uart'].count('INPUT P7..P4=0xF')
            r.advance(.21)
            assert r.state()['uart'].count('INPUT P7..P4=0xF') > previous, r.state()
        r.button(4, True)
        r.button(7, True)
        r.advance(.21)
        assert 'INPUT P7..P4=0x6' in r.state()['uart'], r.state()
        assert not any('ERROR' in line for line in r.state()['uart'])
    # An input held during power-on is valid, not a failed chip self-test.
    with Renode(executable) as r:
        r.button(4, True)
        r.advance(.25)
        assert 'INPUT P7..P4=0xE' in r.state()['uart'], r.state()
        assert not any('ERROR' in line for line in r.state()['uart'])
    print('PASS firmware: 1 s LED sequence, 200 ms button polling, held input at boot, UART')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['model', 'firmware', 'all'], nargs='?', default='all')
    parser.add_argument('--renode')
    args = parser.parse_args()
    if args.stage in ('model', 'all'):
        check_model(args.renode)
    if args.stage in ('firmware', 'all'):
        check_firmware(args.renode)


if __name__ == '__main__':
    main()
