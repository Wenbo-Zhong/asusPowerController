"""Lossless asusd fan curves, using the same D-Bus interface as ROG Control Center."""
import json
import subprocess

PROFILE_IDS = {'balanced': 0, 'performance': 1, 'quiet': 2, 'low-power': 3, 'custom': 4}
FAN_IDS = {'cpu': 'CPU', 'gpu': 'GPU', 'mid': 'MID'}


def validate(curve):
    if curve.get('fan') not in FAN_IDS or type(curve.get('enabled')) is not bool:
        raise ValueError('无效的风扇或启用状态')
    for field, upper in [('temp', 100), ('pwm', 255)]:
        values = curve.get(field, [])
        if not isinstance(values, (list, tuple)) or len(values) != 8 or any(type(v) is not int or not 0 <= v <= upper for v in values):
            raise ValueError('需要 8 个温度点（0–100°C）和风扇输出点（0–100%）')
        if any(a > b for a, b in zip(values, values[1:])):
            raise ValueError('温度和风扇输出都必须按不递减顺序排列')


class FanCurves:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def call(self, method, signature, *arguments):
        result = self.runner(['busctl', '--system', '--json=short', 'call', 'xyz.ljones.Asusd',
                              '/xyz/ljones', 'xyz.ljones.FanCurves', method, signature,
                              *map(str, arguments)], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError((result.stderr or '风扇曲线接口调用失败').strip())
        return result.stdout

    def read(self, profile):
        if profile not in PROFILE_IDS:
            raise ValueError('不支持此性能方案')
        result = json.loads(self.call('FanCurveData', 'u', PROFILE_IDS[profile]))
        if result['type'] != 'a(s(yyyyyyyy)(yyyyyyyy)b)':
            raise ValueError('风扇曲线接口版本不兼容')
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
                raise ValueError('未检测到支持的性能方案')
            profile = 'balanced' if 'balanced' in supported else supported[0]
            curves = self.read(profile)
            if not curves:
                raise ValueError('asusd 未返回可调节的风扇')
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
                    raise RuntimeError(f'{fan} 恢复后回读不一致')
            except Exception as error:
                errors.append(str(error))
        return errors

    def save(self, profile, curve):
        validate(curve)
        before = self.read(profile)
        if curve['fan'] not in before:
            raise ValueError('当前方案不支持此风扇')
        try:
            self.write(profile, curve)
            actual = self.read(profile)
            if actual.get(curve['fan']) != curve:
                raise RuntimeError('风扇曲线回读不一致')
            return actual
        except Exception as error:
            failures = self.restore(profile, {curve['fan']: before[curve['fan']]})
            detail = '；恢复失败：' + ' / '.join(failures) if failures else '；原曲线已恢复'
            raise RuntimeError(f'曲线保存失败：{error}{detail}') from error

    def defaults(self, profile):
        before = self.read(profile)
        try:
            self.call('SetCurvesToDefaults', 'u', PROFILE_IDS[profile])
            actual = self.read(profile)
            if set(actual) != set(before):
                raise RuntimeError('恢复默认后风扇列表不一致')
            return actual
        except Exception as error:
            failures = self.restore(profile, before)
            detail = '；恢复失败：' + ' / '.join(failures) if failures else '；原曲线已恢复'
            raise RuntimeError(f'恢复默认失败：{error}{detail}') from error
