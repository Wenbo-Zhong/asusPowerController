"""通过 asusd 无损读写风扇曲线；保留 0–255 PWM、重复温度点和启用状态。"""
from ..i18n import tr
import json
import subprocess
from .bus import BusClient, OBJECT, SERVICE

PROFILE_IDS = {'balanced': 0, 'performance': 1, 'quiet': 2, 'low-power': 3, 'custom': 4}
FAN_IDS = {'cpu': 'CPU', 'gpu': 'GPU', 'mid': 'MID'}


def validate(curve):
    if curve.get('fan') not in FAN_IDS or type(curve.get('enabled')) is not bool:
        raise ValueError(tr('fan.invalid_state'))
    for field, upper in [('temp', 100), ('pwm', 255)]:
        values = curve.get(field, [])
        if not isinstance(values, (list, tuple)) or len(values) != 8 or any(type(v) is not int or not 0 <= v <= upper for v in values):
            raise ValueError(tr('fan.required_points'))
        if any(a > b for a, b in zip(values, values[1:])):
            raise ValueError(tr('fan.non_monotonic'))


class FanCurves:
    def __init__(self, runner=subprocess.run):
        self.bus = BusClient(runner, timeout=20, error=tr('fan.call_failed'))

    def call(self, method, signature, *arguments):
        return self.bus.command('call', SERVICE, OBJECT, 'xyz.ljones.FanCurves',
                                method, signature, *arguments)

    def read(self, profile):
        if profile not in PROFILE_IDS:
            raise ValueError(tr('fan.profile_unsupported'))
        result = json.loads(self.call('FanCurveData', 'u', PROFILE_IDS[profile]))
        if result['type'] != 'a(s(yyyyyyyy)(yyyyyyyy)b)':
            raise ValueError(tr('fan.interface_incompatible'))
        curves = {}
        for name, pwm, temp, enabled in result['data'][0]:
            fan = name.lower()
            curve = {'fan': fan, 'pwm': pwm, 'temp': temp, 'enabled': enabled}
            validate(curve)
            curves[fan] = curve
        return curves

    def status(self, profiles):
        supported = [profile for profile in profiles if profile in PROFILE_IDS]
        try:
            if not supported:
                raise ValueError(tr('fan.no_profiles'))
            profile = 'balanced' if 'balanced' in supported else supported[0]
            curves = self.read(profile)
            if not curves:
                raise ValueError(tr('fan.none_returned'))
            return {'supported': True, 'profiles': supported, 'initial_profile': profile, 'curves': curves}
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as error:
            return {'supported': False, 'reason': str(error)}

    def write(self, profile, curve):
        validate(curve)
        self.call('SetFanCurve', 'u(s(yyyyyyyy)(yyyyyyyy)b)', PROFILE_IDS[profile],
                  FAN_IDS[curve['fan']], *curve['pwm'], *curve['temp'], str(curve['enabled']).lower())

    def restore(self, profile, before):
        errors = []
        for fan, curve in before.items():
            try:
                if self.read(profile).get(fan) != curve:
                    self.write(profile, curve)
                if self.read(profile).get(fan) != curve:
                    raise RuntimeError(tr('fan.recovery_readback', fan=fan))
            except Exception as error:
                errors.append(str(error))
        return errors

    def save(self, profile, curve):
        validate(curve)
        before = self.read(profile)
        if curve['fan'] not in before:
            raise ValueError(tr('fan.unsupported'))
        try:
            self.write(profile, curve)
            actual = self.read(profile)
            if actual.get(curve['fan']) != curve:
                raise RuntimeError(tr('fan.readback_mismatch'))
            return actual
        except Exception as error:
            failures = self.restore(profile, {curve['fan']: before[curve['fan']]})
            detail = tr('recovery.failed_prefix') + ' / '.join(failures) if failures else tr('fan.restored_suffix')
            raise RuntimeError(tr('fan.save_failed', error=error, detail=detail)) from error

    def defaults(self, profile):
        before = self.read(profile)
        try:
            self.call('SetCurvesToDefaults', 'u', PROFILE_IDS[profile])
            actual = self.read(profile)
            if set(actual) != set(before):
                raise RuntimeError(tr('fan.reset_list_mismatch'))
            return actual
        except Exception as error:
            failures = self.restore(profile, before)
            detail = tr('recovery.failed_prefix') + ' / '.join(failures) if failures else tr('fan.restored_suffix')
            raise RuntimeError(tr('fan.reset_failed', error=error, detail=detail)) from error
