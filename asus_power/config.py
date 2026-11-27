"""读写用户配置；提权后仍保持原用户所有权，使用原子替换避免半写入。"""
from .i18n import tr
import json
import os
import pwd
import secrets
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
    path = Path(path or config_path())
    data = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        path.parent.mkdir(parents=True, mode=0o700)
        created_directory = True
    except FileExistsError:
        created_directory = False
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = None
    try:
        user = None
        if os.geteuid() == 0 and os.environ.get('PKEXEC_UID'):
            user = pwd.getpwuid(int(os.environ['PKEXEC_UID']))
            if created_directory and os.fstat(directory).st_uid == 0:
                os.fchown(directory, user.pw_uid, user.pw_gid)
            if os.fstat(directory).st_uid != user.pw_uid:
                raise PermissionError(tr('config.wrong_owner'))
        name = f'.{path.name}.{secrets.token_hex(16)}.tmp'
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        temp = name
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(data)
            output.flush()
            if user:
                os.fchown(output.fileno(), user.pw_uid, user.pw_gid)
            os.fsync(output.fileno())
        os.replace(temp, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        temp = None
    finally:
        try:
            if temp is not None:
                os.unlink(temp, dir_fd=directory)
        finally:
            os.close(directory)


class Policy:
    """每次供电变化只触发一次；下一次变化前不重复覆盖手动选择。"""
    def __init__(self):
        self.previous = None

    def target(self, ac, settings):
        if ac is None:
            return None
        previous, self.previous = self.previous, ac
        if previous is None or ac == previous or not settings.get('enabled'):
            return None
        return settings.get('ac' if ac else 'battery')
