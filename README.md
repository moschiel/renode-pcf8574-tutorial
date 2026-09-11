# Modeling an I/O Expander over I²C (PCF8574) in Renode

**English** | [Português (Brasil)](README.pt-BR.md)

Renode can run firmware against ready-made hardware models. When a peripheral is unavailable, you can implement its behavior in C# and connect it to the simulated platform.

This tutorial creates a model of the **PCF8574, an eight-bit digital input/output expander controlled over I2C**. It extends a microcontroller's I/O through the I2C bus. The microcontroller sends commands to drive the outputs and reads the input states.

To demonstrate the use case, the **PCF8574 is connected to an STM32F407 over I2C**. The firmware toggles four LEDs on outputs P0..P3 and prints LED and button changes over UART. Finally, a web panel lets you watch the LEDs and operate the buttons connected to P4..P7 without physical hardware.

> **Scope:** this tutorial focuses on creating a basic peripheral model from the datasheet and demonstrating it with an STM32. Project features outside this scope were 100% *vibe coded* (the web GUI, automated Python/Robot Framework tests, and others).

![Diagram of the PCF8574 model connected to an STM32F407](figures/model.png)


```text
STM32F407 --I2C1--> PCF8574 --P0..P3--> LEDs
                      ^
                      |
                   P4..P7
                   Buttons

STM32 USART2 -----------------------> UART terminal
```

## 1. Prepare the project

