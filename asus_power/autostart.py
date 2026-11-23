"""Manage the desktop entry for login startup in the user's XDG config."""
import os
from pathlib import Path
import pwd
from tempfile import NamedTemporaryFile


def entry_path():
    return (Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
            / 'autostart' / 'asus-power-controller.desktop')


def set_enabled(enabled, path=None):
    path = path or entry_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    project = str(Path(__file__).resolve().parent.parent).replace('\\', '\\\\')
    existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    uid = os.environ.get('PKEXEC_UID')
    user = pwd.getpwuid(int(uid)) if os.geteuid() == 0 and uid else None
    if user and not existed:
        os.chown(path.parent, user.pw_uid, user.pw_gid)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(
                '[Desktop Entry]\nType=Application\nName=ASUS Power Controller\n'
                'Name[zh_CN]=电脑管家\n'
                'Exec=/usr/bin/python3 -m asus_power --start-hidden\n'
                f'Path={project}\n'
                'Icon=preferences-system-power\nStartupWMClass=Asuspowercontroller\nTerminal=false\nStartupNotify=false\n')
            stream.flush()
            if user:
                os.fchown(stream.fileno(), user.pw_uid, user.pw_gid)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
