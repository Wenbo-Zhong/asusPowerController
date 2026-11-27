"""安装 Ubuntu 界面包附带的后端；仅执行经过所有权检查的固定安装器。"""
from .i18n import tr
from pathlib import Path
import os
import shutil
import subprocess

PACKAGE_DIR = Path('/usr/lib/asus-power-controller')
PAYLOAD = PACKAGE_DIR / 'backend/asusctl.deb'
INSTALLER = PACKAGE_DIR / 'install-backend'


def available():
    try:
        for file in (PAYLOAD, INSTALLER):
            if not file.is_file():
                return False
            for path in (file, *file.parents):
                stat = path.lstat()
                if path.is_symlink() or stat.st_uid != 0 or stat.st_mode & 0o022:
                    return False
        return True
    except OSError:
        return False


def service_active():
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', '--quiet', 'asusd.service'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install():
    if not available():
        raise RuntimeError(tr('backend_install.not_bundled'))
    command = [str(INSTALLER)]
    if os.geteuid() != 0:
        pkexec = shutil.which('pkexec')
        if not pkexec:
            raise RuntimeError(tr('backend_install.no_authorizer'))
        command[:0] = [pkexec, '--disable-internal-agent']
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode == 126:
        return tr('backend_install.cancelled')
    if result.returncode:
        lines = (result.stderr or result.stdout).splitlines()
        raise RuntimeError('\n'.join(lines[-6:]) or tr('backend_install.exit_code', returncode=result.returncode))
    return tr('backend_install.completed')
