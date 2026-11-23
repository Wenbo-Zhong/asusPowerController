"""Firmware-bounded power settings through asusd, verified against sysfs."""
import json
from pathlib import Path
import subprocess
from .gpu_scope import external_pci, read_metadata

ATTRIBUTES = {
    'ppt_pl1_spl': '处理器持续功率（SPL / PL1）',
    'ppt_pl2_sppt': '处理器慢速加速功率（SPPT / PL2）',
    'ppt_pl3_fppt': '处理器快速加速功率（FPPT / PL3）',
    'ppt_apu_sppt': 'APU 加速功率',
    'ppt_platform_sppt': '平台加速功率',
    'nv_temp_target': '独显温度上限',
    'nv_tgp': '独显功率上限（TGP）',
    'nv_dynamic_boost': '独显动态加速功率（Dynamic Boost）',
}
SERVICE = 'xyz.ljones.Asusd'
PLATFORM = 'xyz.ljones.Platform'
ARMOURY = 'xyz.ljones.AsusArmoury'


def attribute_unit(name):
    return '°C' if name == 'nv_temp_target' else 'W'


class PowerLimits:
    def __init__(self, root=Path('/sys'), runner=subprocess.run):
        self.root = Path(root)
        self.runner = runner
        self.attributes = self.root / 'class/firmware-attributes/asus-armoury/attributes'

    def command(self, *arguments):
        result = self.runner(['busctl', '--system', '--json=short', *arguments],
                             capture_output=True, text=True, timeout=12)
        if result.returncode:
            raise RuntimeError((result.stderr or '功率控制接口调用失败').strip())
        return result.stdout

    def get(self, path, interface, property_name):
        return json.loads(self.command('get-property', SERVICE, path, interface, property_name))['data']

    def enabled(self):
        value = self.get('/xyz/ljones', PLATFORM, 'EnablePptGroup')
        if type(value) is not bool:
            raise ValueError('asusd 功率开关接口格式不兼容')
        return value

    def context(self):
        profile = (self.root / 'firmware/acpi/platform_profile').read_text().strip()
        sources = []
        for path in sorted((self.root / 'class/power_supply').glob('*/online')):
            sources.append((path.parent.name, path.read_text().strip()))
        return {'profile': profile, 'sources': sources}

    def gpu_block_reason(self):
        try:
            owner = json.loads(self.command(
                'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner', 's', 'io.github.usb4gpumanager.Manager',
            ))['data']
            if owner == [True]:
                return '独显由 USB4 GPU Manager 管理'
            if owner != [False]:
                raise ValueError('unknown D-Bus owner state')
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError):
            return '无法确认独显管理权，已暂停独显调节'

        devices = [path.resolve() for path in (self.root / 'bus/pci/devices').glob('*')
                   if read_metadata(path / 'class').startswith('0x03')]
        if any(external_pci(device) for device in devices):
            return '检测到外接显卡，已暂停独显调节'
        internal = [device for device in devices if read_metadata(device / 'vendor') == '0x10de']
        if len(internal) != 1:
            return '无法确认唯一的机身 NVIDIA 独显'
        device = internal[0]
        if (device / 'driver').resolve().name != 'nvidia':
            return '机身独显未由 NVIDIA 驱动管理'
        if read_metadata(device / 'power/runtime_status') != 'active':
            return '机身独显未处于活动状态'
        return ''

    def read_attributes(self, gpu_block=None):
        if gpu_block is None:
            gpu_block = self.gpu_block_reason()
        values = {}
        for name, label in ATTRIBUTES.items():
            if gpu_block and name.startswith('nv_'):
                continue
            directory = self.attributes / name
            try:
                data = {key: int((directory / file).read_text().strip()) for key, file in
                        [('minimum', 'min_value'), ('maximum', 'max_value'),
                         ('step', 'scalar_increment'), ('current', 'current_value')]}
                try:
                    data['default'] = int((directory / 'default_value').read_text().strip())
                except (OSError, ValueError):
                    data['default'] = None
                if not 0 <= data['minimum'] <= data['current'] <= data['maximum'] or data['step'] <= 0:
                    continue
                data['label'] = label
                values[name] = data
            except (OSError, ValueError):
                continue
        return values

    def status(self):
        gpu_block = self.gpu_block_reason()
        values = self.read_attributes(gpu_block)
        if not values:
            return {'supported': False, 'attributes': {}, 'reason': gpu_block or '当前驱动未开放可调功率接口',
                    'gpu_block': gpu_block}
        try:
            for name, data in values.items():
                data['pending'] = self.pending(name)
            return {'supported': True, 'attributes': values, 'enabled': self.enabled(),
                    'context': self.context(), 'gpu_block': gpu_block}
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as error:
            return {'supported': False, 'attributes': values, 'reason': str(error), 'gpu_block': gpu_block}

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('无效的功率调节开关')
        context = self.context()
        self.command('set-property', SERVICE, '/xyz/ljones', PLATFORM, 'EnablePptGroup', 'b', str(enabled).lower())
        if self.context() != context or self.enabled() != enabled:
            raise RuntimeError('电源方案发生变化或功率调节开关回读失败，请重新读取')
        return '已启用当前方案的功率调节' if enabled else '已关闭当前方案的功率调节'

    def pending(self, name):
        if not name.startswith('nv_') or self.gpu_block_reason():
            return None
        try:
            value = self.get('/xyz/ljones/asus_armoury/' + name, ARMOURY, 'QueuedGpuValue')
            return value if type(value) is int and value >= 0 else None
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError):
            return None

    def write(self, name, value):
        if name.startswith('nv_') and (reason := self.gpu_block_reason()):
            raise RuntimeError(reason)
        self.command('set-property', SERVICE, '/xyz/ljones/asus_armoury/' + name,
                     ARMOURY, 'CurrentValue', 'i', str(value))

    def apply(self, name, value, reset=False):
        if name not in ATTRIBUTES:
            raise ValueError('不支持此功率属性')
        if name.startswith('nv_') and (reason := self.gpu_block_reason()):
            raise RuntimeError(reason)
        context = self.context()
        before = self.read_attributes().get(name)
        if before is None:
            raise ValueError('当前设备未开放此功率属性')
        if reset:
            value = before['default']
        if (type(value) is not int or not before['minimum'] <= value <= before['maximum']
                or (value - before['minimum']) % before['step']):
            raise ValueError(f"数值需为 {before['minimum']}–{before['maximum']} {attribute_unit(name)}，步长 {before['step']} {attribute_unit(name)}")
        if not self.enabled():
            raise ValueError('请先启用当前方案的功率调节')
        original_pending = self.pending(name)
        try:
            if self.context() != context:
                raise RuntimeError('电源方案已变化')
            self.write(name, value)
            if self.context() != context:
                raise RuntimeError('电源方案已变化')
            current = self.read_attributes().get(name)
            pending = self.pending(name)
            if pending is not None and pending != value:
                raise RuntimeError('独显存在其他待应用的功率值')
            if pending == value:
                return f"{before['label']} {value} {attribute_unit(name)} 已排队，独显唤醒后生效"
            if current and current['current'] == value:
                return f"{before['label']} 已应用 {value} {attribute_unit(name)}"
            raise RuntimeError('驱动实际功率未达到请求值，asusd 可能只保存了配置')
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as error:
            if self.context() != context:
                raise RuntimeError('设置期间切换了电源或性能方案，请重新读取功率状态') from error
            try:
                self.write(name, original_pending if original_pending is not None else before['current'])
                recovered = self.read_attributes().get(name)
                if not recovered or recovered['current'] != before['current'] or self.pending(name) != original_pending:
                    raise RuntimeError('原值恢复或待应用状态回读不一致')
            except Exception as recovery:
                raise RuntimeError(f'功率设置失败：{error}；恢复未确认：{recovery}') from error
            raise RuntimeError(f'功率设置失败，已恢复原值：{error}') from error
