"""Validated AMD APU clock control. Also runs as an isolated privileged helper."""
import argparse
import json
import os
from pathlib import Path
import re
import socket

# Start with the Strix Point APU identified on the supported ProArt P16.
# Never infer "integrated" merely from AMD vendor ID or card numbering.
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
    # Accept only the APU SCLK-only format. Resetting it must not reset voltage tuning.
    if re.findall(r'^([A-Z_]+):\s*$', text, re.MULTILINE) != ['OD_SCLK', 'OD_RANGE']:
        raise ValueError('核显驱动未提供支持的 SCLK 调节格式')
    points = re.search(r'OD_SCLK:\s*0:\s*(\d+)Mhz\s*1:\s*(\d+)Mhz', text, re.IGNORECASE)
    limits = re.search(r'^SCLK:\s*(\d+)Mhz\s*(\d+)Mhz', text, re.MULTILINE | re.IGNORECASE)
    if not points or not limits:
        raise ValueError('无法读取核显频率范围')
    minimum, maximum = map(int, points.groups())
    lower, upper = map(int, limits.groups())
    if not 0 < lower <= minimum <= maximum <= upper:
        raise ValueError('核显频率范围无效')
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
        for path in (HELPER, *HELPER.parents):
            stat = path.lstat()
            if path.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
                return False
        return HELPER.is_file()
    except OSError:
        return False


def change_clock(device, maximum=None):
    before = clock_info(device)
    if before['mode'] not in ('auto', 'manual'):
        raise ValueError('当前核显处于其他性能模式，请先由原管理程序恢复自动调频')
    if maximum is not None:
        if isinstance(maximum, bool) or not isinstance(maximum, int):
            raise ValueError('最高频率必须是整数 MHz')
        if not before['minimum'] <= maximum <= before['upper']:
            raise ValueError(f"最高频率需在 {before['minimum']}–{before['upper']} MHz 内")
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
            raise RuntimeError('核显设置后的回读不一致')
        return after
    except (OSError, ValueError, RuntimeError) as error:
        if not changed:
            raise RuntimeError(f'无法进入核显手动调频模式：{error}') from error
        # Recover both endpoints and mode, including after a failed commit/reset.
        try:
            mode_path.write_text('manual\n')
            od_path.write_text(f"s 1 {before['maximum']}\n")
            od_path.write_text(f"s 0 {before['minimum']}\n")
            od_path.write_text('c\n')
            mode_path.write_text(before['mode'] + '\n')
            recovered = clock_info(device)
            if any(recovered[key] != before[key] for key in ('minimum', 'maximum', 'mode')):
                raise RuntimeError('恢复后回读不一致')
        except (OSError, ValueError, RuntimeError) as recovery:
            raise RuntimeError(f'核显设置失败：{error}；恢复也失败：{recovery}') from error
        raise RuntimeError(f'核显设置失败，已恢复原设置：{error}') from error


def main():
    parser = argparse.ArgumentParser(description='AMD 核显最高频率控制')
    parser.add_argument('--device', help='PCI 地址')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--max', type=int, dest='maximum', help='最高频率 MHz')
    action.add_argument('--reset', action='store_true', help='恢复原厂频率范围及自动调频')
    args = parser.parse_args()
    try:
        devices = find_devices()
        if args.maximum is None and not args.reset:
            print(json.dumps({key: clock_info(device) for key, device in devices.items()}, ensure_ascii=False))
            return
        if os.geteuid() != 0:
            raise PermissionError('修改核显频率需要管理员授权')
        if args.device not in devices:
            raise ValueError('未找到支持的 AMD 核显')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as lock:
            lock.bind('\0asus-power-igpu-' + args.device)
            result = change_clock(devices[args.device], args.maximum)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
