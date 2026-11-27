"""读取 DMI BIOS 版本并查询 fwupd；仅检查更新，支持链接供用户核对机型。"""
from ..i18n import tr
import json
import os
from pathlib import Path
import re
import subprocess

DMI = Path('/sys/class/dmi/id')


def bios_info(root=DMI):
    def read(name):
        try:
            return (root / name).read_text().strip()
        except OSError:
            return ''
    board = read('board_name')
    vendor = read('sys_vendor')
    support = ''
    if 'ASUS' in vendor.upper() and re.fullmatch(r'[A-Za-z0-9-]+', board):
        support = f'https://www.asus.com/supportonly/{board}/helpdesk_bios/'
    return read('bios_version'), support


def check_bios_updates(runner=subprocess.run):
    def run(*arguments):
        try:
            result = runner(['fwupdmgr', *arguments, '--json'], capture_output=True, text=True,
                            timeout=45, env={**os.environ, 'LC_ALL': 'C'})
        except FileNotFoundError as error:
            raise RuntimeError(tr('firmware.fwupd_missing')) from error
        if result.returncode not in (0, 2):
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or tr('firmware.exit_code', returncode=result.returncode))
        return result.stdout

    devices = json.loads(run('get-devices') or '{}').get('Devices', [])
    system = [device for device in devices
              if device.get('Name') == 'System Firmware' and device.get('Plugin') == 'uefi_capsule'
              and device.get('DeviceId')]
    if len(system) != 1:
        return tr('firmware.ambiguous')

    run('refresh')
    device = system[0]
    updates = json.loads(run('get-updates', device['DeviceId']) or '{}').get('Devices', [])
    for update in updates:
        if update.get('DeviceId') != device['DeviceId']:
            continue
        versions = [release['Version'] for release in update.get('Releases', []) if release.get('Version')]
        if versions:
            return tr('firmware.available_prefix') + tr('common.list_separator').join(dict.fromkeys(versions))
    return tr('firmware.no_update')
