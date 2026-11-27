"""AMD 核显 SCLK 校验与失败恢复；也可携同目录的 i18n/JSON 独立提权运行。"""
if __package__:
    from .i18n import Translator, tr
else:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from i18n import Translator, tr

import argparse
import json
import os
from pathlib import Path
import re
import socket

# 仅接受已验证的 Strix Point PCI ID；AMD 厂商号或 card 编号不能证明是核显。
APU_IDS = {'0x150e'}
HELPER = Path('/usr/local/lib/asus-power-controller/igpu.py')


def find_devices(root=Path('/sys')):
    devices = {}
    for card in sorted((root / 'class/drm').glob('card*')):
        if not re.fullmatch(r'card\d+', card.name):
            continue
        device = card / 'device'
        try:
            vendor = (device / 'vendor').read_text().strip()
            model = (device / 'device').read_text().strip()
            properties = dict(line.split('=', 1) for line in (device / 'uevent').read_text().splitlines() if '=' in line)
            address = properties.get('PCI_SLOT_NAME', '')
            if (vendor == '0x1002' and model in APU_IDS and properties.get('DRIVER') == 'amdgpu'
                    and re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]', address)):
                devices[address] = device
        except OSError:
            continue
    return devices


def clock_info(device):
    text = (device / 'pp_od_clk_voltage').read_text()
    # 只接受纯 SCLK 格式，避免恢复频率时误清除驱动的电压调校。
    if re.findall(r'^([A-Z_]+):\s*$', text, re.MULTILINE) != ['OD_SCLK', 'OD_RANGE']:
        raise ValueError(tr('igpu.format_unsupported'))
    points = re.search(r'OD_SCLK:\s*0:\s*(\d+)Mhz\s*1:\s*(\d+)Mhz', text, re.IGNORECASE)
    limits = re.search(r'^SCLK:\s*(\d+)Mhz\s*(\d+)Mhz', text, re.MULTILINE | re.IGNORECASE)
    if not points or not limits:
        raise ValueError(tr('igpu.range_unreadable'))
    minimum, maximum = map(int, points.groups())
    lower, upper = map(int, limits.groups())
    if not 0 < lower <= minimum <= maximum <= upper:
        raise ValueError(tr('igpu.invalid_range'))
    mode = (device / 'power_dpm_force_performance_level').read_text().strip()
    frequency = None
    for path in sorted((device / 'hwmon').glob('hwmon*/freq1_input')):
        try:
            frequency = int(path.read_text().strip()) / 1_000_000
            break
        except (OSError, ValueError):
            continue
    return dict(minimum=minimum, maximum=maximum, lower=lower, upper=upper,
                mode=mode, frequency_mhz=frequency)


def trusted_helper():
    try:
        locale_directory = HELPER.parent / 'locales'
        if not locale_directory.is_dir():
            locale_directory = HELPER.parent
        files = {HELPER, HELPER.with_name('i18n.py'),
                 *(locale_directory / f'{language}.json' for language in ('en', 'zh_Hans', 'zh_Hant')),
                 *locale_directory.glob('*.json')}
        # i18n.py 与 JSON 同样参与可信检查；旧版单文件助手必须重新安装。
        # 若存在 locales/，还须检查实际加载的目录，不能漏过符号链接或宽松权限。
        paths = files | {parent for file in files for parent in file.parents}
        for path in paths:
            metadata = path.lstat()
            if path.is_symlink() or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                return False
        return all(file.is_file() for file in files)
    except OSError:
        return False


def change_clock(device, maximum=None):
    before = clock_info(device)
    if before['mode'] not in ('auto', 'manual'):
        raise ValueError(tr('igpu.other_mode'))
    if maximum is not None:
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise ValueError(tr('igpu.integer_required'))
        if not before['minimum'] <= maximum <= before['upper']:
            raise ValueError(tr('igpu.frequency_range', minimum=before['minimum'], upper=before['upper']))
    mode_path = device / 'power_dpm_force_performance_level'
    od_path = device / 'pp_od_clk_voltage'
    changed = False
    try:
        mode_path.write_text('manual\n')
        changed = True
        if maximum is None:
            od_path.write_text('r\n')
        else:
            od_path.write_text(f's 1 {maximum}\n')
        od_path.write_text('c\n')
        if maximum is None:
            mode_path.write_text('auto\n')
        after = clock_info(device)
        expected_max = before['upper'] if maximum is None else maximum
        expected_mode = 'auto' if maximum is None else 'manual'
        if (after['maximum'] != expected_max or after['mode'] != expected_mode
                or after['minimum'] != (before['lower'] if maximum is None else before['minimum'])):
            raise RuntimeError(tr('igpu.readback_mismatch'))
        return after
    except (OSError, ValueError, RuntimeError) as error:
        if not changed:
            raise RuntimeError(tr('igpu.manual_failed', error=error)) from error
        # 提交或重置失败时同时恢复上下限和模式，不能只恢复最高频率。
        try:
            mode_path.write_text('manual\n')
            od_path.write_text(f"s 1 {before['maximum']}\n")
            od_path.write_text(f"s 0 {before['minimum']}\n")
            od_path.write_text('c\n')
            mode_path.write_text(before['mode'] + '\n')
            recovered = clock_info(device)
            if any(recovered[key] != before[key] for key in ('minimum', 'maximum', 'mode')):
                raise RuntimeError(tr('igpu.recovery_mismatch'))
        except (OSError, ValueError, RuntimeError) as recovery:
            raise RuntimeError(tr('igpu.failed_recovery', error=error, recovery=recovery)) from error
        raise RuntimeError(tr('igpu.failed_restored', error=error)) from error


def main():
    global tr
    # pkexec 会清理桌面环境变量，因此主进程显式传入已经解析的消息语言。
    language_parser = argparse.ArgumentParser(add_help=False)
    language_parser.add_argument('--language')
    language, _ = language_parser.parse_known_args()
    if language.language:
        tr = Translator(language.language)
    parser = argparse.ArgumentParser(description=tr('igpu.cli_description'))
    parser.add_argument('--language', help=argparse.SUPPRESS)
    parser.add_argument('--device', help=tr('igpu.device_help'))
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--max', type=int, dest='maximum', help=tr('igpu.max_help'))
    action.add_argument('--reset', action='store_true', help=tr('igpu.reset_help'))
    args = parser.parse_args()
    try:
        devices = find_devices()
        if args.maximum is None and not args.reset:
            print(json.dumps({key: clock_info(device) for key, device in devices.items()}, ensure_ascii=False))
            return
        if os.geteuid() != 0:
            raise PermissionError(tr('igpu.admin_required'))
        if args.device not in devices:
            raise ValueError(tr('igpu.device_missing'))
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
            lock.bind('\0asus-power-igpu-' + args.device)
            result = change_clock(devices[args.device], args.maximum)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
