"""独立保存 asusd 的三套 EPP 规则；插电/离电共用，退出界面后继续生效。"""
from ..i18n import tr
import subprocess
from .bus import BusClient, OBJECT, PLATFORM as INTERFACE, SERVICE

EPP = ('default', 'performance', 'balance_performance', 'balance_power', 'power')
PROPERTIES = {'quiet': 'ProfileQuietEpp', 'balanced': 'ProfileBalancedEpp',
              'performance': 'ProfilePerformanceEpp'}
LINK = 'PlatformProfileLinkedEpp'


class ProfileEpp:
    def __init__(self, runner=subprocess.run):
        self.bus = BusClient(runner, timeout=8, error=tr('epp.call_failed'))

    def read(self):
        result = self.bus.json('call', SERVICE, OBJECT, 'org.freedesktop.DBus.Properties',
                               'GetAll', 's', INTERFACE)
        properties = result['data'][0]
        linked = properties[LINK]
        if linked['type'] != 'b' or not isinstance(linked['data'], bool):
            raise ValueError(tr('epp.invalid_link'))
        profiles = {}
        for profile, name in PROPERTIES.items():
            value = properties[name]
            index = value['data']
            if value['type'] != 'u' or type(index) is not int or not 0 <= index < len(EPP):
                raise ValueError(tr('epp.enum_incompatible'))
            profiles[profile] = EPP[index]
        return {'supported': True, 'enabled': linked['data'], 'profiles': profiles,
                'choices': list(EPP)}

    def status(self):
        try:
            status = self.read()
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as error:
            return {'supported': False, 'reason': str(error)}
        try:
            service = self.bus.runner(['systemctl', 'is-active', 'power-profiles-daemon.service'],
                                      capture_output=True, text=True, timeout=3)
            status['ppd_active'] = service.returncode == 0 and service.stdout.strip() == 'active'
        except (OSError, subprocess.SubprocessError):
            status['ppd_active'] = None
        return status

    def set_property(self, name, value):
        signature = 'b' if name == LINK else 'u'
        encoded = str(value).lower() if name == LINK else str(EPP.index(value))
        self.bus.put(OBJECT, INTERFACE, name, signature, encoded)

    def save(self, enabled, profiles):
        if type(enabled) is not bool or not isinstance(profiles, dict) or set(profiles) != set(PROPERTIES):
            raise ValueError(tr('epp.required_rules'))
        if any(value not in EPP for value in profiles.values()):
            raise ValueError(tr('epp.unsupported_level'))
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
                raise RuntimeError(tr('epp.readback_mismatch'))
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
            detail = tr('recovery.partial_prefix') + ' / '.join(failures) if failures else tr('epp.restored_suffix')
            raise RuntimeError(tr('epp.save_failed', error=error, detail=detail)) from error
        return tr('epp.saved')
