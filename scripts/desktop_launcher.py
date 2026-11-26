#!/usr/bin/python3
"""Launch the desktop app, with optional authorization for interactive starts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent


def launch_command(start_hidden=False):
    command = ['/usr/bin/python3', '-m', 'asus_power']
    if start_hidden:
        command.append('--start-hidden')
    return command


def authorization_command():
    pkexec = shutil.which('pkexec')
    if not pkexec:
        return None
    home = Path.home()
    variables = {
        'DISPLAY': os.environ.get('DISPLAY', ''),
        'XAUTHORITY': os.environ.get('XAUTHORITY', str(home / '.Xauthority')),
        'XDG_RUNTIME_DIR': os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'),
        'DBUS_SESSION_BUS_ADDRESS': os.environ.get(
            'DBUS_SESSION_BUS_ADDRESS', f'unix:path=/run/user/{os.getuid()}/bus'),
        'XDG_CONFIG_HOME': os.environ.get('XDG_CONFIG_HOME') or str(home / '.config'),
    }
    return [pkexec, '--disable-internal-agent', '/usr/bin/env',
            *(f'{key}={value}' for key, value in variables.items()),
            '/usr/bin/python3', '-I', str(Path(__file__).resolve()), '--authorized']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-hidden', action='store_true', help='启动到托盘')
    parser.add_argument('--check', action='store_true', help='只显示启动命令')
    parser.add_argument('--authorized', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.authorized and (os.geteuid() != 0 or not os.environ.get('PKEXEC_UID')):
        parser.error('需要先完成管理员授权')
    sys.path.insert(0, str(PROJECT))
    from asus_power.config import load

    command = launch_command(args.start_hidden)
    authorized = None
    if not args.start_hidden and not args.authorized and os.geteuid() != 0:
        if load().get('request_admin_at_launch') is True:
            authorized = authorization_command()
    if args.check:
        print(json.dumps({'project': str(PROJECT), 'command': authorized or command}, ensure_ascii=False, indent=2))
        return 0
    os.chdir(PROJECT)
    if authorized:
        try:
            if subprocess.run(authorized, check=False).returncode == 0:
                return 0
        except OSError:
            pass  # Continue with a normal launch if authorization cannot start.
    os.execv(command[0], command)


if __name__ == '__main__':
    sys.exit(main())