Requirements: [Renode 1.16.1](https://renode.readthedocs.io/en/latest/introduction/installing.html), Python 3.10 or newer.

This repository already contains everything you need, but to follow this tutorial, create a sibling directory named `my-pcf8574`. The following commands copy only the firmware and the interface and test helpers; you will create the model and platform in the following steps.

**Windows / PowerShell:**

```powershell
$reference = (Get-Location).Path
New-Item -ItemType Directory ../my-pcf8574 -ErrorAction Stop
Set-Location ../my-pcf8574
New-Item -ItemType Directory models, platforms, scripts, firmware, tools, tests, web
Copy-Item "$reference/firmware/demo.elf" firmware/
Copy-Item "$reference/tools/renode_client.py" tools/
Copy-Item "$reference/tools/lab.py" tools/
Copy-Item "$reference/tests/check.py" tests/
Copy-Item "$reference/scripts/bridge.py" scripts/
Copy-Item "$reference/web/index.html" web/
python --version
```

**Linux / Bash:**

```bash
reference="$PWD"
mkdir ../my-pcf8574
cd ../my-pcf8574
mkdir models platforms scripts firmware tools tests web
cp "$reference/firmware/demo.elf" firmware/
cp "$reference/tools/renode_client.py" "$reference/tools/lab.py" tools/
cp "$reference/tests/check.py" tests/
cp "$reference/scripts/bridge.py" scripts/
cp "$reference/web/index.html" web/
python3 --version
```
Use `python3` instead of `python` in the following commands if necessary.


**Check:** `renode --version` should show the installed version, and `python --version` should show Python 3.10 or newer. The commands below expect `renode` to be available on the PATH.

All paths below are relative to the **my-pcf8574** directory. Commands labeled **Terminal** run in PowerShell or Bash; commands labeled **Monitor** run inside Renode.

## 2. Implement the PCF8574

The implementation follows this subset of the [TI PCF8574 datasheet, revision K](https://www.ti.com/lit/ds/symlink/pcf8574.pdf):

| Behavior | Implementation | Reference |
| --- | --- | --- |
| Pins initially high | The latch starts at `0xFF` | Section 7.1 |
| Writing zero forces a low level | A zero in the latch dominates the observed level | Figure 7-2 |
| Writing one releases the pin with a weak pull-up | An external signal can pull the level low | Figure 7-2 |
| Reads observe the pins | Return the effective state, not only the latch | Figure 7-4 |
| Successive-byte updates | Process every received byte | Figure 7-3 |
| A2/A1/A0 tied low | Seven-bit I2C address `0x20` | Section 7.3.3 |

There is no direction register: writing `1` allows a pin to be used as an input. In this digital model, the observed logic level is determined by `outputLatch & externalLevels`. Currents, resistances, and other electrical characteristics are not simulated.

**Successive writes:** section 7.3.1 of the datasheet says that additional bytes in the same write transaction are ignored, while Figure 7-3 shows two bytes updating the port. The `foreach` below follows the diagram; this is a modeling choice in response to that discrepancy. The firmware sends one byte per transaction. Separate transactions can still update the port normally.

Create `models/PCF8574.cs`:

<!-- tutorial-file: models/PCF8574.cs -->
```csharp
using System;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Tutorial
{
    public class PCF8574 : II2CPeripheral, IGPIOReceiver, INumberedGPIOOutput
    {
        // Last command from the I2C master.
        private byte outputLatch;
        // External levels survive chip reset: a held button stays held.
        private byte externalLevels = 0xFF;

        public IReadOnlyDictionary<int, IGPIO> Connections { get; }

        public PCF8574()
        {
            // Create the eight numbered GPIO connectors.
            var pins = new Dictionary<int, IGPIO>();
            for(var pin = 0; pin < 8; pin++)
            {
                pins.Add(pin, new GPIO());
            }
            Connections = pins;
            Reset();
        }


        // Restore the peripheral's power-on latch state.
        public void Reset()
        {
            outputLatch = 0xFF;
            UpdatePinLevels();
            this.Log(LogLevel.Debug, "Reset: output latch restored to 0xFF.");
        }

        // II2CPeripheral requires Write, Read and FinishTransmission.
        public void Write(byte[] data)
        {
            // Follow TI Rev. K Figure 7-3: each byte updates the port.
            // Section 7.3.1 conflicts with that diagram; see the README note.
            foreach(var value in data)
            {
                this.Log(LogLevel.Debug, "I2C write: output latch changed from 0x{0:X2} to 0x{1:X2}.", outputLatch, value);
                outputLatch = value;
                UpdatePinLevels();
            }
        }

        public byte[] Read(int count = 1)
        {
            var data = new byte[count];
            for(var i = 0; i < count; i++)
            {
                data[i] = EffectivePinLevels;
            }
            this.Log(LogLevel.Noisy, "I2C read: returning 0x{0:X2} ({1} byte(s) requested).", EffectivePinLevels, count);
            return data;
        }

        public void FinishTransmission()
        {
            // No register pointer or partial command to discard on STOP.
        }

        // IGPIOReceiver requires OnGPIO to receive external pin changes.
        public void OnGPIO(int number, bool value)
        {
            if(number < 0 || number >= 8)
            {
                throw new ArgumentOutOfRangeException(nameof(number));
            }
            var mask = (byte)(1 << number);
            if(value)
            {
                externalLevels |= mask;
            }
            else
            {
                externalLevels &= (byte)~mask;
            }
            UpdatePinLevels();
            this.Log(LogLevel.Debug, "GPIO input: P{0} is now {1}; effective port level is 0x{2:X2}.",
                number, value ? "high" : "low", EffectivePinLevels);
        }

        // Combine the master's command with the externally driven levels.
        // Latch 0 actively pulls the pin low: 0 & external = 0.
        // Latch 1 releases the pin: 1 & external = external.
        // An external low can override a released pin, but an external high
        // cannot override a latched low. AND models this digitally, not electrically.
        private byte EffectivePinLevels => (byte)(outputLatch & externalLevels);

        private void UpdatePinLevels()
        {
            for(var pin = 0; pin < 8; pin++)
            {
                Connections[pin].Set((EffectivePinLevels & (1 << pin)) != 0);
            }
        }

    }
}
```

`II2CPeripheral` serves the I2C bus; `IGPIOReceiver` receives external button changes; and `INumberedGPIOOutput` exposes the eight output connectors used by the LEDs and buttons in this example. These contracts separate the [peripheral behavior](https://renode.readthedocs.io/en/latest/advanced/writing-peripherals.html) from the platform connections.

The latch stores the master's command (an STM32 in this example), while `externalLevels` stores the external signal. `OnGPIO` changes one bit of that signal, and `UpdatePinLevels` publishes the result through the connectors. Resetting the chip does not release a button that remains externally pressed.

`this.Log` sends messages through Renode's logging system and identifies them with this peripheral instance. State changes use `Debug`; frequent reads use the more verbose `Noisy` level so they can remain hidden during normal execution.

**Compile check for PCF8574.cs, in the Terminal:**

Run Renode directly from the `my-pcf8574` directory.

```sh
renode --console --disable-gui --plain models/PCF8574.cs
```

`--console` opens the Monitor in the terminal, `--disable-gui` disables the graphical interface, and `--plain` simplifies the output. The final file argument is loaded at startup. You can also open Renode without that argument and run this in the **Monitor**:

```text
include @models/PCF8574.cs
```

Renode should load `PCF8574.cs` and return to the `(monitor)` prompt without compilation errors. Enter `quit` to exit. This checks compilation; the next step instantiates the device to check its behavior.


## 3. Connect the model to LEDs and buttons with .repl/.resc files

A **`.repl` file describes a Renode platform**: which models to instantiate, their parameters, and their connections. A **`.resc` file is a Renode script** that groups Monitor commands used to assemble and run a session.

At this stage, the machine contains **only the PCF8574, four LEDs, and four buttons**.

Create `platforms/pcf8574.repl`:

<!-- tutorial-file: platforms/pcf8574.repl -->
```text
pcf8574: Tutorial.PCF8574 @ sysbus
    preinit:
        include @models/PCF8574.cs
    0 -> led0@0
    1 -> led1@0
    2 -> led2@0
    3 -> led3@0

led0: Miscellaneous.LED @ sysbus
    invert: true
led1: Miscellaneous.LED @ sysbus
    invert: true
led2: Miscellaneous.LED @ sysbus
    invert: true
led3: Miscellaneous.LED @ sysbus
    invert: true

button4: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@4
button5: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@5
button6: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@6
button7: Miscellaneous.Button @ sysbus
    invert: true
    -> pcf8574@7
```

### What each declaration means

| Fragment | Meaning |
| --- | --- |
| `pcf8574:` | Name of this instance in the platform |
| `Tutorial.PCF8574` | C# model class, relative to `Antmicro.Renode.Peripherals` |
| `@ sysbus` | Registers the object in the machine without a memory address in this declaration |
| `preinit: include @models/PCF8574.cs` | Loads the code before creating the instance; `@` identifies a file path here |
| `0 -> led0@0` | Connects PCF8574 `Connections[0]` to input zero of the LED |
| `-> pcf8574@4` | Connects the button output to pin P4, received by `OnGPIO(4, value)` |

`sysbus` exists in every Renode machine. Indentation groups each device's attributes; use spaces. See [describing platforms](https://renode.readthedocs.io/en/latest/basic/describing_platforms.html).

The LEDs represent VCC, a resistor, the LED, and a PCF pin: **a low level turns an LED on**, hence `invert: true`. For buttons, this option makes a press send zero and a release send one.

Create `scripts/platform.resc`:

<!-- tutorial-file: scripts/platform.resc -->
```text
mach create "pcf8574-lab"
using sysbus
machine LoadPlatformDescription @platforms/pcf8574.repl
```

`mach create` creates the machine; `using sysbus` lets you shorten names in the Monitor; and `LoadPlatformDescription` assembles the devices and I/O connections described in the REPL.

### Inspect the model logs

Once the platform is loaded, enable `Debug` messages only for this instance in the **Monitor**:

```text
logLevel 0 sysbus.pcf8574
```

The `sysbus.pcf8574` path comes from the `pcf8574:` declaration in the REPL. The button and output checks below will now show messages when an external GPIO changes or an I2C write updates the latch. To also see every I2C read, enable the `Noisy` level:

```text
logLevel -1 sysbus.pcf8574
```

Per-peripheral logging is useful while developing a model because it exposes internal decisions without changing the firmware or adding temporary `Console.WriteLine` calls.

### Check inputs by pressing the buttons

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/platform.resc
```

After reset, the latch contains `0xFF`: all pins are released and can be used as inputs. Pressing a button should change the logic level observed by the PCF model.

The names used in the Monitor come from the REPL declarations: `pcf8574:`, `led0:`, and `button4:`. Because these objects were registered with `@ sysbus`, their paths are `sysbus.pcf8574`, `sysbus.led0`, and `sysbus.button4`. If an instance is renamed in the REPL, its commands must use the new name too.

Perform the steps below in the **Monitor** opened by the previous command, keeping the same session.

**1. Read the initial PCF8574 state**

<!-- tutorial-model-monitor -->
```text
python "dev = monitor.Machine['sysbus.pcf8574']; print(list(dev.Read(1)))"
```

`python` runs code in Renode's embedded interpreter, not in the terminal's Python. `monitor.Machine[...]` locates the instance by its registered path; `dev` is only a variable reused by the next commands, not a name defined in the REPL.

`Read(1)` calls the model's C# method and requests one byte. `list(...)` and `print(...)` display the result in decimal. **Expected: `[255]`, equivalent to `0xFF`**, with all pins released.

**2. Check LED0**

<!-- tutorial-model-monitor -->
```text
sysbus.led0 State
```

The first term identifies the LED declared as `led0:`; `State` reads its state property. **Expected: `False`**, because the LED is active-low and P0 is high after reset.

**3. Press the button connected to P4**

<!-- tutorial-model-monitor -->
```text
sysbus.button4 Press
```

`Press` operates the model declared as `button4:`. The button name does not determine its destination: the `-> pcf8574@4` connection in the REPL connects it to P4. With `invert: true`, pressing sends a low level to the PCF8574's `OnGPIO(4, false)`.

**4. Apply the event in the simulation**

<!-- tutorial-model-monitor -->
```text
emulation RunFor "0.001"
```

This advances **1 ms of virtual time** and stops again. The advance delivers the button event even without a CPU; it is not a 1 ms wait on the host computer.

**5. Read the result**

<!-- tutorial-model-monitor -->
```text
python "print(list(dev.Read(1)))"
```

The `dev` variable still points to the same PCF8574. **Expected: `[239]`, equivalent to `0xEF` (`11101111` in binary)**: only P4 went low. The button changed the C# `externalLevels` variable; the latch remains at `0xFF`.

**6. Release the button and check recovery**

<!-- tutorial-model-monitor -->
```text
sysbus.button4 Release
emulation RunFor "0.001"
python "print(list(dev.Read(1)))"
```

`Release` releases the same button; the following advance delivers a high level to the PCF8574. **The reading should return to `[255]` (`0xFF`)** without another latch write. This confirms the button path, GPIO connection, and state observed by the model.

### Check an output

In the **same Monitor session**, write directly to the model and then reset it:

<!-- tutorial-output-monitor -->
```text
python "from System import Array, Byte; dev.Write(Array[Byte]([0xFE])); print(list(dev.Read(1)))"
sysbus.led0 State
python "dev.Reset(); print(list(dev.Read(1)))"
sysbus.led0 State
```

**Expected output:** `[254]` and `True` after the write; `[255]` and `False` after reset. A low P0 bit turns LED0 on.

These `Read` and `Write` calls test the C# model functions directly; they are not an I2C transaction. Enter `quit` to continue.

## 4. Connect to the STM32 I2C controller

Create `platforms/stm32.repl` to import the STM32F4 platform distributed with Renode:

<!-- tutorial-file: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"
```

This definition provides the CPU and the STM32's internal peripherals, including the `i2c1` controller.

### Inspect the peripherals available on the STM32

Before connecting the PCF8574, open an empty Renode session from a terminal in the tutorial root:

```sh
renode --console --disable-gui --plain
```

In the **Monitor**, create a machine for inspecting the STM32:

<!-- tutorial-stm32-monitor -->
```text
mach create "stm32-inspect"
```

Load only the STM32 definition you just created, without the PCF8574:

**Windows:**

<!-- tutorial-stm32-monitor-windows -->
```text
machine LoadPlatformDescription @platforms\stm32.repl
```

**Linux:**

<!-- tutorial-stm32-monitor-linux -->
```text
machine LoadPlatformDescription @platforms/stm32.repl
```

List the selected machine's peripherals:

<!-- tutorial-stm32-monitor -->
```text
peripherals
```

The command shows the tree of devices loaded in this machine. The STM32 peripherals should include an `i2c1` entry starting at address `0x40005400`, similar to:

```text
i2c1 (STM32F1_I2C)
    <0x40005400, 0x4000543F>
```

`i2c1` is the stable instance name of the I2C1 controller in the imported definition, so it can be used as the destination of the PCF8574 connection. The displayed range belongs to the controller registers in STM32 memory.

**Check:** `i2c1` should exist, with no `pcf8574` beneath it yet. Enter `quit` to close this inspection session before continuing.

### Register the PCF8574 with the controller

In `platforms/pcf8574.repl`, replace **only the first line**, preserving `preinit` and all LED and button connections:

<!-- tutorial-registration: platforms/pcf8574.repl -->
```text
pcf8574: Tutorial.PCF8574 @ i2c1 0x20
```

`@ i2c1` now registers the PCF8574 as a device on the **STM32 I2C1 controller**. `0x20` is the seven-bit I2C address selected for the PCF8574, corresponding to A2, A1, and A0 tied low. See section 7.3.3 of the [datasheet](https://www.ti.com/lit/ds/symlink/pcf8574.pdf).

Update `scripts/platform.resc` to load the STM32 **before** the PCF8574 because `i2c1` must already exist:

<!-- tutorial-update: scripts/platform.resc -->
```text
mach create "pcf8574-lab"
using sysbus
machine LoadPlatformDescription @platforms/stm32.repl
machine LoadPlatformDescription @platforms/pcf8574.repl
cpu PerformanceInMips 100
```

`PerformanceInMips` configures the simulated CPU execution rate; it is not the SysTick clock.

### Check the connection

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/platform.resc
```

In the **Monitor**, list the peripheral tree again:

<!-- tutorial-i2c-monitor -->
```text
peripherals
```

**Expected output (excerpt):** `pcf8574` now appears under `i2c1`, showing that it is registered with that controller:

```text
i2c1 (STM32F4_I2C)
    <0x40005400, 0x400057FF>
    pcf8574 (PCF8574)
        Address: 32
```

`Address: 32` shows the REPL's `0x20` I2C address in decimal.

Use the expander's new path in the tree to read its initial state:

<!-- tutorial-i2c-monitor -->
```text
python "dev = monitor.Machine['sysbus.i2c1.pcf8574']; print(list(dev.Read(1)))"
```

**Expected output:** `[255]`. The path changed from `sysbus.pcf8574` to `sysbus.i2c1.pcf8574` to reflect the new connection.

This checks the platform assembly. The CPU has not run any firmware yet; master-side I2C communication is tested in the next section. Enter `quit`.

## 5. Run the STM32 firmware

The supplied firmware was generated with STM32CubeMX for the **STM32F407G-DISC1** board, **STM32F407VGTx** MCU, using HAL. The complete project is in [firmware](firmware), including the [CubeMX file](firmware/pcf8574-demo.ioc) and [main.c](firmware/Core/Src/main.c).

The application initializes the port to `0xFF`, keeps P4..P7 released for input, polls the buttons every 200 ms, and toggles one LED every second while cycling through P0..P3. The sequence turns on P0, P1, P2, P3, then turns off P0, P1, P2, P3. LED and input changes are printed through USART2.

With the model logging introduced in section 3, `Debug` shows the latch write once per second. The optional `Noisy` level also shows the five I2C reads performed per second while polling the buttons.

The excerpts below are already part of the supplied [main.c](firmware/Core/Src/main.c); you do not need to add them to run the tutorial ELF.

### Use the pins as inputs and outputs

As implemented in [section 2](#2-implement-the-pcf8574), the PCF8574 has **quasi-bidirectional** pins and no direction register. There is no separate command for configuring `input` or `output`:

| Bit written to the port | Effect on the pin | Use in this firmware |
| --- | --- | --- |
| `0` | Forces a low level | Turn on an active-low LED on P0..P3 |
| `1` | Releases the pin with a weak pull-up; an external signal can pull it low | Turn off an LED or allow a button to be read on P4..P7 |

Writing `1` therefore does not select an exclusive input mode or produce a strong high output. The pin's function also depends on the connected circuit. This is the behavior in Figure 7-2 of the [datasheet](https://www.ti.com/lit/ds/symlink/pcf8574.pdf), represented in the model by `outputLatch & externalLevels`.

The firmware constants define the expander address and which bits must remain released:

<!-- tutorial-firmware-excerpt -->
```c
#define PCF_ADDRESS (0x20U << 1)
#define INPUT_MASK 0xF0U
#define BUTTON_POLL_INTERVAL_MS 200U
#define LED_TOGGLE_INTERVAL_MS 1000U
```

`INPUT_MASK` is `11110000` in binary: it keeps P4..P7 at `1` on every write. P0..P3 receive the desired LED levels. The other constants make the two independent periods explicit. HAL takes the seven-bit address shifted left by one position (`0x20 << 1`); in the REPL, the address remains `0x20`.

### Initialize and access the expander over I2C

The `write_port` function sends one byte through the STM32 I2C1 controller:

<!-- tutorial-firmware-excerpt -->
```c
static void write_port(uint8_t value)
{
    if(HAL_I2C_Master_Transmit(&hi2c1, PCF_ADDRESS, &value, 1, 100U) != HAL_OK)
    {
        fail("ERROR: PCF8574 write\r\n");
    }
}
```

`1` is the byte count, and `100U` is the timeout in milliseconds. No register address is sent: the byte represents all eight pins. In the simulation, the transaction passes through the I2C1 model and reaches the PCF8574's `Write`, which updates `outputLatch` and the LED connectors.

After initializing I2C1 and USART2, `main` calls this validation:

<!-- tutorial-firmware-excerpt -->
```c
static void validate_pcf8574(void)
{
    // All LEDs off (active-low). P4..P7 remain released for button input.
    write_port(0xFFU);
    uint8_t observed = read_port();
    // A held button is valid at boot, so do not compare the input bits to 1.
    if((observed & 0x0FU) != 0x0FU) { fail("ERROR: output readback\r\n"); }
    print_line("PCF8574 ready: P0..P3 LEDs; P4..P7 buttons\r\n");
}
```

`0xFF` releases every pin: all four LEDs start off, and the buttons can change the input levels. The check uses `0x0F` to inspect only P0..P3 because a button held during initialization may legitimately make a P4..P7 bit return zero. `read_port` uses `HAL_I2C_Master_Receive` to receive one byte, ultimately reaching the model's `Read` method.

### Toggle one LED every second

Before the `while` loop, the firmware prepares the LED state and time reference:

<!-- tutorial-firmware-excerpt -->
```c
uint8_t ledLevels = 0x0FU;
uint8_t nextLed = 0U;
uint8_t previousInputs = 0xFFU;  // Sentinel: also print the first sample.
uint32_t lastButtonPoll = HAL_GetTick();
uint32_t lastLedToggle = HAL_GetTick();
```

Inside the loop, this block toggles one pin per interval:

<!-- tutorial-firmware-excerpt -->
```c
if((uint32_t)(now - lastLedToggle) >= LED_TOGGLE_INTERVAL_MS)
{
    lastLedToggle += LED_TOGGLE_INTERVAL_MS;
    // Toggle ONE pin per step: P0, P1, P2, P3, then repeat.
    uint8_t toggledLed = nextLed;
    ledLevels ^= (uint8_t)(1U << toggledLed);
    nextLed = (uint8_t)((nextLed + 1U) % 4U);
    // Never copy observed button lows back into the output latch.
    write_port((uint8_t)(INPUT_MASK | ledLevels));

    char message[32];
    snprintf(message, sizeof(message), "LED P%u=%s\r\n", (unsigned)toggledLed,
        (ledLevels & (1U << toggledLed)) == 0U ? "ON" : "OFF");
    print_line(message);
}
```

XOR (`^=`) flips only the selected LED bit; modulo `% 4` cycles through P0, P1, P2, and P3 repeatedly. Written bytes start at `0xFF`, followed by `0xFE`, `0xFC`, `0xF8`, and `0xF0`: one additional LED turns on at each step. Then `0xF1`, `0xF3`, `0xF7`, and `0xFF` turn them off one at a time. Because this happens only once per second, the firmware also prints concise messages such as `LED P0=ON` without flooding the UART.

OR with `INPUT_MASK` keeps P4..P7 released because these pins are used as button inputs. Therefore, the firmware should not drive them to different logic levels.

### Poll the buttons every 200 ms

Also inside the `while` loop, the read separates the four input bits:

<!-- tutorial-firmware-excerpt -->
```c
uint32_t now = HAL_GetTick();
if((uint32_t)(now - lastButtonPoll) >= BUTTON_POLL_INTERVAL_MS)
{
    lastButtonPoll += BUTTON_POLL_INTERVAL_MS;
    uint8_t inputs = (uint8_t)((read_port() >> 4) & 0x0FU);
    if(inputs != previousInputs)
    {
        char message[48];
        snprintf(message, sizeof(message), "INPUT P7..P4=0x%X\r\n", (unsigned)inputs);
        print_line(message);
        previousInputs = inputs;
    }
}
```

`HAL_GetTick` lets the loop perform an I2C read every 200 ms independently of the LED timer. The `>> 4` shift moves P4..P7 into the lower four bits; the `0x0F` mask keeps only that group. `print_line` transmits through USART2, and the comparison avoids repeating messages while the inputs remain unchanged. The initial `previousInputs = 0xFF` value ensures that the first sample is printed. The loop still ends with `HAL_Delay(5U)` so it can service both timers, but it does not access I2C on every iteration.

This completes the path introduced in section 2: pressing a button calls `OnGPIO`, which changes `externalLevels`; because the corresponding `outputLatch` bit remains `1`, `Read` returns the external level. A released button reads as `1`; a pressed button reads as `0`.

**What to check when running the steps below:** with no buttons pressed, UART should show `INPUT P7..P4=0xF`. Pressing only P4 should show `0xE`; releasing it should show `0xF` again. The LEDs should continue toggling independently of these changes.

### Override the SysTick configuration

The firmware configures SYSCLK and HCLK at **168 MHz**, while the Renode 1.16.1 `stm32f4.repl` platform defines `systickFrequency` as **72 MHz**. This parameter must be aligned so that firmware-calculated intervals have the expected duration in the simulation.

Replace the contents of `platforms/stm32.repl` with:

<!-- tutorial-update: platforms/stm32.repl -->
```text
using "platforms/cpus/stm32f4.repl"

nvic:
    systickFrequency: 168000000
```

`using` imports the original definition; the `nvic:` block overrides only the specified attribute of the device already declared there.

This adjustment is applied while creating the platform. Close the previous session and load a new one after changing the file. Section 3 had no CPU, and in section 4 the CPU was not running firmware yet; those tests did not depend on this frequency.

### Load the binary

Create `scripts/demo.resc`:

<!-- tutorial-file: scripts/demo.resc -->
```text
include @scripts/platform.resc
sysbus LoadELF @firmware/demo.elf
showAnalyzer sysbus.usart2
```

The script loads the platform with `include`, loads the **supplied precompiled firmware** from `firmware/demo.elf` with `LoadELF`, and opens the USART2 output with `showAnalyzer`. You do not need to compile the firmware to run this demo.

### Check the firmware

**Terminal:**

```sh
renode --console --disable-gui --plain scripts/demo.resc
```

In an empty Monitor, the equivalent command is `include @scripts/demo.resc`.

Use a new **Monitor** session and do not run `start` first. Run each block below separately.

Advance 1.05 s of virtual time to initialize the firmware and reach the first LED toggle:

<!-- tutorial-monitor -->
```text
emulation RunFor "1.050"
```

With all buttons released, check UART for `PCF8574 ready`, `INPUT P7..P4=0xF`, and `LED P0=ON`. Then read the LED connected to P0:

<!-- tutorial-monitor -->
```text
sysbus.led0 State
```

**Expected:** `True` (LED on).

Press the button connected to P4; `button4` is the name defined in the REPL:

<!-- tutorial-monitor -->
```text
sysbus.button4 Press
```

Advance time to deliver the signal and let the firmware read it:

<!-- tutorial-monitor -->
```text
emulation RunFor "0.210"
```

**Expected on UART:** `INPUT P7..P4=0xE`. Also read the model's complete byte:

<!-- tutorial-monitor -->
```text
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
```

**Expected:** `[238]` (`0xEE`): P4 is low and P0 remains low, keeping LED0 on. This query calls `Read` directly; the UART message above came from the firmware's I2C read.

Release the same button:

<!-- tutorial-monitor -->
```text
sysbus.button4 Release
```

Advance another 210 ms so the next button poll observes the release:

<!-- tutorial-monitor -->
```text
emulation RunFor "0.210"
```

**Expected on UART:** `INPUT P7..P4=0xF`. Read the value again:

<!-- tutorial-monitor -->
```text
python "print(list(monitor.Machine['sysbus.i2c1.pcf8574'].Read(1)))"
```

**Expected:** `[254]` (`0xFE`): P4 returned high; LED0 remains on. Accumulated time is 1.47 s, still before the second LED toggle.

`RunFor` executes the requested virtual-time interval and stops. Use `start` for continuous execution and `pause` to interrupt it. Exit with `quit`.

### Rebuild with STM32CubeIDE (optional)

To **edit the demo firmware**, import the original repository's `firmware` directory through `File > Import > STM32CubeMX/STM32CubeIDE Project`. Edit `Core/Src/main.c`, for example, and build `pcf8574-demo` in `Debug`.

Then edit the `sysbus LoadELF` line in **`scripts/demo.resc`** to point to the new `.elf` file. Example path relative to the `my-pcf8574` directory:

```text
sysbus LoadELF @../renode-pcf8574-tutorial/firmware/Debug/pcf8574-demo.elf
```

Adjust the path if the project is in a different directory. Alternatively, leave `demo.resc` unchanged and replace the demo ELF by running this in `my-pcf8574`:

```powershell
Copy-Item "$reference/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf
```

On Bash: `cp "$reference/firmware/Debug/pcf8574-demo.elf" firmware/demo.elf`. If you reopened the terminal, redefine `reference` with the original repository path. Restart Renode and repeat the previous check.

**IDE-free alternative:** with Arm GNU Toolchain on the PATH, run this from the original repository root:

```sh
python tools/build_firmware.py
```

The optional script generates `firmware/demo.elf` in that repository; use `--gcc "path/to/arm-none-eabi-gcc"` to select the compiler. Point `LoadELF` to that file or copy it to the exercise's `firmware/demo.elf`. Restart Renode after rebuilding.

## 6. Web panel (vibe-coded)

Close the Monitor and run this in the **Terminal**:

```sh
python tools/lab.py
```

The panel opens at [localhost:8000](http://127.0.0.1:8000). Click a button once to press it and again to release it. The LEDs show the Renode model states; the terminal shows the characters sent by the firmware through USART2.

```text
Browser --HTTP--> Python 3 --XML-RPC--> Renode
```

The panel is specific to this example. It uses the [Renode remote test server](https://renode.readthedocs.io/en/latest/introduction/testing.html), which is compatible with Robot Framework without requiring it to be installed. `scripts/bridge.py` runs in Renode's embedded Python to inspect the LEDs and capture UART.

**Suggested check:**

| Action | Expected result |
| --- | --- |
| Press P4 | UART: `INPUT P7..P4=0xE` |
| Hold P4 and press P7 | UART: `INPUT P7..P4=0x6` |
| Release both | UART ends with `INPUT P7..P4=0xF` |
| Pause and advance 1 s | One LED changes state and its new state appears on UART |

The effect of a button operated while paused appears on the next step. Stop the panel with `Ctrl+C` in the terminal.

## Automated tests (vibe-coded)

From the project directory:

```sh
python tests/check.py all
```

Expect the lines `PASS model:` and `PASS firmware:`. The first test checks the isolated model; the second runs the firmware and checks LEDs, buttons, and UART.

## Limitations and troubleshooting

The model does not implement `INT`, currents, short circuits, electrical I2C timing, or resolution of multiple drivers on the same pin. It is a functional digital simulation, not a complete electrical equivalent of the IC.

| Problem | Fix |
| --- | --- |
| Renode not found | Add the installation to PATH. In PowerShell, an alternative is `& "C:/Program Files/Renode/renode.exe"`; for the Python helpers, use `--renode "path/to/executable"` |
| File not found | Run from the `my-pcf8574` directory and check names and extensions |
| `flash_controller`, `rcc`, or `SYSCFG` warnings during boot | These occur on this platform during HAL initialization; check the reads, LED, and UART described in step 5 |
| Port 8000 already in use | Use `python tools/lab.py --port 8001` |
| Model or ELF change has no effect | Close the session and open a new one |
| Button has no effect | Advance virtual time and check that P4..P7 are released |
| Incorrect LED timing | Check the 168 MHz SysTick setting |

Validated on Windows and Linux with Renode 1.16.1. On Linux, use `python3` in Terminal commands if the `python` executable is unavailable.
