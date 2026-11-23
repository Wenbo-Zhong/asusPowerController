# ASUS Power Controller

为华硕笔记本打造的轻量 Linux 桌面控制器：借鉴 G-Helper 的紧凑操作与供电策略，结合 ROG Control Center 的能力检测与 asusd 后端。

**当前版本：0.1.0 / Linux MVP。** 可运行，不是两个上游项目的完整替代品。详细功能对比、源码参考和局限见 [分析文档](docs/COMPARISON.md)。

## 运行

桌面界面使用 GTK 4、libadwaita 1.4+、PyGObject 和 Cairo。Ubuntu 可安装 `python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libayatana-appindicator3-1`。硬件设置仍通过适配发行版的 asusctl/asusd 执行。

在本目录执行：

```bash
python3 -m asus_power
```

只读诊断：

```bash
python3 -m asus_power --diagnose
```

可选安装为命令：

```bash
python3 -m pip install --user .
asus-power-controller
```

## DEB 和 RPM

GitHub Actions 的 **Build Linux packages** 会在 PR、main/master 推送、版本标签和手动运行时生成安装包。下载对应运行记录里的 `ubuntu-26.04-deb` 或 `fedora-44-rpm` 构建产物。包版本来自 `pyproject.toml`。

- Ubuntu 26.04 可安装 Actions 生成的界面 DEB。带后端的 `amd64` 版本在「日常设置 → 启动与托盘」提供「安装后端服务」。确认授权后，应用用 APT 安装内部的 `asusctl` 包、刷新服务并启动 `asusd`。同路径的源码安装程序会由包管理器接管，`/etc/asusd` 中的配置会保留。安装成功后退出托盘并重新打开应用以刷新硬件能力。
- Fedora 44 在启用提供 `asusctl` 的 Terra 仓库后，用 `sudo dnf install ./asus-power-controller-0.1.0-1.fc44.noarch.rpm` 安装。RPM 声明了 `asusctl` 依赖。 [上游 Fedora 指南](https://opengamingcollective.github.io/asusctl/distributions/fedora.html)

更新 Ubuntu 后端时，在主分支运行 **Build asusctl backend**，输入上游的完整提交 SHA。它只编译后端程序并上传 DEB、对应源码及 Cargo.lock。成功后会自动触发 **Build Linux packages**，把同一次构建的后端包嵌进界面 DEB。只修改界面时可以手动提供 `backend_run_id`，也可以把仓库变量 `ASUSCTL_BACKEND_RUN_ID` 设为可下载的后端运行 ID，让主分支后续构建复用它。后端产物保留 90 天。未选定后端产物的 DEB 可搭配系统上已有的 asusctl/asusd。

本地构建需要 Python 3.11+、`desktop-file-utils`，以及对应平台的 `dpkg-deb` 或 `rpmbuild`。后端打包另外需要 `dpkg-dev`，并复用指定上游目录里的 Rust release 编译产物与 Cargo.lock。

```bash
sh packaging/build.sh deb
sh packaging/build.sh rpm
sh packaging/build-backend.sh /path/to/asusctl dist-backend
sh packaging/build.sh deb dist dist-backend/asusctl_VERSION_amd64.deb
```

## 已实现

- 圆角卡片与控件，深浅主题跟随系统的桌面界面；异步读取和命令执行。
- 「日常设置 → 启动与托盘」可设置登录自启动。关窗后继续在托盘运行，托盘菜单可彻底退出。登录自启动使用普通用户权限。
- 自动发现性能模式，手动切换并回读实际状态。
- [六套组合策略](docs/COMBINATIONS.md)：省电／均衡／性能 × 插电／离电，独立保存功耗和风扇设置，程序运行时自动联动，包括收进托盘后。
- 发现电池及 USB-C/交流供电，设置充电上限并回读。
- 「电池信息」页显示健康度、充放电功率、按当前充电上限估算的时间、满充／设计容量、循环次数及电池型号；兼容只提供电流和电荷量的电池。
- 每 3 秒读取温度、风扇转速、电量及可用电池功率；不调用 nvidia-smi 唤醒独显。
- 可保存的接电 / 电池性能策略，默认关闭，在程序运行期间生效，包括收进托盘后。手动切换模式会禁用此策略。
- 支持时可调节键盘亮度、编辑并启用 8 点风扇曲线。
- 设备支持信息与错误显示。不支持的控制灰显；未知遥测显示 `—`。

配置位于 `$XDG_CONFIG_HOME/asus-power-controller/settings.json`，默认 `~/.config/asus-power-controller/settings.json`。若已保存启用的自动策略，下次启动会恢复并应用它。退出后停止监测和策略，但已写入硬件/asusd 的设置不会自动撤销。

## 本机验证与限制

在 ProArt P16 H7606WI、Linux 7.0、asusctl 6.3.11 上验证了设备探测及 GUI 启动。检测到 quiet / balanced / performance、充电上限、键盘背光和三路风扇转速；现已通过 asusd D-Bus 读出 CPU、GPU 和第三风扇的曲线。写入与恢复默认尚未进行真实硬件验收。

暂无 Windows 后端、GPU 切换、RGB 动画或热键。仅支持已检测到现代子命令结构的 asusctl，未完成跨版本验收。asusd 的自身自动策略可能与本项目策略相互覆盖。CLI 通过系统现有 D-Bus 授权操作，权限不足会显示原始错误；应用默认以普通用户启动，安装或执行核显频率控制时按需授权。「启动与托盘」可选择从桌面快捷方式打开时提前授权，该偏好在更改时保存。登录自启仍以普通用户运行。

风扇编辑器读取 asusd 保存的真实曲线，支持按方案选择 CPU、GPU、第三风扇，拖动 8 个点或在表格中输入数值。重复温度点及原始 0–255 PWM 字节会保留，界面以百分比显示。启用状态和曲线通过单个 SetFanCurve 调用提交并回读验证。失败时尝试恢复原曲线，恢复失败会显示错误。未应用的编辑在切换前会提示确认。

「恢复此方案默认」影响所选方案的全部风扇，确认后调用 asusd 的 SetCurvesToDefaults 并重新读取。该操作可能由 asusd 暂时切换硬件方案以读取原厂曲线。百分比表示风扇输出比例，实际转速显示在实时 RPM 栏。

实现参考 ROG Control Center 的 [setup_fans.rs](https://github.com/OpenGamingCollective/asusctl/blob/main/rog-control-center/src/ui/setup_fans.rs) 和 CurveData 的 D-Bus 数据格式，复用 asusd 服务接口。编辑器使用 GTK 和系统字体，跟随深浅主题及系统强调色。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q asus_power
```

测试使用临时 sysfs 和模拟命令，涵盖参数拒绝、USB 供电、读取异常、回读失败、命令错误、曲线验证、供电策略和配置恢复，不写入真实硬件。

GPL-3.0-or-later。参考项目和许可证说明见 [分析文档](docs/COMPARISON.md)。

## 显示与主题

GTK 4 使用桌面的分辨率缩放和系统字体。libadwaita 跟随系统深浅模式和支持的强调色。卡片及控件采用圆角，首页布局可在硬件检测前显示，等待读数时显示 `--`。

`scripts/with_smooth_tk.py` 仍可运行旧版 Tk 的界面回归测试，正常启动使用 GTK 4。

## 自适应 CPU EPP

监控服务每秒读取 CPU 总占用、最忙核心以及 CPU、磁盘 I/O 和内存的等待压力。日志的 `pressure` 是本次采样窗口内至少有部分任务等待资源的时间比例（百分比）。任一压力达到 2% 显示 `elevated`，达到 8% 显示 `high`。这是系统卡顿风险估计，桌面和游戏的实际帧时间需由对应应用测量。[Linux 内核 PSI 文档](https://docs.kernel.org/accounting/psi.html)

策略在 CPU 排队明显或总占用达到 70% 时立即建议性能档。持续单核满载也能升档。轻中负载使用两个均衡档，低负载持续 8 秒后回到省电档。I/O 或内存压力单独出现时只报告风险。可用档位少于四档时，使用所有 CPU 策略都支持的最接近档位。

先以前台只读方式观察 12 个样本：

```bash
python3 -m asus_power.epp --samples 12
```

`suggested_epp` 是策略建议，`applied_epp` 是已应用的档位，只观察时为 `null`。部署为 systemd 服务后默认仍然只观察：

```bash
sudo install -Dm0644 asus_power/epp.py /usr/local/lib/asus-power-controller/epp.py
sudo install -Dm0644 contrib/systemd/asus-power-epp.service /etc/systemd/system/asus-power-epp.service
sudo systemctl daemon-reload
sudo systemctl enable --now asus-power-epp.service
journalctl -u asus-power-epp.service -f
```

监控器仅提供建议。EPP 联动由 asusd 执行。

## 核显最高频率

在「散热调节」页设置 MHz 上限，页面显示当前频率、现有上限和调频模式。首次使用点击「安装核显控制组件…」完成系统管理员授权，之后点击「应用」或「恢复自动调频」时也会请求授权。

当前支持已识别的 AMD Strix Point 核显（PCI ID `1002:150e`），本机驱动提供 600–2900 MHz 范围。应用上限后使用驱动的 manual 模式，恢复自动调频会重置原厂 SCLK 范围并切回 auto。上下限以驱动实际报告为准。

组件安装在 `/usr/local/lib/asus-power-controller/igpu.py`，由 root 所有。界面通过 `pkexec /usr/bin/python3 -I` 调用这个组件。组件重新识别 PCI 设备并校验频率范围，提交后回读，提交或回读失败时尝试恢复原频率范围及模式。后台采样只读取状态，应用启动时不会自动设置核显频率。

这是频率限制，功耗仍随负载变化。本机已验证只读探测，写入、恢复与失败回滚通过模拟驱动接口验证。

## 按电源方案联动 EPP

「组合策略 → EPP」采用 ROG Control Center 的方案。通过 asusd D-Bus 的 `PlatformProfileLinkedEpp`、`ProfileQuietEpp`、`ProfileBalancedEpp`、`ProfilePerformanceEpp` 属性保存联动开关与三种方案的偏好。规则由 asusd 保存并在后台随平台方案应用，关闭本应用后仍然有效。界面显示每个 CPUFreq policy 实际报告的 EPP 和调速器，多个不同值会一起列出。选项包括固件默认和四种命名预设。

Linux 的 EPP 策略取决于驱动及系统电源管理器。驱动提供 EPP 接口，硬件按偏好和负载调节频率，power-profiles-daemon、asusd 等服务可按电源方案设置这些偏好。[内核文档](https://docs.kernel.org/admin-guide/pm/amd-pstate.html)

本机 asusd 的现有规则是静音 → power、均衡 → balance_performance、性能 → performance。power-profiles-daemon 也在运行，界面会显示提示。两个服务同时管理平台方案和 EPP 时可能互相覆盖，需要明确由哪个服务负责。[上游兼容性说明](https://github.com/OpenGamingCollective/asusctl/blob/main/docs/distributions/bazzite.md)

本次实现参考了上游 `rog-control-center/src/ui/setup_system.rs` 中 EPP 属性的读写方式，以及 `rog-platform/src/cpu.rs` 的 CPUEPP 枚举。通过现有服务接口实现，没有复制上游源码文件。真实接口读取已经验证，配置写入、失败恢复用模拟 D-Bus 验证。


## 功率设置

「功率设置」页提供处理器 SPL/PL1、SPPT/PL2、FPPT/PL3，以及设备支持的 APU/平台功率、独显 TGP 和 Dynamic Boost。每项的当前值、范围、步长和默认值均从 asus-armoury 固件属性读取。

先勾选「启用当前方案的功率调节」，再逐项填写瓦数并应用。启用开关会应用 asusd 已保存的当前性能方案和供电状态对应配置。每项可恢复固件默认值。

应用通过 asusd D-Bus 提交，随后读取驱动的 current_value 验证。独显休眠时若服务返回待应用值，界面显示「待应用」，并与实际当前值分开显示。期间切换电源或性能方案时会中止并提示重新读取，避免向新方案回写旧值。应用失败时尝试恢复原值，无法确认恢复时明确显示错误。

本机发现处理器持续功率 15–80 W、两档加速功率 35–80 W、独显 TGP 55–85 W、Dynamic Boost 5–20 W。真实接口读取已经验证，写入、恢复默认、缓存未生效和独显排队通过模拟接口验证，真实功率调整尚待硬件验收。
