"""共用 busctl 传输与 JSON 解码；硬件语义校验留在调用此模块的各功能中。"""
from ..i18n import tr
import json
import subprocess

SERVICE = 'xyz.ljones.Asusd'
OBJECT = '/xyz/ljones'
PLATFORM = 'xyz.ljones.Platform'


class BusClient:
    def __init__(self, runner=subprocess.run, *, timeout=8, error=tr('bus.call_failed')):
        self.runner = runner
        self.timeout = timeout
        self.error = error

    def command(self, *arguments):
        result = self.runner(['busctl', '--system', '--json=short', *map(str, arguments)],
                             capture_output=True, text=True, timeout=self.timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or self.error).strip())
        return result.stdout

    def json(self, *arguments):
        return json.loads(self.command(*arguments))

    def get(self, path, interface, name):
        return self.json('get-property', SERVICE, path, interface, name)['data']

    def put(self, path, interface, name, signature, *values):
        return self.command('set-property', SERVICE, path, interface, name, signature,
                            *(str(value).lower() if type(value) is bool else str(value)
                              for value in values))
