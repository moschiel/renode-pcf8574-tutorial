using System;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Tutorial
{
    // Digital functional subset of TI PCF8574. No INT or analog drive simulation.
    public class PCF8574 : II2CPeripheral, IGPIOReceiver, INumberedGPIOOutput
    {
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

        // Renode uses these same GPIO objects for numbered outgoing REPL wires.
        public IReadOnlyDictionary<int, IGPIO> Connections { get; }

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
            // There is no register pointer or partial command to discard on STOP.
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

        // The master's last command, distinct from what a read observes.
        private byte outputLatch;
        // Released/unconnected inputs default high. Device reset does not release
        // a button held outside the chip; only OnGPIO changes external levels.
        private byte externalLevels = 0xFF;
    }
}
