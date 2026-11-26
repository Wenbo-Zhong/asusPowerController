"""Small system-bus transport; hardware validation belongs to each feature."""
import json
import subprocess

SERVICE = 'xyz.ljones.Asusd'
OBJECT = '/xyz/ljones'
PLATFORM = 'xyz.ljones.Platform'


class BusClient:
    def __init__(self, runner=subprocess.run, *, timeout=8, error='asusd 接口调用失败'):
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
