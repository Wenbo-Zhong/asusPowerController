"""Identify PCI graphics devices without opening GPU driver interfaces."""
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
