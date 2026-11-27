"""构建时把 JSON 译文填入桌面模板；DEB/RPM 与用户自启动共享语言资源。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asus_power.i18n import desktop_entries


def main():
    source, destination = map(Path, sys.argv[1:])
    text = source.read_text(encoding='utf-8')
    entries = desktop_entries() + desktop_entries('Comment', 'desktop.comment')
    destination.write_text(text.replace('[Desktop Entry]\n', '[Desktop Entry]\n' + entries, 1),
                           encoding='utf-8')


if __name__ == '__main__':
    main()
