# 界面依赖与许可证

桌面入口使用 GTK 4 和 libadwaita。Ubuntu 发行版包的版权清单将两者主要代码标为 LGPL-2.1-or-later。本项目使用系统动态库，应用许可证仍为 GPL-3.0-only。PyGObject 提供 Python 绑定，图表使用 Cairo，托盘通过独立进程调用系统的 Ayatana AppIndicator。

当前桌面界面使用以下系统依赖。

| 组件 | 用途 | 许可证 / 说明 |
|---|---|---|
| CPython | Python 运行时 | PSF 许可，保留发行版内的许可声明 |
| GTK 4 / libadwaita | 主窗口及原生控件 | LGPL-2.1-or-later；使用系统动态库 |
| PyGObject | GI 绑定及 GLib 事件循环 | LGPL-2.1-or-later；使用发行版包 |
| Cairo / Pycairo | 图表绘制 | 保留发行版内的版权及许可声明 |
| GTK 3 / Ayatana AppIndicator | 独立进程中的系统托盘 | 使用系统动态库，保留发行版版权及许可声明 |
| Xft、Fontconfig | Linux 平滑字体与系统字体发现 | MIT 风格许可；使用发行版现有库 |
| FreeType | 字形栅格化，由系统字体栈使用 | 双许可，可按宽松的 FreeType License 使用；保留相应声明 |
| Noto CJK | 中文轮廓字体 | SIL Open Font License 1.1；使用系统字体，本项目未复制分发字体文件 |

本项目不捆绑上述运行库。若打包分发系统运行库，应随包保留版权与许可证文件，并检查所选发行版包的实际依赖。

上述为界面依赖选型记录。本项目自身当前为 GPL-3.0-only，系统 asusctl 为独立进程；本次调整没有改变本项目许可证。
