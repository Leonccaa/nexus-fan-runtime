#!/usr/bin/env python3
"""Host-side deployment and sampling via an existing NX-OS admin SSH alias."""
import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

REMOTE = '/bootflash/scripts/nexus-fan20.py'
JOB = 'nexus-fan20'


def ssh(host, command, payload=None, timeout=40):
    argv = ['ssh', '-tt' if payload else '-T', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=6', host, command]
    result = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace') + result.stdout.decode(errors='replace'))
    return result.stdout.decode(errors='replace')


def root_rpc(host, code):
    outer = ('import subprocess,json,sys\n'
             "rc='import sys; sys.path='+repr(sys.path)+chr(10)+'import encodings'+chr(10)+" + repr(code) + '\n'
             "p=subprocess.Popen(['/usr/bin/sudo','-n','/isan/bin/nxpython','-S','-c',rc],stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
             "a,b=p.communicate();print('RPC_BEGIN'+json.dumps({'exit':p.returncode,'out':a,'err':b})+'RPC_END')")
    payload = ('exec(' + repr(outer) + ')\nexit()\nexit\n').encode()
    output = ssh(host, 'python', payload)
    matches = re.findall(r'RPC_BEGIN(\{.*?\})RPC_END', output, re.S)
    if not matches:
        raise RuntimeError('Missing structured RPC response')
    result = json.loads(matches[-1])
    if result['exit']:
        raise RuntimeError(result)
    return result['out']


def action(host, name, minimum=None):
    argv = [REMOTE, name] + ([str(minimum)] if minimum is not None else [])
    code = ('import sys\nsys.argv=' + repr(argv) + '\n'
            'exec(compile(open(' + repr(REMOTE) + ').read(), ' + repr(REMOTE) + ', "exec"))')
    return json.loads(root_rpc(host, code))


def deploy(host, backup_dir, check_only=False):
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    for command, filename in [('show running-config', 'running-before.txt'),
                              ('show startup-config', 'startup-before.txt'),
                              ('show environment fan detail ; show environment temperature ; show interface brief', 'environment-before.txt')]:
        path = backup_dir / filename
        if path.exists():
            raise RuntimeError('Use a fresh backup directory')
        path.write_text(ssh(host, command))
        path.chmod(0o600)
    blob = Path(__file__).with_name('nxos_patch.py').read_bytes()
    encoded = base64.b64encode(blob).decode('ascii')
    code = ('import os,base64,json\n'
            'path=' + repr(REMOTE) + '\n'
            "if not os.path.isdir('/bootflash/scripts'): os.mkdir('/bootflash/scripts',0o755)\n"
            "if os.path.exists(path): raise RuntimeError('Script already exists; explicit update required')\n"
            'f=open(path,"wb");f.write(base64.b64decode(' + repr(encoded) + '));f.flush();os.fsync(f.fileno());f.close()\n'
            'os.chmod(path,0o644)\nprint(json.dumps({"uploaded":path}))')
    print(root_rpc(host, code).strip())
    print(json.dumps(action(host, 'sample')))
    # Configure as the existing admin user, so Scheduler authenticates that account.
    mode = 'check' if check_only else 'ensure'
    print(ssh(host, 'configure terminal ; feature scheduler ; end'))
    print(ssh(host, 'configure terminal ; scheduler job name ' + JOB +
              ' ; python bootflash:scripts/nexus-fan20.py ' + mode + ' ; end'))
    # Use a separate CLI call: NX-OS job-definition parsing consumes trailing commands.
    print(ssh(host, 'configure terminal ; scheduler schedule name ' + JOB +
              ' ; job name ' + JOB + ' ; time start now repeat 0:0:1 ; end'))
    print(ssh(host, 'show scheduler config ; show scheduler schedule ; show scheduler job'))
    # Saving is a separate explicit step after scheduled execution has been verified.


def update(host, expected_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('Expected script SHA256 must be 64 lowercase hexadecimal characters')
    blob = Path(__file__).with_name('nxos_patch.py').read_bytes()
    encoded = base64.b64encode(blob).decode('ascii')
    code = ('import os,hashlib,base64,json\npath=' + repr(REMOTE) + '\n'
            'old=hashlib.sha256(open(path,"rb").read()).hexdigest()\n'
            'if old!=' + repr(expected_sha256) + ': raise RuntimeError("Deployed script hash changed")\n'
            'blob=base64.b64decode(' + repr(encoded) + ')\n'
            'compile(blob,path,"exec")\n'  # Device Python 2 syntax validation before replacement.
            'temp=path+".new"\n'
            'fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)\n'
            'f=os.fdopen(fd,"wb");f.write(blob);f.flush();os.fsync(f.fileno());f.close()\n'
            'os.chmod(temp,0o644);os.rename(temp,path)\n'
            'print(json.dumps({"old_script_sha256":old,"new_script_sha256":hashlib.sha256(blob).hexdigest()}))')
    print(root_rpc(host, code).strip())
    print(json.dumps(action(host, 'sample')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='SSH destination configured for your switch')
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'sample', 'ensure', 'enable', 'restore'):
        command = subs.add_parser(name)
        if name in ('ensure', 'enable'):
            command.add_argument('--minimum', type=int, choices=(20, 30), default=20)
    deploy_parser = subs.add_parser('deploy')
    deploy_parser.add_argument('--backup-dir', type=Path, required=True)
    deploy_parser.add_argument('--check-only', action='store_true')
    update_parser = subs.add_parser('update')
    update_parser.add_argument('--expected-sha256', required=True)
    subs.add_parser('scheduler-status')
    activate = subs.add_parser('activate-scheduler')
    activate.add_argument('--minimum', type=int, choices=(20, 30), default=20)
    subs.add_parser('save-config')
    subs.add_parser('remove-scheduler')
    args = parser.parse_args()
    if args.command == 'deploy':
        deploy(args.host, args.backup_dir, args.check_only)
    elif args.command == 'update':
        update(args.host, args.expected_sha256)
    elif args.command == 'scheduler-status':
        print(ssh(args.host, 'show scheduler config ; show scheduler schedule ; show scheduler logfile'))
    elif args.command == 'activate-scheduler':
        print(ssh(args.host, 'configure terminal ; no scheduler job name ' + JOB + ' ; end'))
        print(ssh(args.host, 'configure terminal ; scheduler job name ' + JOB +
                  ' ; python bootflash:scripts/nexus-fan20.py ensure ' + str(args.minimum) + ' ; end'))
        print(ssh(args.host, 'configure terminal ; scheduler schedule name ' + JOB +
                  ' ; job name ' + JOB + ' ; end'))
    elif args.command == 'save-config':
        print(ssh(args.host, 'copy running-config startup-config'))
    elif args.command == 'remove-scheduler':
        commands = ('configure terminal ; no scheduler schedule name ' + JOB +
                    ' ; no scheduler job name ' + JOB + ' ; end')
        print(ssh(args.host, commands))
    else:
        print(json.dumps(action(args.host, args.command, getattr(args, 'minimum', None)), indent=2))


if __name__ == '__main__':
    main()
