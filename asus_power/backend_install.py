"""Install the administrator-owned backend package shipped with the Ubuntu DEB."""
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


def install():
    if not available():
        raise RuntimeError('未找到随界面包安装的后端')
    command = [str(INSTALLER)]
    if os.geteuid() != 0:
        pkexec = shutil.which('pkexec')
        if not pkexec:
            raise RuntimeError('系统没有可用的管理员授权工具')
        command[:0] = [pkexec, '--disable-internal-agent']
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode == 126:
        return '已取消安装后端服务'
    if result.returncode:
        lines = (result.stderr or result.stdout).splitlines()
        raise RuntimeError('\n'.join(lines[-6:]) or f'安装程序退出码 {result.returncode}')
    return '后端服务已安装并启动。请退出托盘中的应用后重新打开，以重新检测硬件。'
