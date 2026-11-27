"""按桌面用户维持单实例；提权升级时等待旧进程完成硬件任务后再接管。"""
from .i18n import tr
import errno
import os
import socket
import struct
import time


def peer_uid(connection):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    return struct.unpack('3i', credentials)[1]


class Instance:
    def __init__(self, start_hidden):
        self.uid = int(os.environ.get('PKEXEC_UID', os.getuid())) if os.geteuid() == 0 else os.getuid()
        self.address = f'\0asus-power-controller.{self.uid}'
        self.elevated = os.geteuid() == 0
        self.command = b'upgrade' if self.elevated else b'quiet' if start_hidden else b'show'
        self.listener = None

    def acquire(self):
        deadline = time.monotonic() + 5
        replacing = False
        while replacing or time.monotonic() < deadline:
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as candidate:
                try:
                    candidate.bind(self.address)
                except OSError as error:
                    if error.errno != errno.EADDRINUSE:
                        raise
                else:
                    candidate.listen()
                    self.listener = candidate.dup()
                    self.listener.setblocking(False)
                    return True
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                    client.settimeout(1)
                    client.connect(self.address)
                    if peer_uid(client) not in (self.uid, 0):
                        raise PermissionError(tr('instance.uid_mismatch'))
                    client.sendall(self.command)
                    reply = client.recv(16)
                    if reply == b'handled':
                        return False
                    if reply == b'retry' and self.command == b'upgrade':
                        # 旧实例完成硬件任务之前持有锁；只有验证过的提权升级确认
                        # 才取消普通响应超时，避免等待期间启动两个硬件控制器。
                        replacing = True
            except (ConnectionError, TimeoutError):
                pass
            time.sleep(.05)
        raise RuntimeError(tr('instance.timeout'))

    def attach(self, root, show, quit_app):
        def accept(listener, _mask):
            try:
                peer, _ = listener.accept()
            except BlockingIOError:
                return
            # 空闲连接不能阻塞界面主线程或其他启动请求。
            peer.setblocking(False)

            def release():
                root.deletefilehandler(peer)
                peer.close()

            def receive(connection, _mask):
                root.after_cancel(timeout)
                try:
                    uid = peer_uid(connection)
                    if uid not in (self.uid, 0):
                        return
                    command = connection.recv(16)
                    if command == b'upgrade' and uid == 0 and not self.elevated:
                        if not getattr(self, 'replacing', False):
                            self.replacing = True
                            root.after_idle(quit_app)
                        connection.send(b'retry')
                    elif command in (b'show', b'quiet', b'upgrade'):
                        if command != b'quiet':
                            root.after_idle(show)
                        connection.send(b'handled')
                except OSError:
                    pass
                finally:
                    release()

            timeout = root.after(1000, release)
            root.createfilehandler(peer, 2, receive)

        root.createfilehandler(self.listener, 2, accept)

    def close(self):
        if self.listener is not None:
            self.listener.close()
