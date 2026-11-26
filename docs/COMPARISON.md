# G-Helper 与 ROG Control Center：源码分析与产品取舍

分析日期：2026-10-28。此处的 ROG Control Center 指 asus-linux/asusctl 内的官方 GUI，不是同名第三方程序。

## 参考版本与入口

- G-Helper：https://github.com/seerge/g-helper ，提交 `0a1538ac4fb0337f6cf3d64a907acde10c91a3d9`，GPL-3.0。实际阅读 `app/AsusACPI.cs` 的硬件端点、`SetFanCurve`、`FixFanCurve`，以及 `app/Mode/ModeControl.cs` 的 `AutoPerformance`、`AutoFans` 和 `AutoPower`；参考 `app/Program.cs` 的组件组合方式。
- asusctl / ROG Control Center：https://gitlab.com/asus-linux/asusctl ，提交 `321b1114a0ac3782d67aa39201682effb749f4d9`，MPL-2.0。实际阅读 `rog-control-center/src/ui/setup_system.rs`、`setup_fans.rs`、`rog-platform/src/power.rs` 和 `platform.rs`。GitLab README 说明后续开发已迁移至 https://github.com/opengamingcollective/asusctl 。本次分析基于归档版本，没有把它声称为新仓库最新版本。

## 功能比较

| 领域 | G-Helper | ROG Control Center / asusctl | 本项目的取舍 |
|---|---|---|---|
| 运行架构 | C# Windows 桌面应用，经 ACPI/WMI、设备接口及厂商库控制 | Rust GUI，经 D-Bus 调用系统服务 asusd，结合内核驱动 | Python/GTK 4 桌面前端，复用已安装 asusctl/asusd，不另建特权守护进程 |
| 性能模式 | 静音、均衡、Turbo；按模式组合功耗与风扇设置 | 查询平台模式，支持按供电变化切换 | 从 platform_profile_choices 读取，不硬编码机型支持；写入后回读 |
| 自动化 | 供电联动性能、GPU、刷新率和灯光超时 | 守护进程具有供电联动，GUI 配置系统功能 | 首版仅供电联动性能模式；默认关闭，仅窗口存活时运行 |
| GPU | Eco、Standard、Ultimate、Optimized，含机型相关 MUX | 平台 MUX / dGPU 相关接口；版本与 supergfxd 集成方式不同 | 暂不实现，不能把 Windows 模式名直接映射成 Linux 操作 |
| 风扇 | 按模式编辑 CPU/GPU/第三风扇曲线，修正不规则曲线点 | 通过 FanCurvesProxy 查询各模式各风扇曲线和启用状态 | 可编辑 8 点文本曲线，校验递增温度与不减转速，按能力启用 |
| 功耗 / 温度限制 | CPU PPT、Boost，部分 GPU 调频、降压与功率控制 | 新内核 asus-armoury 固件属性及动态范围 | 尚未实现，后续必须按固件读取上下限，不使用通用机型常量 |
| 电池 | 充电上限、状态与供电策略 | 充电上限及一次充满等守护进程功能 | BAT0/BAT1 自动发现，20–100% 上限，写入回读；处理 USB-C 供电 |
| 监控 | CPU/GPU 温度、转速、电池；较新版本含游戏 OSD | 硬件状态，部分由系统接口提供 | hwmon 温度与转速、电量与可用电池功率；缺失值显示破折号 |
| 灯光 | 键盘 RGB、AniMe/Slash、外设控制 | Aura、AniMe、Slash 等独立模块，按设备支持 | 首版仅键盘亮度；RGB 和动画待实现 |
| 屏幕 | 刷新率、Overdrive、部分 Mini LED / 调光 | 支持的面板和固件属性 | 尚未实现，需分别处理 Wayland、X11 和固件能力 |
| 集成 | 轻量单文件、托盘、热键 | GUI、托盘、通知，复用系统服务 | 无额外 Python 包即可运行；托盘、热键与登录启动待实现 |

## 从代码得出的关键结论

