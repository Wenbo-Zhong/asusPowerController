"""UI-independent editing state. Only explicit saves update persisted settings."""
from copy import deepcopy

from ..combinations import MODES, SOURCES, normalized
from ..hardware.fan_curves import validate as validate_curve


def pwm_percent(value):
    return round(value * 100 / 255, 1)


class CurveDraft:
    def __init__(self):
        self.curves = {}
        self.active = None

    def load(self, curves):
        self.curves = deepcopy(curves)
        if self.active not in self.curves:
            self.active = next(iter(self.curves), None)

    def select(self, key):
        if key not in self.curves:
            raise ValueError('请先读取方案曲线')
        self.active = key

    def edit(self, temperatures, percentages, enabled):
        if self.active is None:
            raise ValueError('请先读取方案曲线')
        original = self.curves[self.active]
        if len(temperatures) != 8 or len(percentages) != 8:
            raise ValueError('需要 8 个温度和风扇输出点')
        curve = {'fan': self.active, 'temp': list(temperatures), 'pwm': [],
                 'enabled': enabled}
        for old, percent in zip(original['pwm'], percentages):
            # Display rounding must not change any of the 256 original PWM values.
            curve['pwm'].append(old if abs(percent - pwm_percent(old)) < .0001
                                else round(percent * 255 / 100))
        validate_curve(curve)
        self.curves[self.active] = curve

    def export(self, managed):
        if not managed:
            return {}
        if self.active is None:
            raise ValueError('请先读取方案曲线')
        return deepcopy(self.curves)


class CombinationDraft:
    def __init__(self, settings):
        self.settings = deepcopy(settings)
        self.selected = ('balanced', 'ac')
        self.templates = {}

    @property
    def entry(self):
        mode, source = self.selected
        return self.settings['entries'][mode][source]

    def select(self, mode, source):
        if mode not in MODES or source not in SOURCES:
            raise ValueError('不支持的运行方式或供电类型')
        self.selected = mode, source

    def collect(self, power_values, fans, enabled):
        entry = deepcopy(self.entry)
        for key, value in power_values.items():
            if value is None:
                entry['power'].pop(key, None)
            else:
                entry['power'][key] = value
        entry['fans'] = deepcopy(fans)
        mode, source = self.selected
        self.settings['entries'][mode][source] = entry
        self.settings['enabled'] = enabled

    def curves(self):
        return self.entry['fans'] or self.templates.get(self.selected[0], {})

    def replace_fans(self, selected, curves):
        mode, source = selected
        self.settings['entries'][mode][source]['fans'] = deepcopy(curves)

    def other_supply(self):
        mode, source = self.selected
        return mode, 'battery' if source == 'ac' else 'ac'

    def export(self):
        return normalized(self.settings)
