"""六套供电/模式配置的校验、去重调度与事务式应用；不管理独立的 EPP 规则。"""
from .i18n import tr
from copy import deepcopy
import json
from threading import Lock
from .combination_presets import for_machine
from .hardware.fan_curves import validate as validate_curve, FanCurves
from .hardware.power_limits import ATTRIBUTES, PowerLimits

MODES = ('quiet', 'balanced', 'performance')
SOURCES = ('ac', 'battery')


def defaults():
    return {'enabled': False,
            'entries': {mode: {source: {'power': {}, 'fans': {}}
                               for source in SOURCES} for mode in MODES}}


def validate(settings):
    if not isinstance(settings, dict) or type(settings.get('enabled')) is not bool:
        raise ValueError(tr('combination.invalid_switch'))
    entries = settings.get('entries')
    if not isinstance(entries, dict) or set(entries) != set(MODES):
        raise ValueError(tr('combination.required_modes'))
    for sources in entries.values():
        if not isinstance(sources, dict) or set(sources) != set(SOURCES):
            raise ValueError(tr('combination.required_sources'))
        for entry in sources.values():
            if not isinstance(entry, dict) or set(entry) != {'power', 'fans'}:
                raise ValueError(tr('combination.required_sections'))
            power, fans = entry['power'], entry['fans']
            if not isinstance(power, dict) or any(name not in ATTRIBUTES or type(value) is not int or value < 0 for name, value in power.items()):
                raise ValueError(tr('combination.invalid_power'))
            if not isinstance(fans, dict):
                raise ValueError(tr('combination.invalid_fans'))
            for name, curve in fans.items():
                if not isinstance(curve, dict) or name != curve.get('fan'):
                    raise ValueError(tr('combination.fan_name_mismatch'))
                validate_curve(curve)


def normalized(value):
    if value is None:
        value = defaults()
        for_machine(value['entries'])
        validate(value)
        return value
    value = deepcopy(value)
    if isinstance(value, dict):
        value.pop('epp_values', None)
        entries = value.get('entries')
        if isinstance(entries, dict):
            for sources in entries.values():
                if isinstance(sources, dict):
                    for entry in sources.values():
                        if isinstance(entry, dict):
                            entry.pop('epp', None)
    validate(value)
    return value


def effective(settings, mode, source):
    validate(settings)
    return deepcopy(settings['entries'][mode][source])


class CombinationPolicy:
    """仅在实际状态或配置变化时应用一次，避免每次采样覆盖用户手动调整。"""
    def __init__(self):
        self.previous = None
        self.revision = 0
        self.lock = Lock()

    @staticmethod
    def fingerprint(mode, source, entry):
        return mode, source, json.dumps(entry, sort_keys=True)

    def invalidate(self):
        with self.lock:
            self.previous = None
            self.revision += 1

    def run_pending(self, request, revision, settings, apply):
        """尚未执行的任务必须重新检查版本；新保存或新状态使旧任务失效。"""
        mode, source, entry = request
        with self.lock:
            if (revision != self.revision or not settings.get('enabled')
                    or self.previous != self.fingerprint(*request)
                    or effective(settings, mode, source) != entry):
                return None
        return apply(mode, source, entry)

    def request(self, state, settings):
        if not settings.get('enabled') or state.get('ac') is None or state.get('profile') not in MODES:
            if self.previous is not None:
                self.invalidate()
            return None
        mode, source = state['profile'], 'ac' if state['ac'] else 'battery'
        entry = effective(settings, mode, source)
        fingerprint = self.fingerprint(mode, source, entry)
        with self.lock:
            if fingerprint == self.previous:
                return None
            self.previous = fingerprint
            self.revision += 1
        return mode, source, entry


