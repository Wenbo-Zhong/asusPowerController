"""共用系统电池发现与供电判定；排除外设电池，按系统电池/交流状态消歧。"""
from pathlib import Path
from .battery import text


def discover(root):
    supplies = sorted((Path(root) / 'class/power_supply').glob('*'))
    batteries = [path for path in supplies if text(path / 'type') == 'Battery'
                 and text(path / 'present') != '0' and text(path / 'scope') != 'Device']
    battery = next((path for path in batteries if (path / 'charge_control_end_threshold').exists()),
                   batteries[0] if batteries else None)
    return supplies, batteries, battery


def external_power(supplies, battery):
    sources = [(text(path / 'type'), text(path / 'online')) for path in supplies
               if text(path / 'scope') != 'Device']
    if ('Mains', '1') in sources:
        return True
    status = text(battery / 'status') if battery is not None else None
    if status == 'Discharging':
        # USB-C PD 链路在线不等于电脑正在由外部供电，还要结合系统电池状态。
        return False
    if status == 'Charging':
        return True
    online = [value for kind, value in sources if kind not in (None, 'Battery')
              and value in ('0', '1')]
    return '1' in online if online else None
