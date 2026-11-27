"""仅凭 PCI 元数据识别机身和外接显卡，避免探测过程唤醒休眠独显。"""
import re


def read_metadata(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def external_pci(device):
    return any(read_metadata(node / 'removable') == 'removable'
               or read_metadata(node / 'external_facing') == '1'
               for node in (device, *device.parents)
               if re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', node.name))


def graphics_device(path):
    return next((node for node in path.resolve().parents
                 if re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', node.name)
                 and read_metadata(node / 'class').startswith('0x03')), None)
