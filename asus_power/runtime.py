"""Optional project-local Ubuntu Tk runtime; no downloads during application startup."""
import os
from pathlib import Path
import sys
import subprocess


def smooth_environment(root=None, environ=None):
    root = Path(root or os.environ.get('ASUS_POWER_TK_ROOT',
                Path(__file__).resolve().parent.parent / '.runtime/smooth-tk')).resolve()
    python = Path('/usr/bin/python3')
    if not sys.platform.startswith('linux') or not python.is_file() or not root.is_dir():
        return None
    libraries = sorted(root.glob('usr/lib/*/libtk8.6.so'))
    try:
        suffix = subprocess.check_output([str(python), '-E', '-S', '-c',
            'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))'],
            text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    extensions = sorted(root.glob(f'usr/lib/python*/lib-dynload/_tkinter{suffix}'))
    scripts = root / 'usr/share/tcltk/tk8.6'
    if not libraries or not extensions or not scripts.is_dir():
        return None
    env = dict(os.environ if environ is None else environ)
    env['PYTHONPATH'] = os.pathsep.join([str(extensions[0].parent.parent),
                                       str(extensions[0].parent), env.get('PYTHONPATH', '')])
    env['LD_LIBRARY_PATH'] = os.pathsep.join([str(libraries[0].parent), env.get('LD_LIBRARY_PATH', '')])
    env['TK_LIBRARY'] = str(scripts)
    # A Conda TCL_LIBRARY can point at incompatible scripts for the system Tcl.
    env.pop('TCL_LIBRARY', None)
    env.pop('PYTHONHOME', None)
    env['ASUS_POWER_SMOOTH_TK'] = '1'
    return str(python), env


def ensure_smooth_tk(args=None):
    if os.environ.get('ASUS_POWER_SMOOTH_TK') == '1':
        return
    runtime = smooth_environment()
    if runtime:
        python, env = runtime
        os.execve(python, [python, *(args if args is not None else ['-m', 'asus_power', *sys.argv[1:]])], env)
