#!/usr/bin/env python3
"""Download/extract distro Tk into this checkout; never installs system packages."""
from pathlib import Path
import subprocess
import sys

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
from asus_power.runtime import smooth_environment


def main():
    version = subprocess.check_output(['/usr/bin/python3', '-c',
        'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], text=True).strip()
    root = project / '.runtime/smooth-tk'
    downloads = project / '.runtime/downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    # Versioned module package follows the system Python ABI; Qt is not involved.
    for package in (f'python{version}-tk', 'libtk8.6'):
        subprocess.run(['apt-get', 'download', package], cwd=downloads, check=True)
        for archive in downloads.glob(package + '_*.deb'):
            subprocess.run(['dpkg-deb', '-x', str(archive), str(root)], check=True)
    runtime = smooth_environment(root)
    if runtime is None:
        raise RuntimeError('未找到完整的 Tk 运行库')
    python, env = runtime
    subprocess.run([python, '-c', 'import tkinter as tk; from tkinter import font; '
        'r=tk.Tk(); r.withdraw(); '
        'assert "Noto Sans CJK SC" in font.families(r), "请安装 fonts-noto-cjk"; '
        'print("平滑中文字体就绪：Noto Sans CJK SC / Tk", r.tk.call("info", "patchlevel")); '
        'r.destroy()'], env=env, check=True)
    print('启动：python3 -m asus_power')


if __name__ == '__main__':
    main()
