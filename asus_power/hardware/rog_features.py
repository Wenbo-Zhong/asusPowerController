"""Additional ROG controls through the installed asusd service."""
import re
import subprocess

from .bus import BusClient, OBJECT, PLATFORM as INTERFACE, SERVICE
from .fan_curves import PROFILE_IDS

AURA = 'xyz.ljones.Aura'
POWER_FIELDS = ('启动', '运行', '睡眠', '关机')
SUPPLY_FIELDS = {
    'ac': ('ChangePlatformProfileOnAc', 'PlatformProfileOnAc'),
    'battery': ('ChangePlatformProfileOnBattery', 'PlatformProfileOnBattery'),
}
PROFILE_NAMES = {value: name for name, value in PROFILE_IDS.items()}


class RogFeatures:
    def __init__(self, runner=subprocess.run):
        self.bus = BusClient(runner, timeout=8, error='asusd ROG 接口调用失败')

    def get(self, path, interface, name):
        return self.bus.get(path, interface, name)

    def put(self, path, interface, name, signature, *values):
        return self.bus.put(path, interface, name, signature, *values)

    def aura(self):
        candidates = self.bus.command('--list', 'tree', SERVICE).splitlines()
        for path in candidates:
            if not re.fullmatch(r'/xyz/ljones/aura/[a-zA-Z0-9_]+', path):
                continue
            # Limit this interface to the keyboard power zone.
            zones = self.get(path, AURA, 'SupportedPowerZones')
            if 1 in zones:
                return self.read_aura(path)
        return None

    def read_aura(self, path):
        mode = self.get(path, AURA, 'LedModeData')
        power, = self.get(path, AURA, 'LedPower')
        keyboard = next(row[1:] for row in power if row[0] == 1)
        return {'path': path, 'color': mode[2], 'power': keyboard}

    def save_aura(self, path, color, enabled):
        if not re.fullmatch(r'/xyz/ljones/aura/[a-zA-Z0-9_]+', path):
            raise ValueError('无效的 Aura 键盘路径')
        if len(color) != 3 or any(type(value) is not int or not 0 <= value <= 255 for value in color):
            raise ValueError('无效的键盘颜色')
        if len(enabled) != 4 or any(type(value) is not bool for value in enabled):
            raise ValueError('无效的键盘灯光状态')
        before = self.read_aura(path)
        mode = self.get(path, AURA, 'LedModeData')
        power, = self.get(path, AURA, 'LedPower')
        updated_power = [[row[0], *enabled] if row[0] == 1 else row for row in power]
        changes = []
        if list(color) != before['color']:
            changes.append(('LedModeData', '(uu(yyy)(yyy)ss)',
                            [*mode[:2], *mode[2], *mode[3], *mode[4:]],
                            [*mode[:2], *color, *mode[3], *mode[4:]]))
        if list(enabled) != before['power']:
            changes.append(('LedPower', '(a(ubbbb))',
                            [len(power), *(value for row in power for value in row)],
                            [len(updated_power), *(value for row in updated_power for value in row)]))
        self.apply_changes(path, AURA, changes, lambda: self.read_aura(path),
                           dict(before, color=list(color), power=list(enabled)))
        return '键盘灯效已保存'

    def supply_rules(self):
        result = {}
        for source, (toggle, profile) in SUPPLY_FIELDS.items():
            enabled = self.get(OBJECT, INTERFACE, toggle)
            mode = self.get(OBJECT, INTERFACE, profile)
            if type(enabled) is not bool or type(mode) is not int or mode not in PROFILE_NAMES:
                raise ValueError('asusd 供电规则格式不兼容')
            result[source] = (enabled, PROFILE_NAMES[mode])
        return result

    def save_supply(self, desired, profiles, current_source=None):
        before = self.supply_rules()
        changes = []
        for source, (toggle, profile) in SUPPLY_FIELDS.items():
            enabled, mode = desired[source]
            old_enabled, old_mode = before[source]
            if type(enabled) is not bool or mode not in profiles or mode not in PROFILE_IDS:
                raise ValueError('无效的插拔电源规则')
            if mode != old_mode:
                changes.append((profile, 'u', [PROFILE_IDS[old_mode]], [PROFILE_IDS[mode]]))
            if enabled != old_enabled:
                changes.append((toggle, 'b', [old_enabled], [enabled]))
        active = self.get(OBJECT, INTERFACE, 'PlatformProfile')
        source_at_start = current_source() if current_source else None
        applied_modes = set()

        def record(name, values):
            if name in {fields[1] for fields in SUPPLY_FIELDS.values()}:
                applied_modes.update(values)

        try:
            self.apply_changes(OBJECT, INTERFACE, changes, self.supply_rules, desired, record)
        finally:
            # asusd applies a changed mode immediately, even for the inactive
            # power source. Preserve the running mode when saving the rule.
            same_source = current_source is None or current_source() == source_at_start
            current = self.get(OBJECT, INTERFACE, 'PlatformProfile') if applied_modes and same_source else active
            if current != active and current in applied_modes:
                self.put(OBJECT, INTERFACE, 'PlatformProfile', 'u', active)
                if self.get(OBJECT, INTERFACE, 'PlatformProfile') != active:
                    raise RuntimeError('供电规则已处理，但原运行模式未能恢复')
        return 'asusd 插拔电源规则已保存'

    def apply_changes(self, path, interface, changes, read, desired, on_attempt=None):
        attempted = []
        try:
            for name, signature, previous, values in changes:
                attempted.append((name, signature, previous))
                if on_attempt:
                    on_attempt(name, values)
                self.put(path, interface, name, signature, *values)
            if read() != desired:
                raise RuntimeError('设置回读不一致')
        except Exception as error:
            failures = []
            for name, signature, values in reversed(attempted):
                try:
                    if on_attempt:
                        on_attempt(name, values)
                    self.put(path, interface, name, signature, *values)
                except Exception as recovery:
                    failures.append(str(recovery))
            if failures:
                raise RuntimeError(f'{error}；恢复失败：' + ' / '.join(failures)) from error
            raise

    def status(self):
        result = {}
        for key, read in [('aura', self.aura), ('supply', self.supply_rules)]:
            try:
                result[key] = read()
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError,
                    KeyError, TypeError, StopIteration):
                result[key] = None
        return result

    def full_charge(self):
        self.bus.command('call', SERVICE, OBJECT, INTERFACE, 'OneShotFullCharge')
        return '已请求一次充满'
