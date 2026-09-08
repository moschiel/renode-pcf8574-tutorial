# Executed by Renode's embedded IronPython, not by the host Python 3 process.
import json

uart_lines = []
uart_partial = []


def on_uart_byte(value):
    if value == 10:
        uart_lines.append(''.join(uart_partial))
        del uart_partial[:]
        if len(uart_lines) > 80:
            del uart_lines[0]
    elif value != 13:
        uart_partial.append(chr(value))


uart = monitor.Machine['sysbus.usart2']
uart.CharReceived += on_uart_byte


def mc_lab_state():
    machine = monitor.Machine
    print(json.dumps({
        'leds': [bool(machine['sysbus.led' + str(i)].State) for i in range(4)],
        'port': int(machine['sysbus.i2c1.pcf8574'].Read(1)[0]),
        'uart': list(uart_lines)
    }))
