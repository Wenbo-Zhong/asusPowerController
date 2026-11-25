import json
import os
import pwd
from pathlib import Path


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'asus-power-controller/settings.json'


def load(path=None):
    try:
        value = json.loads((path or config_path()).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save(value, path=None):
    path = path or config_path()
    directory_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    # The authenticated desktop launcher shares the initiating user's settings.
    # Keep new files writable when that user later launches without elevation.
    if os.geteuid() == 0 and os.environ.get('PKEXEC_UID'):
        user = pwd.getpwuid(int(os.environ['PKEXEC_UID']))
        os.chown(temp, user.pw_uid, user.pw_gid)
        if not directory_existed:
            os.chown(path.parent, user.pw_uid, user.pw_gid)
    temp.replace(path)


class Policy:
    """Apply once per power transition; retry only after a new transition."""
    def __init__(self):
        self.previous = None

    def target(self, ac, settings):
        if ac is None:
            return None
        previous, self.previous = self.previous, ac
        if previous is None or ac == previous or not settings.get('enabled'):
            return None
        return settings.get('ac' if ac else 'battery')
