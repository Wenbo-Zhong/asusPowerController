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
    parser = argparse.ArgumentParser(description='ASUS Power Controller')
    parser.add_argument('--diagnose', action='store_true', help='只读输出设备能力和实时状态')
    parser.add_argument('--start-hidden', action='store_true', help='启动到系统托盘')
    args = parser.parse_args()
    if args.diagnose:
        backend = LinuxBackend()
        print(json.dumps({'capabilities': backend.capabilities(), 'state': backend.snapshot()}, ensure_ascii=False, indent=2))
    else:
        import os
        import sys
        # Draw the control panel without opening discrete or external GPU nodes.
        os.environ['GSK_RENDERER'] = 'cairo'
        try:
            from .ui.controller import launch
        except (ImportError, ValueError) as error:
            # Conda Python often lacks distribution GI bindings.
            system_python = '/usr/bin/python3'
            if os.path.realpath(sys.executable) != os.path.realpath(system_python) and os.path.isfile(system_python):
                os.execv(system_python, gtk_fallback_command(system_python, sys.argv[1:]))
            parser.exit(1, f'需要 GTK 4、libadwaita 和 PyGObject：{error}\n')
        launch(start_hidden=args.start_hidden)


if __name__ == '__main__':
    main()
