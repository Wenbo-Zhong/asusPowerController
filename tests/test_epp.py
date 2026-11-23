import unittest

from asus_power.epp import Load, Policy, Reading, between


class PolicyTests(unittest.TestCase):
    def test_short_window_cpu_and_io_accounting(self):
        old = Reading({'cpu': (0,) * 8, 'cpu0': (0,) * 8}, {'cpu': 0, 'io': 0}, 1)
        new = Reading({'cpu': (20, 0, 0, 40, 40, 0, 0, 0), 'cpu0': (9, 0, 0, 1, 0, 0, 0, 0)},
                      {'cpu': 10_000, 'io': 200_000, 'memory': None}, 2)
        load = between(old, new)
        self.assertEqual((load.busy, load.busiest), (20, 90))
        self.assertEqual(load.pressure, {'cpu': 1, 'io': 20, 'memory': None})
        self.assertEqual(load.stall_risk, 'high')

    def test_cpu_queue_boosts_immediately_and_idle_must_persist(self):
        policy = Policy()
        queued = Load(22, 40, {'cpu': 10})
        idle = Load(5, 18, {'cpu': 0})
        self.assertEqual(policy.choose(queued, 1), 'performance')
        self.assertEqual(policy.choose(idle, 2), 'performance')
        self.assertEqual(policy.choose(idle, 9), 'performance')
        self.assertEqual(policy.choose(idle, 10), 'power')

    def test_single_core_saturation_and_non_cpu_stalls(self):
        policy = Policy()
        io_wait = Load(4, 10, {'cpu': 0, 'io': 45, 'memory': 25})
        self.assertEqual(policy.choose(io_wait, 1), 'balance_power')
        self.assertEqual(policy.choose(io_wait, 9), 'power')
        one_core = Load(9, 96, {'cpu': 0})
        self.assertEqual(policy.choose(one_core, 10), 'power')
        self.assertEqual(policy.choose(one_core, 11), 'performance')
