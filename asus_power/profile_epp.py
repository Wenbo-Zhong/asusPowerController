"""ROG Control Center style platform-profile EPP settings through asusd D-Bus."""
import json
import subprocess

EPP = ('default', 'performance', 'balance_performance', 'balance_power', 'power')
PROPERTIES = {'quiet': 'ProfileQuietEpp', 'balanced': 'ProfileBalancedEpp',
              'performance': 'ProfilePerformanceEpp'}
LINK = 'PlatformProfileLinkedEpp'
SERVICE = 'xyz.ljones.Asusd'
OBJECT = '/xyz/ljones'
INTERFACE = 'xyz.ljones.Platform'


class ProfileEpp:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def command(self, *args):
        result = self.runner(['busctl', '--system', '--json=short', *args],
                             capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError((result.stderr or 'asusd EPP 接口调用失败').strip())
        return result.stdout

    def read(self):
        result = json.loads(self.command('call', SERVICE, OBJECT, 'org.freedesktop.DBus.Properties',
                                         'GetAll', 's', INTERFACE))
        properties = result['data'][0]
        linked = properties[LINK]
        if linked['type'] != 'b' or not isinstance(linked['data'], bool):
            raise ValueError('asusd EPP 联动属性格式无效')
        profiles = {}
        for profile, name in PROPERTIES.items():
            value = properties[name]
            index = value['data']
            if value['type'] != 'u' or type(index) is not int or not 0 <= index < len(EPP):
                raise ValueError('asusd EPP 枚举版本不兼容')
            profiles[profile] = EPP[index]
        return {'supported': True, 'enabled': linked['data'], 'profiles': profiles,
                'choices': list(EPP)}

    def status(self):
        try:
            status = self.read()
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as error:
            return {'supported': False, 'reason': str(error)}
        try:
            service = self.runner(['systemctl', 'is-active', 'power-profiles-daemon.service'],
                                  capture_output=True, text=True, timeout=3)
            status['ppd_active'] = service.returncode == 0 and service.stdout.strip() == 'active'
        except (OSError, subprocess.SubprocessError):
            status['ppd_active'] = None
        return status

    def set_property(self, name, value):
        signature = 'b' if name == LINK else 'u'
        encoded = str(value).lower() if name == LINK else str(EPP.index(value))
        self.command('set-property', SERVICE, OBJECT, INTERFACE, name, signature, encoded)

    def save(self, enabled, profiles):
        if type(enabled) is not bool or not isinstance(profiles, dict) or set(profiles) != set(PROPERTIES):
            raise ValueError('需要指定联动开关和三种方案的 EPP')
        if any(value not in EPP for value in profiles.values()):
            raise ValueError('请选择 asusd 支持的 EPP 档位')
        old = self.read()
        before = {PROPERTIES[key]: value for key, value in old['profiles'].items()}
        before[LINK] = old['enabled']
        desired = {PROPERTIES[key]: value for key, value in profiles.items()}
        desired[LINK] = enabled
        changed = []
        try:
            for name, value in desired.items():
                if value == before[name]:
                    continue
                changed.append(name)
                self.set_property(name, value)
            actual = self.read()
            if actual['enabled'] != enabled or actual['profiles'] != profiles:
                raise RuntimeError('asusd EPP 规则回读不一致')
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as error:
            failures = []
            for name in reversed(changed):
                try:
                    current = self.read()
                    value = current['enabled'] if name == LINK else current['profiles'][next(
                        profile for profile, prop in PROPERTIES.items() if prop == name)]
                    if value == desired[name]:
                        self.set_property(name, before[name])
                except Exception as recovery:
                    failures.append(str(recovery))
            detail = '；部分恢复失败：' + ' / '.join(failures) if failures else '；已尝试恢复原规则'
            raise RuntimeError(f'EPP 规则保存失败：{error}{detail}') from error
        return 'EPP 方案规则已保存到 asusd，方案切换时由后台服务应用'
