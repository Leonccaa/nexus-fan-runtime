#!/usr/bin/env python3
"""Execute the pinned original x86 curve with synthetic inputs; no hardware."""
import argparse
import hashlib
import io
import json
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EIP, UC_X86_REG_ESP, UC_X86_REG_EAX

from nxos_patch import EXPECTED_SHA256

GLOBAL = 0x1594260
ZONES, OUT, STACK, STOP = 0x3100000, 0x3101000, 0x31ff000, 0x3000000
SENSORS = [(101, 95, 42), (60, 53, 25), (54, 46, 25), (57, 50, 26)]


class Curve:
    def __init__(self, blob, low):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(0, 0x1800000)
        self.uc.mem_map(0x3000000, 0x200000)
        for segment in ELFFile(io.BytesIO(blob)).iter_segments():
            if segment['p_type'] == 'PT_LOAD':
                self.uc.mem_write(segment['p_vaddr'], segment.data())
        self.uc.mem_write(0x2963a8, struct.pack('<I', low))
        self.uc.mem_write(GLOBAL + 0x6a4, struct.pack('<I', 0x11c))
        self.uc.mem_write(0x29dad4, struct.pack('<I', ZONES))
        self.uc.hook_add(UC_HOOK_CODE, self.hook)

    @staticmethod
    def hook(uc, address, size, unused):
        if address in (0x3d3e0, 0x3cd10):
            stack = uc.reg_read(UC_X86_REG_ESP)
            dest = struct.unpack('<I', uc.mem_read(stack, 4))[0]
            uc.reg_write(UC_X86_REG_EAX, 1 if address == 0x3d3e0 else 0)
            uc.reg_write(UC_X86_REG_ESP, stack + 4)
            uc.reg_write(UC_X86_REG_EIP, dest)
        elif 0x3ba00 <= address < 0x3d8e7:
            raise RuntimeError('Unexpected external call ' + hex(address))

    def run(self, sensors, previous, reset=True):
        uc = self.uc
        for index, (high, minor, current) in enumerate(sensors):
            uc.mem_write(GLOBAL + 0x51c + 4 * index, bytes([high, minor, current, 0]))
        uc.mem_write(ZONES, struct.pack('<4I', 0, previous, 0, 0))
        uc.mem_write(OUT, struct.pack('<I', previous))
        if reset:
            uc.mem_write(0x29dae1, b'\0')
            uc.mem_write(0x29dae2, b'\0')
        uc.mem_write(STACK, struct.pack('<6I', STOP, GLOBAL, GLOBAL + 0x514,
                                       0, max(s[2] for s in sensors), OUT))
        uc.reg_write(UC_X86_REG_ESP, STACK)
        uc.emu_start(0x13e76a, STOP, count=20000)
        if uc.reg_read(UC_X86_REG_EIP) != STOP:
            raise RuntimeError('Instruction budget exhausted')
        return struct.unpack('<I', uc.mem_read(OUT, 4))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pfm', required=True, type=Path,
                        help='Private local copy of the original firmware executable')
    parser.add_argument('--minimum', type=int, choices=(20, 30), default=20)
    args = parser.parse_args()
    blob = args.pfm.read_bytes()
    if hashlib.sha256(blob).hexdigest() != EXPECTED_SHA256:
        raise RuntimeError('Unsupported PFM image')
    minimum = args.minimum
    original, patched = Curve(blob, 50), Curve(blob, minimum)
    cases, mismatches = 0, []
    for sensor in range(4):
        for temperature in range(121):
            sensors = list(SENSORS)
            sensors[sensor] = (*sensors[sensor][:2], temperature)
            for state in (50, 70, 99):
                before = original.run(sensors, state)
                after = patched.run(sensors, minimum if state == 50 else state)
                cases += 1
                if after != (minimum if before == 50 else before):
                    mismatches.append([sensor, temperature, state, before, after])
    sequence, before, after = [], 50, minimum
    for temperature in (25, 47, 55, 25, 25, 25):
        sensors = list(SENSORS)
        sensors[2] = (54, 46, temperature)
        before = original.run(sensors, before, reset=False)
        after = patched.run(sensors, after, reset=False)
        sequence.append([temperature, before, after])
    print(json.dumps({'minimum': minimum, 'cases': cases, 'mismatches': mismatches,
                      'D2_rising_cooling_sequence': sequence,
                      'hardware_emulated': False}, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
