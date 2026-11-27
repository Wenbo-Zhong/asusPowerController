import errno
from asus_power.i18n import tr
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from asus_power.instance import Instance


class InstanceTests(unittest.TestCase):
    def acquire_with_clock(self, reply, release_after, peer=1000, upgrade=True):
        clock = SimpleNamespace(now=0., binds=[], messages=[])
        instance = Instance.__new__(Instance)
        instance.uid = 1000
        instance.address = 'fixture'
        instance.elevated = upgrade
        instance.command = b'upgrade' if upgrade else b'show'
        instance.listener = None

        def socket_factory(*_):
            sock = MagicMock()
            sock.__enter__.return_value = sock

            def bind(_):
                clock.binds.append(clock.now)
                if clock.now < release_after:
                    raise OSError(errno.EADDRINUSE, 'busy')

            sock.bind.side_effect = bind
            sock.sendall.side_effect = clock.messages.append
            sock.recv.return_value = reply
            return sock

        def advance(seconds):
            clock.now += seconds
            if clock.now > 10:
                raise AssertionError('acquisition did not finish')

        with patch('asus_power.instance.socket.socket', side_effect=socket_factory), \
                patch('asus_power.instance.peer_uid', return_value=peer), \
                patch('asus_power.instance.time.monotonic', side_effect=lambda: clock.now), \
                patch('asus_power.instance.time.sleep', side_effect=advance):
            result = instance.acquire()
        return result, clock, instance

    def test_acknowledged_upgrade_waits_for_hardware_drain_past_five_seconds(self):
        result, clock, instance = self.acquire_with_clock(b'retry', 6.)
        self.assertTrue(result)
        self.assertGreaterEqual(clock.now, 6.)
        self.assertIsNotNone(instance.listener)
        self.assertEqual(set(clock.messages), {b'upgrade'})

    def test_unresponsive_instance_keeps_normal_deadline(self):
        with self.assertRaisesRegex(RuntimeError, tr('instance.timeout')):
            self.acquire_with_clock(b'', 9.)

    def test_retry_to_normal_launch_does_not_remove_deadline(self):
        with self.assertRaisesRegex(RuntimeError, tr('instance.timeout')):
            self.acquire_with_clock(b'retry', 9., upgrade=False)

    def test_untrusted_peer_cannot_acknowledge_replacement(self):
        with self.assertRaises(PermissionError):
            self.acquire_with_clock(b'retry', 6., peer=2000)

    def test_handled_launch_returns_without_replacing(self):
        result, clock, instance = self.acquire_with_clock(b'handled', 9.)
        self.assertFalse(result)
        self.assertEqual(clock.now, 0.)
        self.assertIsNone(instance.listener)
