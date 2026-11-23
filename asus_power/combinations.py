"""Six independent power-source/profile configurations and transactional apply."""
from copy import deepcopy
import json
from .fan_curves import validate as validate_curve, FanCurves
from .power_limits import ATTRIBUTES, PowerLimits

MODES = ('quiet', 'balanced', 'performance')
SOURCES = ('ac', 'battery')


def defaults():
    return {'enabled': False,
            'entries': {mode: {source: {'power': {}, 'fans': {}}
                               for source in SOURCES} for mode in MODES}}


def validate(settings):
    if not isinstance(settings, dict) or type(settings.get('enabled')) is not bool:
        raise ValueError('无效的组合策略开关')
    entries = settings.get('entries')
    if not isinstance(entries, dict) or set(entries) != set(MODES):
        raise ValueError('需要省电、均衡、性能三种方案')
    for sources in entries.values():
        if not isinstance(sources, dict) or set(sources) != set(SOURCES):
            raise ValueError('每种方案需要插电和离电两套配置')
        for entry in sources.values():
            if not isinstance(entry, dict) or set(entry) != {'power', 'fans'}:
                raise ValueError('组合需要功耗和风扇 设置')
            power, fans = entry['power'], entry['fans']
            if not isinstance(power, dict) or any(name not in ATTRIBUTES or type(value) is not int or value < 0 for name, value in power.items()):
                raise ValueError('功耗配置含不支持的属性或数值')
            if not isinstance(fans, dict):
                raise ValueError('风扇配置无效')
            for name, curve in fans.items():
                if not isinstance(curve, dict) or name != curve.get('fan'):
                    raise ValueError('风扇配置名称不一致')
                validate_curve(curve)


def normalized(value):
    if value is None:
        return defaults()
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
    """Apply once per real context/configuration change, not every telemetry tick."""
    def __init__(self):
        self.previous = None

    def request(self, state, settings):
        if not settings.get('enabled') or state.get('ac') is None or state.get('profile') not in MODES:
            self.previous = None
            return None
        mode, source = state['profile'], 'ac' if state['ac'] else 'battery'
        entry = effective(settings, mode, source)
        fingerprint = (mode, source, json.dumps(entry, sort_keys=True))
        if fingerprint == self.previous:
            return None
        self.previous = fingerprint
        return mode, source, entry


class CombinationApplier:
    def __init__(self, backend):
        self.backend = backend
        self.power = PowerLimits(backend.root, backend.runner)
        self.fans = FanCurves(backend.runner)

    def apply(self, mode, source, entry):
        # Validate the entire payload before any write, including hardware ranges.
        if mode not in MODES or source not in SOURCES:
            raise ValueError('不支持的运行方式或供电类型')
        probe = defaults()
        probe['entries'][mode][source] = deepcopy(entry)
        validate(probe)
        context = self.backend.combination_context()
        if context != {'profile': mode, 'ac': source == 'ac'}:
            raise RuntimeError('当前运行方式或供电与所选组合不一致，请切换后再应用')

        def check():
            if self.backend.combination_context() != context:
                raise RuntimeError('应用期间切换了电源或运行方式，请等待下一次状态刷新')

        attributes = self.power.read_attributes() if entry['power'] else {}
        for name, value in entry['power'].items():
            data = attributes.get(name)
            if data is None or not data['minimum'] <= value <= data['maximum'] or (value - data['minimum']) % data['step']:
                raise ValueError(f'{ATTRIBUTES[name]} 不符合当前固件范围或步长，请重新读取')
        before_fans = self.fans.read(mode) if entry['fans'] else {}
        if not set(entry['fans']).issubset(before_fans):
            raise ValueError('当前方案不支持保存的风扇')
        enabled = self.power.enabled() if entry['power'] else None
        pending = {name: self.power.pending(name) for name in attributes}
        touched_power, touched_fans = [], []
        switched = False
        messages = []
        try:
            check()
            if entry['power'] and not enabled:
                switched = True
                touched_power.extend(attributes)
                self.power.set_enabled(True)
                # Enabling the firmware group can also load unrelated saved
                # values. Preserve unselected parameters across that operation.
                after_enable = self.power.read_attributes()
                for name in attributes.keys() - entry['power'].keys():
                    check()
                    desired = pending[name] if pending[name] is not None else attributes[name]['current']
                    if (after_enable.get(name, {}).get('current') != attributes[name]['current']
                            or self.power.pending(name) != pending[name]):
                        messages.append(self.power.apply(name, desired))
            for name, value in entry['power'].items():
                check()
                if name not in touched_power:
                    touched_power.append(name)
                messages.append(self.power.apply(name, value))
                check()
            for name, curve in entry['fans'].items():
                check()
                touched_fans.append(name)
                self.fans.save(mode, curve)
                check()
            # Verify early writes again, catching asynchronous service overrides.
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
                    raise RuntimeError('组合应用后功耗被外部服务修改，请检查电源管理设置')
            actual_fans = self.fans.read(mode) if entry['fans'] else {}
            if any(actual_fans.get(name) != curve for name, curve in entry['fans'].items()):
                raise RuntimeError('组合应用后风扇曲线被外部服务修改')
            check()
        except Exception as error:
            # Never restore the old source's power values into the new source.
            if self.backend.combination_context() != context:
                raise RuntimeError(f'组合未完成：{error}；供电或方案已改变，未向新组合写回旧值') from error
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
                        raise RuntimeError('恢复回读不一致')
                except Exception as recovery:
                    failures.append(f'{name}: {recovery}')
            if switched:
                try:
                    check()
                    self.power.set_enabled(False)
                except Exception as recovery:
                    failures.append(str(recovery))
            detail = '；恢复未确认：' + ' / '.join(failures) if failures else '；已恢复原设置'
            raise RuntimeError(f'组合应用失败：{error}{detail}') from error
        label = {'quiet': '省电', 'balanced': '均衡', 'performance': '性能'}[mode]
        label += ' · 插电' if source == 'ac' else ' · 离电'
        queued = any('排队' in message for message in messages)
        return f'已应用 {label}' + ('；独显功耗已排队，唤醒后生效' if queued else '')
