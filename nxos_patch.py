#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hash-pinned PFM minimum 20/30 patch for C3132Q-X / NX-OS 7.0(3)I7(9).

Python 2.7 on NX-OS; Python 3 can import this file for offline validation.
Never changes the executable, boot image, instructions, or temperature thresholds.
"""
from __future__ import print_function
import fcntl
import glob
import hashlib
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import time

EXPECTED_SHA256 = '8f98bf8242954dc64e34b86711883aaf24166c0ec1aba449ace5e2d33a9ddd88'
IMAGE = '/isan/bin/pfm'
DISABLED = '/bootflash/scripts/nexus-fan20.disabled'
LOCK = '/tmp/nexus-fan20.lock'
TEMPERATURE_CONFIG = '0402021f2c05050101010100'
SENSOR_THRESHOLDS = [(101, 95), (60, 53), (54, 46), (57, 50)]


def octets(blob):
    return list(bytearray(blob))


def cli(command):
    proc = subprocess.Popen(['/isan/bin/vsh', '-c', command],
                            stdin=open('/dev/null'), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    deadline = time.time() + 6
    while proc.poll() is None:
        if time.time() > deadline:
            proc.kill()
            proc.communicate()
            raise RuntimeError('CLI timeout')
        time.sleep(0.1)
    out, err = proc.communicate()
    if not isinstance(out, str):
        out = out.decode('utf-8', 'replace')
        err = err.decode('utf-8', 'replace')
    if proc.returncode or re.search(r'Syntax error|Invalid command|Cmd exec error', out):
        raise RuntimeError('CLI failed: ' + err + out)
    return out


def fans():
    out = cli('show environment fan detail')
    rows = re.findall(r'^(Fan[1-4]\(sys_fan[1-4]\))\s+(fan[12])\s+\S+\s+(\d+)\s+(\d+)\s*$', out, re.M)
    if len(rows) != 8:
        raise RuntimeError('Expected eight chassis rotors')
    if len(re.findall(r'^Fan[1-4]\(sys_fan[1-4]\).*\sOk\s*$', out, re.M)) != 4:
        raise RuntimeError('Chassis fan status fault')
    result = [[r[0], r[1], int(r[2]), int(r[3])] for r in rows]
    if any(r[3] < 300 for r in result):
        raise RuntimeError('Chassis rotor stalled')
    return result


class Memory(object):
    def __init__(self, writable=False):
        with open(IMAGE, 'rb') as source:
            if hashlib.sha256(source.read()).hexdigest() != EXPECTED_SHA256:
                raise RuntimeError('Unsupported PFM image; refusing memory access')
        found = []
        for path in glob.glob('/proc/[0-9]*'):
            try:
                with open(path + '/cmdline', 'rb') as source:
                    if source.read().split(b'\0')[0] == IMAGE.encode('ascii'):
                        found.append(path)
            except IOError:
                pass
        if len(found) != 1:
            raise RuntimeError('PFM not ready or process identity ambiguous')
        self.proc = found[0]
        self.start = self.start_time()
        with open(self.proc + '/maps') as source:
            maps = [line.split() for line in source
                    if line.rstrip().endswith(IMAGE) and line.split()[2] == '00000000']
        if len(maps) != 1:
            raise RuntimeError('Ambiguous PFM load address')
        self.bias = int(maps[0][0].split('-')[0], 16)
        self.fd = os.open(self.proc + '/mem', os.O_RDWR if writable else os.O_RDONLY)
        try:
            self.global_ptr = self.u32(self.bias + 0x258528)
            self.low_addr = self.bias + 0x2963a8
            self.zone_addr = self.u32(self.bias + 0x29dad4) + 4
            self.validate()
        except Exception:
            self.close()
            raise

    def close(self):
        os.close(self.fd)

    def start_time(self):
        with open(self.proc + '/stat') as source:
            return source.read().rsplit(')', 1)[1].split()[19]

    def read(self, address, length):
        os.lseek(self.fd, address, 0)
        blob = os.read(self.fd, length)
        if len(blob) != length:
            raise RuntimeError('Short process-memory read')
        return blob

    def u32(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def write(self, address, value):
        if self.start_time() != self.start:
            raise RuntimeError('PFM identity changed')
        if address not in (self.low_addr, self.zone_addr):
            raise RuntimeError('Write outside the two allowed data words')
        os.lseek(self.fd, address, 0)
        if os.write(self.fd, struct.pack('<I', value)) != 4 or self.u32(address) != value:
            raise RuntimeError('Memory write/readback failed')

    def validate(self):
        if self.u32(self.global_ptr + 0x6a4) != 0x11c:
            raise RuntimeError('Unsupported card')
        name_ptr = self.u32(self.bias + 0x2963a0)
        if self.read(name_ptr, 14).split(b'\0')[0] != b'NXA-FAN-30CFM':
            raise RuntimeError('Unsupported fan configuration')
        if struct.unpack('<3I', self.read(self.low_addr, 12)) not in ((50, 70, 99), (20, 70, 99), (30, 70, 99)):
            raise RuntimeError('Unexpected curve data')
        if self.u32(self.zone_addr) not in (20, 30, 50, 70, 99):
            raise RuntimeError('Fan zone not initialized')
        if octets(self.read(self.bias + 0x29dac8, 1)) != [1]:
            raise RuntimeError('Unexpected zone count')
        if octets(self.read(self.bias + 0x29dad8, 1)) != [0]:
            raise RuntimeError('Fixed-speed mode active')
        config = ''.join('%02x' % x for x in octets(self.read(self.bias + 0x29628c, 12)))
        if config != TEMPERATURE_CONFIG:
            raise RuntimeError('Unexpected temperature configuration')
        owner = self.u32(self.bias + 0x258580)
        ops = self.u32(owner + 0x1914)
        if self.u32(ops + 0x7c) != self.bias + 0x1aa5d8:
            raise RuntimeError('Unexpected hardware callback')
        if (self.u32(ops + 0xc4), self.u32(ops + 0xc8)) != (20, 99):
            raise RuntimeError('Unexpected driver bounds')
        self.temperatures()

    def temperatures(self):
        result = []
        for i, thresholds in enumerate(SENSOR_THRESHOLDS):
            high, minor, current, status = octets(self.read(self.global_ptr + 0x51c + 4 * i, 4))
            if (high, minor) != thresholds or status != 1 or current > 120:
                raise RuntimeError('Sensor not ready or unexpected sensor data')
            result.append(current)
        return result

    def snapshot(self, with_fans=False):
        result = {'time': time.time(), 'pfm_pid': int(self.proc.split('/')[-1]),
                  'start_time': self.start, 'levels': list(struct.unpack('<3I', self.read(self.low_addr, 12))),
                  'zone_state': self.u32(self.zone_addr), 'temperatures_C': self.temperatures(),
                  'fixed_speed': octets(self.read(self.bias + 0x29dad8, 1))[0],
                  'applied_speed': octets(self.read(self.global_ptr + 0x3b64, 1))[0]}
        if with_fans:
            result['rotors'] = fans()
        return result


def refresh():
    # Resubmit the existing default increment. This recalculates automatic speed.
    out = cli('configure terminal ; hardware fan-zone 1 raise-speed 0 ; end')
    if out.strip():
        raise RuntimeError('Unexpected refresh response: ' + out)


def disable():
    fd = os.open(DISABLED, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.write(fd, b'Disabled: run enable explicitly to reapply.\n')
    os.fsync(fd)
    os.close(fd)


def restore(memory):
    memory.write(memory.low_addr, 50)
    if memory.u32(memory.zone_addr) in (20, 30):
        # A real 70 -> 50 transition updates the old applied-speed cache too.
        memory.write(memory.zone_addr, 70)
    refresh()


def arm_rollback(memory):
    """Detached 30-second rollback if the applying parent fails or disappears."""
    read_end, write_end = os.pipe()
    ready_r, ready_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(write_end)
        os.close(ready_r)
        os.setsid()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        null = os.open('/dev/null', os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        if null > 2:
            os.close(null)
        # Separate descriptor: fork-shared file offsets would race the parent.
        memory.close()
        memory.fd = os.open(memory.proc + '/mem', os.O_RDWR)
        os.write(ready_w, b'R')
        os.close(ready_w)
        readable = select.select([read_end], [], [], 30)[0]
        complete = bool(readable) and os.read(read_end, 1) == b'C'
        if not complete:
            try:
                disable()
                restore(memory)
            except Exception:
                # The original executable still provides reboot recovery.
                pass
        os._exit(0)
    os.close(read_end)
    os.close(ready_w)
    if os.read(ready_r, 1) != b'R':
        os.close(write_end)
        raise RuntimeError('Rollback process did not arm')
    os.close(ready_r)
    return write_end, pid


def ensure(memory, minimum=20):
    if minimum not in (20, 30):
        raise RuntimeError('Minimum must be 20 or 30')
    state = memory.u32(memory.zone_addr)
    low = memory.u32(memory.low_addr)
    temperatures = memory.temperatures()
    cold = all(t <= limit for t, limit in zip(temperatures, (89, 47, 40, 44)))
    transition = state in (20, 30, 50) and state != minimum
    if transition and not cold and minimum < state:
        return 'waiting_for_cold_transition'
    if low == minimum and not transition:
        return 'already_applied'
    before = fans()  # Only apply to healthy, initialized chassis fans.
    increasing_from_20 = state == 20 and minimum == 30 and all(r[2] == 20 for r in before)
    baseline_rpm = dict(((r[0], r[1]), r[3]) for r in before)
    if cli('show running-config | include fan-zone').strip():
        raise RuntimeError('Manual fan-zone increment configured')
    ack, guard_pid = arm_rollback(memory)
    try:
        memory.write(memory.low_addr, minimum)
        if transition:
            # Do not pre-fill the target: it suppresses the change notification.
            memory.write(memory.zone_addr, 70)
        refresh()
        deadline = time.time() + 20
        while True:
            snapshot = memory.snapshot(with_fans=True)
            target = snapshot['zone_state']
            speeds_ok = all(r[2] == target for r in snapshot['rotors'])
            ceilings = {20: (3500, 2600), 30: (5000, 3700)}[minimum]
            rpm_ok = target != minimum or all(r[3] < ceilings[0 if r[1] == 'fan1' else 1]
                                              for r in snapshot['rotors'])
            if increasing_from_20 and target == minimum:
                rpm_ok = rpm_ok and all(r[3] > baseline_rpm[(r[0], r[1])] * 1.15
                                        for r in snapshot['rotors'])
            if target in (minimum, 70, 99) and speeds_ok and rpm_ok:
                break
            if time.time() > deadline:
                raise RuntimeError('Actual fan speed did not match automatic target')
            time.sleep(0.5)
        os.write(ack, b'C')
        os.close(ack)
        os.waitpid(guard_pid, 0)
        return 'applied'
    except Exception:
        disable()
        try:
            restore(memory)
        finally:
            os.close(ack)  # EOF asks the independent guard to retry rollback.
            os.waitpid(guard_pid, 0)
        raise


def elevate():
    path = os.path.abspath(__file__)
    code = ('import sys; sys.path=' + repr(sys.path) + '\nimport encodings\n'
            'sys.argv=' + repr([path] + sys.argv[1:]) + '\n'
            'exec(compile(open(' + repr(path) + ').read(), ' + repr(path) + ', "exec"))')
    return subprocess.call(['/usr/bin/sudo', '-n', '/isan/bin/nxpython', '-S', '-c', code])


def main():
    if os.geteuid() != 0:
        return elevate()
    action = sys.argv[1] if len(sys.argv) > 1 else 'ensure'
    if action not in ('check', 'sample', 'ensure', 'enable', 'restore'):
        raise RuntimeError('Use check, sample, ensure, enable, or restore')
    minimum = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    if minimum not in (20, 30) or len(sys.argv) > 3:
        raise RuntimeError('Optional minimum must be 20 or 30')
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if action == 'ensure' and os.path.exists(DISABLED):
        print(json.dumps({'result': 'disabled'}))
        os.close(lock_fd)
        return 0
    if action == 'restore':
        disable()  # Make scheduled ensure a no-op before restoring memory.
    memory = Memory(writable=action in ('ensure', 'enable', 'restore'))
    try:
        if action == 'enable' and os.path.exists(DISABLED):
            os.unlink(DISABLED)
        if action in ('ensure', 'enable'):
            result = ensure(memory, minimum)
        elif action == 'restore':
            restore(memory)
            result = 'restored_and_disabled'
        else:
            result = 'checked'
        snapshot = memory.snapshot(with_fans=action != 'check')
        snapshot['result'] = result
        print(json.dumps(snapshot))
    finally:
        memory.close()
        os.close(lock_fd)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'result': 'error', 'error': str(error)}))
        sys.exit(1)
