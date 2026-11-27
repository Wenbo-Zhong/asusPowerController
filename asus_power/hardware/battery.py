"""只读电池遥测与显示数据；兼容 Wh 能量和 Ah 电荷量两种驱动接口。

Behavior reference: asusctl 61c39ffd, rog-platform/src/power.rs and
rog-control-center/ui/pages/battery.slint. Independently implemented; no
upstream source is copied or bundled.
"""
from ..i18n import tr
import math
from pathlib import Path


def text(path):
    try:
        return path.read_text().strip() or None
    except (OSError, UnicodeError):
        return None


def numeric(path, signed=False):
    try:
        value = float(text(path))
        return value if math.isfinite(value) and (signed or value >= 0) else None
    except (TypeError, ValueError, OverflowError):
        return None


def read_battery(path):
    if path is None:
        return {'present': False}
    path = Path(path)
    if not path.is_dir() or text(path / 'present') == '0':
        return {'present': False, 'name': path.name}

    def value(name, signed=False):
        number = numeric(path / name, signed)
        return number / 1_000_000 if number is not None else None

    energy = {key: value('energy_' + key) for key in ('now', 'full', 'full_design')}
    charge = {key: value('charge_' + key) for key in ('now', 'full', 'full_design')}
    voltage = value('voltage_now')
    design_voltage = value('voltage_min_design')
    current = value('current_now', signed=True)
    current = abs(current) if current is not None else None
    power = value('power_now', signed=True)
    if power is not None:
        power = abs(power)
    elif current is not None and voltage is not None and voltage > 0:
        power = current * voltage
        if not math.isfinite(power):
            power = None

    # 健康度的分子分母必须同单位；不能把 Wh 满充容量与 Ah 设计容量相除。
    health = None
    for quantities in (energy, charge):
        full, design = quantities['full'], quantities['full_design']
        if full is not None and design is not None and full > 0 and design > 0:
            health = min(100., full / design * 100)
            break
    percent = numeric(path / 'capacity')
    if percent is None or percent > 100:
        percent = None
        for quantities in (energy, charge):
            now, full = quantities['now'], quantities['full']
            if now is not None and full is not None and full > 0:
                percent = min(100., now / full * 100)
                break
    limit = numeric(path / 'charge_control_end_threshold')
    if limit is not None and not 20 <= limit <= 100:
        limit = None
    status = text(path / 'status') or 'Unknown'
    cycle = numeric(path / 'cycle_count')
    cycle = int(cycle) if cycle is not None and cycle.is_integer() else None

    seconds = None
    kind = None
    if status in ('Charging', 'Discharging'):
        # 优先使用原生 Wh/W；退回 Ah/A 时无需电压，避免单位折算误差。
        for quantities, rate in ((energy, power), (charge, current)):
            now, full = quantities['now'], quantities['full']
            if now is None or rate is None or rate <= 0:
                continue
            if quantities is energy and rate < .1:
                break
            if quantities is charge and power is not None and power < .1:
                break
            if status == 'Charging':
                if full is None or full <= 0:
                    continue
                remaining = full * (limit if limit is not None else 100) / 100 - now
            else:
                remaining = now
            if remaining > 0:
                estimate = remaining / rate * 3600
                if math.isfinite(estimate):
                    seconds = estimate
                    kind = 'charging' if status == 'Charging' else 'discharging'
            break

    # Ah 容量统一用一个标称电压换算为可比较的 Wh，并在界面标明估算性质。
    nominal = design_voltage if design_voltage is not None and design_voltage > 0 else voltage
    converted = False
    display_energy = dict(energy)
    for key in energy:
        if display_energy[key] is None and charge[key] is not None and nominal is not None and nominal > 0:
            estimate = charge[key] * nominal
            if math.isfinite(estimate):
                display_energy[key] = estimate
                converted = True
    return {'present': True, 'name': path.name, 'manufacturer': text(path / 'manufacturer'),
            'model': text(path / 'model_name'), 'technology': text(path / 'technology'),
            'status': status, 'capacity_percent': percent, 'limit_percent': limit,
            'health_percent': health, 'cycle_count': cycle,
            'power_w': power, 'voltage_v': voltage, 'current_a': current,
            'energy_now_wh': display_energy['now'], 'energy_full_wh': display_energy['full'],
            'energy_design_wh': display_energy['full_design'], 'energy_converted': converted,
            'charge_full_ah': charge['full'], 'charge_design_ah': charge['full_design'],
            'time_seconds': seconds, 'time_kind': kind,
            'time_target_percent': (limit if limit is not None else 100) if kind == 'charging' else None}


STATUS_NAMES = {'Charging': tr('battery.charging'), 'Discharging': tr('battery.discharging'), 'Full': tr('battery.full'),
                'Not charging': tr('battery.not_charging'), 'Unknown': tr('battery.unknown_state')}


def formatted_values(info):
    """所有状态共用单位和缺失值显示规则，避免页面各自折算。"""
    def amount(key, unit, digits=1):
        value = info.get(key)
        return '—' if value is None else f'{value:.{digits}f}{unit}'

    if not info.get('present'):
        return {'summary': tr('battery.not_detected'), 'percent': '—', 'health': '—', 'power': '—',
                'time': '—', 'power_title': tr('battery.power'), 'time_title': tr('battery.remaining_time'),
                'note': tr('battery.reconnect_note')}
    status = info.get('status', 'Unknown')
    charging = status == 'Charging'
    power_title = tr('battery.charging_power') if charging else tr('battery.discharging_power') if status == 'Discharging' else tr('battery.power')
    target = info.get('time_target_percent')
    time_title = tr('battery.eta_target', target=target) if charging and target is not None else tr('battery.charging_time') if charging else tr('battery.runtime')
    seconds = info.get('time_seconds')
    duration = '—'
    if seconds is not None and seconds > 0:
        minutes = max(1, round(seconds / 60))
        hours, minutes = divmod(minutes, 60)
        duration = tr('battery.hours_minutes', hours=hours, minutes=minutes) if hours else tr('battery.minutes', minutes=minutes)
    values = {'summary': f"{info.get('name', tr('page.battery'))} · {STATUS_NAMES.get(status, tr('battery.unknown_state'))}",
              'percent': amount('capacity_percent', '%', 0),
              'health': amount('health_percent', '%'), 'power': amount('power_w', ' W'),
              'time': duration, 'power_title': power_title, 'time_title': time_title,
              'limit': amount('limit_percent', '%', 0),
              'full': amount('energy_full_wh', ' Wh'), 'design': amount('energy_design_wh', ' Wh'),
              'energy': amount('energy_now_wh', ' Wh'),
              'charge_full': amount('charge_full_ah', ' Ah', 3),
              'charge_design': amount('charge_design_ah', ' Ah', 3),
              'cycles': amount('cycle_count', tr('battery.cycles_suffix'), 0), 'voltage': amount('voltage_v', ' V', 2),
              'current': amount('current_a', ' A', 3),
              'manufacturer': info.get('manufacturer') or '—', 'model': info.get('model') or '—',
              'technology': info.get('technology') or '—',
              'note': tr('battery.health_note')}
    if info.get('energy_converted'):
        values['note'] += tr('battery.energy_note')
    return values
