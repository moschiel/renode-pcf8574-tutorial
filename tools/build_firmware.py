"""Optional firmware rebuild. Requires Arm GNU Toolchain, not an IDE or make."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gcc', default='arm-none-eabi-gcc', help='Executable name or full path')
    args = parser.parse_args()
    if not shutil.which(args.gcc):
        parser.error('Arm GCC not found. Use --gcc with the executable path.')
    fw = ROOT / 'firmware'
    build = fw / 'build'
    build.mkdir(exist_ok=True)
    vendor = fw / 'Drivers'
    hal = vendor / 'STM32F4xx_HAL_Driver'
    sources = [*sorted((fw / 'Core/Src').glob('*.c')),
               *sorted((fw / 'Core/Startup').glob('*.s')), *sorted((hal / 'Src').glob('*.c'))]
    includes = [fw / 'Core/Inc', hal / 'Inc', hal / 'Inc/Legacy',
                vendor / 'CMSIS/Include', vendor / 'CMSIS/Device/ST/STM32F4xx/Include']
    flags = ['-mcpu=cortex-m4', '-mthumb', '-mfpu=fpv4-sp-d16', '-mfloat-abi=hard',
             '-DSTM32F407xx', '-DUSE_HAL_DRIVER', '-Os', '-g3', '-ffunction-sections',
             '-fdata-sections', '-Wall', '-Wextra', '--specs=nano.specs']
    objects = []
    for source in sources:
        obj = build / (source.stem + '.o')
        subprocess.run([args.gcc, *flags, *['-I' + str(p) for p in includes],
                        '-c', str(source), '-o', str(obj)], check=True)
        objects.append(str(obj))
    subprocess.run([args.gcc, *flags, *objects, '-T' + str(fw / 'STM32F407VGTX_FLASH.ld'),
                    '--specs=nosys.specs', '-Wl,--gc-sections',
                    '-Wl,-Map=' + str(build / 'demo.map'), '-o', str(fw / 'demo.elf')], check=True)
    print('Built firmware/demo.elf. Restart the lab to load the new binary.')


if __name__ == '__main__':
    main()