1. **不能拼接两个 GUI 就得到跨平台控制器。** `AsusACPI.cs` 的 `ATKACPI` 和 DeviceGet/DeviceSet 是 Windows 平台路径；Linux 应优先复用内核及 asusd，而不是照抄设备编号进行未知写入。
2. **能力来自查询。** RCC 的 setup_system 首先把功能设成不可用，再按 supported_properties 启用；setup_fans 分别填充模式和风扇能力。本项目采用同一设计原则，不过首版通过 sysfs 保守发现，可能漏报某些实际可由 D-Bus 操作的设备。
3. **界面与控制分离。** 界面线程不执行外部命令。后台单线程串行处理读取和设置；子进程有超时，失败显示错误，不把命令失败显示为成功。
4. **自动策略必须可解释。** 插拔电源才执行一次，供电未知不切换；手动选择模式会关闭本项目自动策略。asusd 自身的供电策略仍可能覆盖界面设置，首版没有统一管理它的配置，应避免同时启用相互冲突的规则。
5. **风扇限制是本项目策略。** 8 点源于参考项目和驱动格式；末点至少 90°C/100% 是本项目保守约束，不代表所有机型的原厂曲线要求。写入曲线和启用是两个命令，不具备事务性；第二步失败时第一步可能已保存，界面报告失败，尚无自动回滚。

## 首版边界及后续工作

首版是可运行的 Linux MVP，并不等价于两个成熟项目功能的全集。真实机器已验证只读探测和 GUI；写操作以虚拟 sysfs / mock 子进程测试，尚未进行真实风扇、性能或电池写入验收。

下一阶段建议顺序：直接 D-Bus 的能力检测与事件订阅、读取已有曲线和恢复原厂配置、asus-armoury 功率范围及回滚、GPU 切换的注销/重启状态管理、托盘和单实例、Windows WMI 后端。发布前需要为各支持的 asusctl 版本验证命令兼容性；目前仅检查现代子命令结构，并非完整版本兼容保证。

## 代码与许可证

本项目是参考架构和行为后的独立实现，没有粘贴两个项目的源文件。选择 GPL-3.0-only；仓库附 GPLv3 正文。调用系统 asusctl，不捆绑它的源码或二进制。未来若复制或修改上游文件，应保留原作者、版权和许可证，并按各文件许可证履行分发要求，不能仅用本项目许可证替换 MPL 文件头。

## 电池信息功能补充（2026-11-14）

本次实际阅读本地 asusctl 源码提交 `61c39ffd855690cf92ded455beedea800dd40e95` 的：

- `rog-control-center/ui/pages/battery.slint`：充电上限、健康度、状态、功率和预计时间。
- `rog-control-center/src/ui/setup_system.rs`：后台采样和界面数据更新。
- `rog-platform/src/power.rs`：Wh/Ah 两类传感器、功率换算、容量比例及按充电上限计算的时间估算。

在本项目中独立实现 `asus_power/hardware/battery.py`，当前界面由 `asus_power/ui/battery_page.py` 显示，没有复制上游源文件或引入其 GUI 框架。「电池信息」页使用系统主题；由后台工作线程采样。充电上限与日常设置共用输入值，并继续通过 asusctl 接口保存、回读。

本实现同时显示循环次数、型号、制造商、电池类型、电压、电流、满充和设计容量。健康度只用同一单位的一对容量计算；功率优先读 `power_now`，否则用 `current_now × voltage_now`。Ah 转 Wh 优先用设计电压，界面注明折算；缺失、不合法、非有限数值显示 `—`，不会误报为 0。外设电池不作为笔记本电池，采样会重新发现电池和供电设备。

预计充电时间以实际充电上限为目标，放电时间按 Wh/W 或 Ah/A 估算；已知功率低于 0.1 W、暂停充电、已充满或数据不足时不提供估计。缺少电压时仍可按有效电荷和电流计算时间。时间会随负载变化，不是续航承诺。本机已只读验证 BAT1 的电荷型遥测；本次未修改真实充电上限。
