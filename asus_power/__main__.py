"""命令行入口：诊断不加载 GTK；桌面启动必要时转交系统 Python。"""
from .i18n import tr
import argparse
import json
from .backend import LinuxBackend


def gtk_fallback_command(system_python, arguments):
    from pathlib import Path
    package_root = str(Path(__file__).resolve().parent.parent)
    bootstrap = ('import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); '
                 'runpy.run_module("asus_power",run_name="__main__",alter_sys=True)')
    return [system_python, '-I', '-c', bootstrap, package_root, *arguments]


def main():
    parser = argparse.ArgumentParser(description=tr('app.name'))
    parser.add_argument('--diagnose', action='store_true', help=tr('cli.diagnose_help'))
    parser.add_argument('--start-hidden', action='store_true', help=tr('cli.hidden_help'))
    args = parser.parse_args()
    if args.diagnose:
        backend = LinuxBackend()
        print(json.dumps({'capabilities': backend.capabilities(), 'state': backend.snapshot()}, ensure_ascii=False, indent=2))
    else:
        import os
        import sys
        # 使用 Cairo 绘制，避免界面初始化打开独显或外接显卡节点。
        os.environ['GSK_RENDERER'] = 'cairo'
        try:
            from .ui.controller import launch
        except (ImportError, ValueError) as error:
            # Conda 等 Python 环境可能缺少发行版 GI，改用系统解释器并保留包路径。
            system_python = '/usr/bin/python3'
            if os.path.realpath(sys.executable) != os.path.realpath(system_python) and os.path.isfile(system_python):
                os.execv(system_python, gtk_fallback_command(system_python, sys.argv[1:]))
            parser.exit(1, tr('cli.gtk_required', error=error))
        launch(start_hidden=args.start_hidden)


if __name__ == '__main__':
    main()
