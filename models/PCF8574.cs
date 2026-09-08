using System;
using System.Collections.Generic;
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Tutorial
{
    // Digital functional subset of TI PCF8574. No INT or analog drive simulation.
    public class PCF8574 : II2CPeripheral, IGPIOReceiver, INumberedGPIOOutput
    {
        public PCF8574()
        {
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

        public void Reset()
        {
            outputLatch = 0xFF;
            UpdatePinLevels();
        }

        public void Write(byte[] data)
        {
            // Each data byte updates the port: TI datasheet, Write Mode diagram.
            foreach(var value in data)
            {
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
            return data;
        }

        public void FinishTransmission()
        {
            // There is no register pointer or partial command to discard on STOP.
        }

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
        }

        // Latch 0 forces low; latch 1 lets the external signal determine the level.
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