class CombinationApplier:
    def __init__(self, backend):
        self.backend = backend
        self.power = PowerLimits(backend.root, backend.runner)
        self.fans = FanCurves(backend.runner)

    def apply(self, mode, source, entry):
        # 写入前校验整个组合及固件范围，避免前半部分成功后才发现后半部分无效。
        if mode not in MODES or source not in SOURCES:
            raise ValueError(tr('combination.invalid_context'))
        probe = defaults()
        probe['entries'][mode][source] = deepcopy(entry)
        validate(probe)
        context = self.backend.combination_context()
        if context != {'profile': mode, 'ac': source == 'ac'}:
            raise RuntimeError(tr('combination.context_mismatch'))

        def check():
            if self.backend.combination_context() != context:
                raise RuntimeError(tr('combination.context_changed'))

        gpu_block = (self.power.gpu_block_reason()
                     if any(name.startswith('nv_') for name in entry['power']) else '')
        if gpu_block:
            entry = dict(entry, power={name: value for name, value in entry['power'].items()
                                       if not name.startswith('nv_')})
        attributes = self.power.read_attributes() if entry['power'] else {}
        for name, value in entry['power'].items():
            data = attributes.get(name)
            if data is None or not data['minimum'] <= value <= data['maximum'] or (value - data['minimum']) % data['step']:
                raise ValueError(tr('combination.invalid_range', label=ATTRIBUTES[name]))
        before_fans = self.fans.read(mode) if entry['fans'] else {}
        if not set(entry['fans']).issubset(before_fans):
            raise ValueError(tr('combination.fan_unsupported'))
        enabled = self.power.enabled() if entry['power'] else None
        pending = {name: self.power.pending(name) for name in attributes}
        touched_power, touched_fans = [], []
        switched = False
        queued = False
        try:
            check()
            if entry['power'] and not enabled:
                switched = True
                touched_power.extend(attributes)
                self.power.set_enabled(True)
                # 开启固件功率组可能顺带加载 asusd 保存的其他值。
                # 未被当前组合管理的参数也须保留，不能因开启总开关而被改写。
                after_enable = self.power.read_attributes()
                for name in attributes.keys() - entry['power'].keys():
                    check()
                    desired = pending[name] if pending[name] is not None else attributes[name]['current']
                    if (after_enable.get(name, {}).get('current') != attributes[name]['current']
                            or self.power.pending(name) != pending[name]):
                        self.power.apply(name, desired)
            for name, value in entry['power'].items():
                check()
                if name not in touched_power:
                    touched_power.append(name)
                self.power.apply(name, value)
                check()
            for name, curve in entry['fans'].items():
                check()
                touched_fans.append(name)
                self.fans.save(mode, curve)
                check()
            # 最后统一回读早先的写入，以发现 asusd 或其他电源服务的异步覆盖。
            check()
            expected_power = dict(entry['power'])
            if switched:
                for name in attributes.keys() - entry['power'].keys():
                    expected_power[name] = pending[name] if pending[name] is not None else attributes[name]['current']
            actual_power = self.power.read_attributes() if expected_power else {}
            for name, value in expected_power.items():
                queued_value = self.power.pending(name)
                if ((queued_value is not None and queued_value != value)
                        or (queued_value is None and actual_power.get(name, {}).get('current') != value)):
                    raise RuntimeError(tr('combination.power_overridden'))
                # 状态判断必须使用硬件接口，不能依赖任一种语言的提示文本。
                queued = queued or queued_value is not None
            actual_fans = self.fans.read(mode) if entry['fans'] else {}
            if any(actual_fans.get(name) != curve for name, curve in entry['fans'].items()):
                raise RuntimeError(tr('combination.fan_overridden'))
            check()
        except Exception as error:
            # 供电或模式变化后，原值属于旧组合，不能恢复到新的硬件上下文。
            # 同一上下文内按写入的逆序恢复，并分别报告不能确认的恢复结果。
            if self.backend.combination_context() != context:
                raise RuntimeError(tr('combination.aborted_context', error=error)) from error
            failures = []
            for name in reversed(touched_fans):
                try:
                    check()
                    failures.extend(self.fans.restore(mode, {name: before_fans[name]}))
                except Exception as recovery:
                    failures.append(str(recovery))
            for name in reversed(touched_power):
                try:
                    check()
                    self.power.write(name, pending[name] if pending[name] is not None else attributes[name]['current'])
                    actual = self.power.read_attributes().get(name)
                    if not actual or actual['current'] != attributes[name]['current'] or self.power.pending(name) != pending[name]:
                        raise RuntimeError(tr('recovery.readback_mismatch'))
                except Exception as recovery:
                    failures.append(f'{name}: {recovery}')
            if switched:
                try:
                    check()
                    self.power.set_enabled(False)
                except Exception as recovery:
                    failures.append(str(recovery))
            detail = tr('recovery.unconfirmed_prefix') + ' / '.join(failures) if failures else tr('recovery.restored_suffix')
            raise RuntimeError(tr('combination.apply_failed', error=error, detail=detail)) from error
        label = {'quiet': tr('mode.quiet'), 'balanced': tr('mode.balanced'), 'performance': tr('mode.performance')}[mode]
        label += tr('combination.ac_suffix') if source == 'ac' else tr('combination.battery_suffix')
        if gpu_block:
            status = tr('combination.applied', label=label) if entry['power'] or entry['fans'] else tr('combination.skipped', label=label)
            return tr('combination.with_gpu_reason', status=status, gpu_block=gpu_block)
        return tr('combination.applied', label=label) + (tr('combination.queued_suffix') if queued else '')
