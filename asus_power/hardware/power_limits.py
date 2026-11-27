"""按固件范围经 asusd 调节功率；以 sysfs 当前值和待应用值确认结果。"""
from ..i18n import tr
import json
from pathlib import Path
import subprocess
from .bus import BusClient, PLATFORM, SERVICE
from .gpu_scope import external_pci, read_metadata
from .power_supply import discover, external_power

ATTRIBUTES = {
    'ppt_pl1_spl': tr('power.cpu_sustained'),
    'ppt_pl2_sppt': tr('power.cpu_slow'),
    'ppt_pl3_fppt': tr('power.cpu_fast'),
    'ppt_apu_sppt': tr('power.apu_boost'),
    'ppt_platform_sppt': tr('power.platform_boost'),
    'nv_temp_target': tr('power.gpu_temperature'),
    'nv_tgp': tr('power.gpu_tgp'),
    'nv_dynamic_boost': tr('power.gpu_boost'),
}
ARMOURY = 'xyz.ljones.AsusArmoury'
GPU_MANAGER = 'io.github.usb4gpumanager.Manager'
GPU_MANAGER_PATH = '/io/github/usb4gpumanager/Manager'


def attribute_unit(name):
    return '°C' if name == 'nv_temp_target' else 'W'


class PowerLimits:
    def __init__(self, root=Path('/sys'), runner=subprocess.run):
        self.root = Path(root)
        self.bus = BusClient(runner, timeout=12, error=tr('power.call_failed'))
        self.attributes = self.root / 'class/firmware-attributes/asus-armoury/attributes'

    def get(self, path, interface, property_name):
        return self.bus.get(path, interface, property_name)

    def enabled(self):
        value = self.get('/xyz/ljones', PLATFORM, 'EnablePptGroup')
        if type(value) is not bool:
            raise ValueError(tr('power.switch_incompatible'))
        return value

    def context(self):
        profile = (self.root / 'firmware/acpi/platform_profile').read_text().strip()
        supplies, _, battery = discover(self.root)
        return {'profile': profile, 'ac': external_power(supplies, battery)}

    def gpu_block_reason(self):
        try:
            owner, = self.bus.json(
                'call', 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner', 's', GPU_MANAGER,
            )['data']
            if type(owner) is not bool:
                raise ValueError('unknown D-Bus owner state')
            if owner:
                status, = self.bus.json(
                    'call', GPU_MANAGER, GPU_MANAGER_PATH, GPU_MANAGER + '1', 'GetStatus',
                )['data']
                enabled = json.loads(status)['automation_enabled']
                if type(enabled) is not bool:
                    raise ValueError('unknown GPU automation state')
                if enabled:
                    return tr('power.gpu_manager')
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError):
            return tr('power.ownership_unknown')

        devices = [path.resolve() for path in (self.root / 'bus/pci/devices').glob('*')
                   if read_metadata(path / 'class').startswith('0x03')]
        if any(external_pci(device) for device in devices):
            return tr('power.external_gpu')
        internal = [device for device in devices if read_metadata(device / 'vendor') == '0x10de']
        if len(internal) != 1:
            return tr('power.ambiguous_gpu')
        device = internal[0]
        if (device / 'driver').resolve().name != 'nvidia':
            return tr('power.gpu_wrong_driver')
        if read_metadata(device / 'power/runtime_status') != 'active':
            return tr('power.gpu_inactive')
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
            return {'supported': False, 'attributes': {}, 'reason': gpu_block or tr('power.no_interface'),
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
            raise ValueError(tr('power.invalid_switch'))
        context = self.context()
        self.bus.put('/xyz/ljones', PLATFORM, 'EnablePptGroup', 'b', enabled)
        if self.context() != context or self.enabled() != enabled:
            raise RuntimeError(tr('power.switch_readback'))
        return tr('power.enabled') if enabled else tr('power.disabled')

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
        self.bus.put('/xyz/ljones/asus_armoury/' + name, ARMOURY, 'CurrentValue', 'i', value)

    def apply(self, name, value, reset=False):
        if name not in ATTRIBUTES:
            raise ValueError(tr('power.attribute_unsupported'))
        if name.startswith('nv_') and (reason := self.gpu_block_reason()):
            raise RuntimeError(reason)
        context = self.context()
        before = self.read_attributes().get(name)
        if before is None:
            raise ValueError(tr('power.attribute_unavailable'))
        if reset:
            value = before['default']
        if (type(value) is not int or not before['minimum'] <= value <= before['maximum']
                or (value - before['minimum']) % before['step']):
            raise ValueError(tr('power.value_range', minimum=before['minimum'], maximum=before['maximum'],
                                unit=attribute_unit(name), step=before['step']))
        if not self.enabled():
            raise ValueError(tr('power.enable_first'))
        original_pending = self.pending(name)
        try:
            if self.context() != context:
                raise RuntimeError(tr('power.profile_changed'))
            self.write(name, value)
            if self.context() != context:
                raise RuntimeError(tr('power.profile_changed'))
            current = self.read_attributes().get(name)
            pending = self.pending(name)
            if pending is not None and pending != value:
                raise RuntimeError(tr('power.pending_conflict'))
            if pending == value:
                return tr('power.queued', label=before['label'], value=value, unit=attribute_unit(name))
            if current and current['current'] == value:
                return tr('power.applied', label=before['label'], value=value, unit=attribute_unit(name))
            raise RuntimeError(tr('power.driver_mismatch'))
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as error:
            if self.context() != context:
                raise RuntimeError(tr('power.context_changed')) from error
            try:
                self.write(name, original_pending if original_pending is not None else before['current'])
                recovered = self.read_attributes().get(name)
                if not recovered or recovered['current'] != before['current'] or self.pending(name) != original_pending:
                    raise RuntimeError(tr('power.recovery_mismatch'))
            except Exception as recovery:
                raise RuntimeError(tr('power.failed_recovery', error=error, recovery=recovery)) from error
            raise RuntimeError(tr('power.failed_restored', error=error)) from error
