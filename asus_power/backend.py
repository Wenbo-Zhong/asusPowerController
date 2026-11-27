"""界面的硬件入口：汇总 hardware 探测，校验 asusctl 写入，并调度核显提权助手。"""
from .i18n import CATALOG_DIR, tr
from pathlib import Path
import os
import shutil
import subprocess
from .hardware.battery import read_battery
from .hardware.profile_epp import ProfileEpp
from .hardware.rog_features import RogFeatures
from .hardware.fan_curves import FanCurves
from .hardware.power_limits import PowerLimits
from .hardware.power_supply import discover, external_power
from .hardware.gpu_scope import external_pci, graphics_device, read_metadata
from .igpu import HELPER, clock_info, find_devices, trusted_helper


class HardwareError(RuntimeError):
    pass


def read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default


def number(path, divisor=1):
    try:
        return float(read(path)) / divisor
    except (ValueError, TypeError):
        return None


class LinuxBackend:
    def __init__(self, root='/sys', runner=subprocess.run, executable=None):
        self.root = Path(root)
        self.runner = runner
        self.exe = executable if executable is not None else shutil.which('asusctl')
        self.discover_power_supplies()
        self.profile_path = self.root / 'firmware/acpi/platform_profile'
        self.profiles = (read(self.profile_path.with_name('platform_profile_choices'), '')).split()
        self.hwmon = sorted((self.root / 'class/hwmon').glob('*'))
        self.fans = []
        for h in self.hwmon:
            if graphics_device(h) is None and read(h / 'name') in ('asus', 'asus_custom_fan_curve'):
                for index, name in [(1, 'cpu'), (2, 'gpu'), (3, 'mid')]:
                    if (h / f'pwm{index}_auto_point1_temp').exists():
                        if name not in self.fans:
                            self.fans.append(name)
        self.led = any((self.root / 'class/leds').glob('*kbd_backlight'))
        self.version = self.command('--help') if self.exe else ''
        # 仅为已确认的现代 asusctl 子命令开放写操作，避免跨版本误用接口。
        self.modern = 'battery' in self.version and 'profile' in self.version

    def discover_power_supplies(self):
        self.supplies, self.batteries, self.battery = discover(self.root)

    def external_power(self):
        return external_power(self.supplies, self.battery)

    def command(self, *args):
        if not self.exe:
            raise HardwareError(tr('backend.asusctl_missing'))
        try:
            result = self.runner([self.exe, *args], capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HardwareError(str(error)) from error
        if result.returncode:
            raise HardwareError((result.stderr or result.stdout or tr('backend.asusctl_failed')).strip())
        return result.stdout.strip()

    def capabilities(self):
        return {'model': read(self.root / 'class/dmi/id/product_name', tr('common.unknown_device')),
                'profiles': self.profiles, 'asusctl': self.exe, 'modern_cli': self.modern,
                'charge_limit': bool(self.battery and (self.battery / 'charge_control_end_threshold').exists()),
                'battery_info': read_battery(self.battery),
                'fans': self.fans, 'keyboard': self.led, 'igpu_clock': self.igpu_clock(),
                'profile_epp': ProfileEpp(self.runner).status(),
                'rog_features': RogFeatures(self.runner).status(),
                'fan_curves': FanCurves(self.runner).status(self.profiles),
                'power_limits': PowerLimits(self.root, self.runner).status()}

    def one_shot_charge(self):
        if self.battery is None or not (self.battery / 'charge_control_end_threshold').is_file():
            raise HardwareError(tr('battery.full_charge_unsupported'))
        features = RogFeatures(self.runner)
        message = features.full_charge()
        if number(self.battery / 'charge_control_end_threshold') != 100:
            raise HardwareError(tr('battery.full_charge_readback'))
        return message

    def save_aura(self, path, color, enabled):
        return RogFeatures(self.runner).save_aura(path, color, enabled)

    def save_supply_rules(self, rules):
        return RogFeatures(self.runner).save_supply(
            rules, self.profiles, current_source=lambda: self.combination_context()['ac'])

    def combination_context(self):
        self.discover_power_supplies()
        return {'profile': read(self.profile_path), 'ac': self.external_power()}

    def apply_combination(self, mode, source, entry):
        from .combinations import CombinationApplier
        return CombinationApplier(self).apply(mode, source, entry)

    def power_limits(self):
        return PowerLimits(self.root, self.runner).status()

    def read_fan_curves(self, profile):
        return FanCurves(self.runner).read(profile)

    def reset_fan_curves(self, profile):
        return FanCurves(self.runner).defaults(profile)

    def save_profile_epp(self, enabled, profiles):
        return ProfileEpp(self.runner).save(enabled, profiles)

    def cpu_epp_status(self):
        policies = list((self.root / 'devices/system/cpu/cpufreq').glob('policy[0-9]*'))
        return {key: sorted({read(policy / key, tr('common.unknown')) for policy in policies})
                for key in ('scaling_governor', 'energy_performance_preference')}

    def igpu_clock(self):
        devices = find_devices(self.root)
        if not devices:
            return {'supported': False, 'reason': tr('igpu.not_detected')}
        address, device = next(iter(devices.items()))
        try:
            return {**clock_info(device), 'device': address, 'supported': True,
                    'helper_ready': trusted_helper()}
        except (OSError, ValueError) as error:
            return {'supported': False, 'reason': str(error)}

    def privileged_igpu_command(self, arguments):
        command = arguments
        if os.geteuid() != 0:
            pkexec = shutil.which('pkexec')
            if not pkexec:
                raise HardwareError(tr('backend.pkexec_missing'))
            command = [pkexec, *arguments]
        try:
            result = self.runner(command, capture_output=True, text=True, timeout=150)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HardwareError(tr('igpu.execution_failed', error=error)) from error
        if result.returncode:
            raise HardwareError((result.stderr or tr('igpu.cancelled')).strip())

    def install_igpu_helper(self):
        if not self.igpu_clock()['supported']:
            raise HardwareError(tr('igpu.unsupported'))
        source = Path(__file__).resolve().parent
        # 一次授权安装助手和语言资源；之后只运行经所有权检查的固定路径。
        files = [source / 'igpu.py', source / 'i18n.py', *sorted(CATALOG_DIR.glob('*.json'))]
        self.privileged_igpu_command(['/usr/bin/install', '-D', '-m', '0644', '-o', 'root', '-g', 'root',
                                     '-t', str(HELPER.parent), '--', *(str(file) for file in files)])
        if not trusted_helper():
            raise HardwareError(tr('igpu.helper_untrusted'))
        return tr('igpu.helper_installed')

    def set_igpu_max(self, maximum=None):
        info = self.igpu_clock()
        if not info['supported']:
            raise HardwareError(info['reason'])
        if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int)
                                    or not info['minimum'] <= maximum <= info['upper']):
            raise ValueError(tr('igpu.integer_range', minimum=info['minimum'], upper=info['upper']))
        if not trusted_helper():
            raise HardwareError(tr('igpu.install_first'))
        action = ['--reset'] if maximum is None else ['--max', str(maximum)]
        self.privileged_igpu_command(['/usr/bin/python3', '-I', str(HELPER),
                                     '--language', tr.language, '--device', info['device'], *action])
        actual = self.igpu_clock()
        expected = info['upper'] if maximum is None else maximum
        mode = 'auto' if maximum is None else 'manual'
        if (not actual['supported'] or actual['device'] != info['device']
                or actual['maximum'] != expected or actual['mode'] != mode):
            raise HardwareError(tr('igpu.readback_check'))
        return tr('igpu.auto_restored') if maximum is None else tr('igpu.max_applied', maximum=maximum)

    def snapshot(self):
        self.discover_power_supplies()
        battery = self.battery
        battery_info = read_battery(battery)
        ac = self.external_power()
        sensors = {}
        power_limits = self.power_limits()
        for h in self.hwmon:
            device = graphics_device(h)
            if device is not None and (external_pci(device)
                    or read_metadata(device / 'power/runtime_status') != 'active'
                    or (read_metadata(device / 'vendor') == '0x10de'
                        and power_limits.get('gpu_block'))):
                continue
            name = read(h / 'name', h.name)
            for p in sorted(h.glob('temp*_input')):
                value = number(p, 1000)
                if value is not None:
                    sensors[f'{name} · {read(p.with_name(p.name.replace("_input", "_label")), p.stem)}'] = value
            for p in sorted(h.glob('fan*_input')):
                value = number(p)
                if value is not None:
                    sensors[f'{name} · {read(p.with_name(p.name.replace("_input", "_label")), p.stem)} RPM'] = value
        return {'profile': read(self.profile_path), 'ac': ac,
                'battery': battery_info.get('capacity_percent'),
                'battery_info': battery_info,
                'battery_status': battery_info.get('status'),
                'limit': battery_info.get('limit_percent'),
                'battery_watts': battery_info.get('power_w'),
                'sensors': sensors, 'igpu_clock': self.igpu_clock(), 'cpu_epp': self.cpu_epp_status(),
                'power_limits': power_limits}

    def switch_profile_on_power_change(self, target, profile, ac):
        # 排队任务可能晚于桌面手动切换或拔电开始，执行前须确认采样状态仍有效。
        if self.combination_context() == {'profile': profile, 'ac': ac}:
            self.set_profile(target)

    def set_profile(self, profile):
        if not self.modern or profile not in self.profiles:
            raise ValueError(tr('backend.profile_unsupported'))
        self.command('profile', 'set', profile)
        actual = read(self.profile_path)
        if actual != profile:
            raise HardwareError(tr('backend.profile_readback', profile=profile, actual=actual))

    def set_limit(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 20 <= value <= 100:
            raise ValueError(tr('battery.limit_range'))
        if not self.modern or self.battery is None or not (self.battery / 'charge_control_end_threshold').exists():
            raise ValueError(tr('battery.limit_unsupported'))
        self.command('battery', 'limit', str(value))
        actual = number(self.battery / 'charge_control_end_threshold')
        if actual != value:
            raise HardwareError(tr('battery.limit_readback', value=value, actual=actual))

    def set_keyboard(self, level):
        if not self.modern or not self.led or level not in ('off', 'low', 'med', 'high'):
            raise ValueError(tr('keyboard.brightness_unsupported'))
        self.command('leds', 'set', level)
