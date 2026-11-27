"""六套组合的初始软件预设；combinations 在首次无配置时按机型载入。"""
from pathlib import Path
import re


POWER_FIELDS = (
    'ppt_pl1_spl',
    'ppt_pl2_sppt',
    'ppt_pl3_fppt',
    'nv_tgp',
    'nv_dynamic_boost',
    'nv_temp_target',
)
FANS = ('cpu', 'gpu', 'mid')

# 每个风扇包含 8 个摄氏温度点和 8 个原始 PWM 字节（0–255）。
PROART_P16_HX370 = {
    ('quiet', 'ac'): (
        (25, 38, 45, 55, 5, 77),
        ([44, 52, 58, 64, 69, 74, 79, 84], [0, 23, 46, 74, 112, 156, 204, 255]),
        ([42, 50, 56, 61, 66, 70, 74, 77], [0, 20, 43, 71, 110, 150, 204, 255]),
        ([44, 52, 58, 63, 68, 73, 78, 83], [0, 15, 33, 56, 87, 130, 189, 255]),
    ),
    ('quiet', 'battery'): (
        (17, 35, 38, 55, 5, 75),
        ([44, 53, 60, 66, 71, 76, 81, 86], [0, 20, 38, 61, 92, 133, 186, 255]),
        ([42, 51, 57, 62, 67, 71, 75, 76], [0, 18, 36, 59, 92, 138, 196, 255]),
        ([44, 53, 60, 65, 70, 74, 79, 84], [0, 13, 26, 46, 74, 120, 176, 255]),
    ),
    ('balanced', 'ac'): (
        (42, 55, 65, 70, 10, 80),
        ([44, 52, 58, 64, 69, 74, 79, 84], [0, 31, 61, 94, 138, 184, 224, 255]),
        ([42, 50, 56, 61, 66, 70, 74, 80], [0, 28, 59, 94, 135, 178, 222, 255]),
        ([44, 52, 58, 63, 68, 73, 78, 83], [0, 20, 43, 74, 112, 158, 207, 255]),
    ),
    ('balanced', 'battery'): (
        (25, 38, 45, 55, 5, 77),
        ([44, 53, 60, 66, 71, 76, 81, 86], [0, 26, 48, 76, 115, 158, 209, 255]),
        ([42, 51, 57, 62, 67, 71, 75, 77], [0, 20, 43, 74, 110, 153, 204, 255]),
        ([44, 53, 60, 65, 70, 74, 79, 84], [0, 15, 33, 56, 89, 130, 186, 255]),
    ),
    ('performance', 'ac'): (
        (58, 68, 78, 85, 15, 82),
        ([44, 52, 58, 64, 69, 74, 79, 84], [0, 38, 74, 115, 161, 204, 237, 255]),
        ([42, 50, 56, 61, 66, 70, 74, 82], [0, 36, 71, 112, 158, 201, 237, 255]),
        ([44, 52, 58, 63, 68, 73, 78, 83], [0, 26, 54, 89, 133, 178, 222, 255]),
    ),
    ('performance', 'battery'): (
        (35, 45, 55, 60, 5, 79),
        ([44, 53, 60, 66, 71, 76, 81, 86], [0, 33, 64, 97, 138, 178, 224, 255]),
        ([42, 51, 57, 62, 67, 71, 75, 79], [0, 28, 59, 92, 135, 176, 222, 255]),
        ([44, 53, 60, 65, 70, 74, 79, 84], [0, 20, 43, 71, 110, 153, 204, 255]),
    ),
}


def for_machine(entries, product_path=Path('/sys/class/dmi/id/product_name'),
                cpuinfo_path=Path('/proc/cpuinfo')):
    try:
        product = product_path.read_text()
        cpuinfo = cpuinfo_path.read_text()
    except OSError:
        return entries
    if not re.search(r'\bproart\s+p16\b|\bH7606[A-Z0-9]*\b', product, re.IGNORECASE):
        return entries
    if not re.search(r'^model name\s*:\s*[^\n]*\bHX\s*370\b', cpuinfo, re.IGNORECASE | re.MULTILINE):
        return entries

    for (mode, source), (power, *curves) in PROART_P16_HX370.items():
        entries[mode][source] = {
            'power': dict(zip(POWER_FIELDS, power)),
            'fans': {
                fan: {'fan': fan, 'temp': list(temp), 'pwm': list(pwm), 'enabled': True}
                for fan, (temp, pwm) in zip(FANS, curves)
            },
        }
    return entries
