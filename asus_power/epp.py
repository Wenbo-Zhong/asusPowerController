"""Linux CPU pressure monitor and read-only EPP recommendations."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import signal
from threading import Event
import time


PROFILES = ('power', 'balance_power', 'balance_performance', 'performance')


@dataclass(frozen=True)
class Reading:
    cpu: dict
    stalls: dict
    timestamp: float


@dataclass(frozen=True)
class Load:
    busy: float
    busiest: float
    pressure: dict

    @property
    def stall_risk(self):
        worst = max((v for v in self.pressure.values() if v is not None), default=None)
        if worst is None:
            return 'unknown'
        if worst >= 8:
            return 'high'
        return 'elevated' if worst >= 2 else 'low'


def read_load(proc=Path('/proc')):
    cpu = {}
    for line in (proc / 'stat').read_text().splitlines():
        if not line.startswith('cpu'):
            break
        name, *fields = line.split()
        # Guest time is already included in user and nice.
        cpu[name] = tuple(map(int, fields[:8]))
    stalls = {}
    for resource in ('cpu', 'io', 'memory'):
        try:
            some = next(line.split() for line in (proc / 'pressure' / resource).read_text().splitlines()
                        if line.startswith('some '))
            stalls[resource] = int(next(field[6:] for field in some if field.startswith('total=')))
        except (OSError, StopIteration, ValueError):
            stalls[resource] = None
    return Reading(cpu, stalls, time.monotonic())


def between(old, new):
    seconds = new.timestamp - old.timestamp
    if seconds <= 0:
        return None
    rates = {}
    for name, current in new.cpu.items():
        previous = old.cpu.get(name)
        if previous is None or len(current) < 5 or len(current) != len(previous):
            continue
        diff = [max(0, b - a) for a, b in zip(previous, current)]
        total = sum(diff)
        if total:
            rates[name] = 100 * (total - diff[3] - diff[4]) / total
    if 'cpu' not in rates:
        return None
    busy = rates.pop('cpu')
    pressure = {}
    for resource, current in new.stalls.items():
        previous = old.stalls.get(resource)
        pressure[resource] = (min(100, (current - previous) / seconds / 10_000)
                              if current is not None and previous is not None and current >= previous else None)
    return Load(busy, max(rates.values(), default=busy), pressure)


class Policy:
    def __init__(self):
        self.level = 1
        self.pending = None
        self.since = 0

    def choose(self, load, now):
        cpu_pressure = load.pressure.get('cpu') or 0
        urgent = cpu_pressure >= 8 or load.busy >= 70
        if urgent or load.busiest >= 90:
            target = 3
        elif cpu_pressure >= 2 or load.busy >= 30 or load.busiest >= 60:
            target = 2
        elif load.busy < 12 and load.busiest < 35 and cpu_pressure < 1:
            target = 0
        else:
            target = 1

        if target != self.pending:
            self.pending, self.since = target, now
        # Respond immediately to queueing or broad saturation, confirm single-core
        # bursts briefly, and wait longer before turning performance back down.
        if target < self.level:
            delay = 8
        elif urgent:
            delay = 0
        else:
            delay = 1
        if now - self.since >= delay:
            self.level = target
        return PROFILES[self.level]


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def monitor(args, stop):
    emit(event='started', mode='observe')
    policy = Policy()
    previous = read_load()
    published = None
    last_log = 0
    samples = 0
    while not stop.wait(args.interval):
        current = read_load()
        load = between(previous, current)
        previous = current
        if load is None:
            continue
        samples += 1
        suggestion = policy.choose(load, current.timestamp)
        state = (suggestion, load.stall_risk)
        if state != published or current.timestamp - last_log >= 10 or args.samples:
            emit(event='sample', cpu_busy=round(load.busy, 1), busiest_cpu=round(load.busiest, 1),
                 pressure={key: round(v, 2) if v is not None else None for key, v in load.pressure.items()},
                 stall_risk=load.stall_risk, suggested_epp=suggestion, applied_epp=None)
            published, last_log = state, current.timestamp
        if args.samples and samples >= args.samples:
            break


def main(argv=None):
    parser = argparse.ArgumentParser(description='监控 Linux CPU 压力并建议 EPP 档位')
    parser.add_argument('--interval', type=float, default=1, help='采样间隔（秒，0.5–5）')
    parser.add_argument('--samples', type=int, default=0, help='输出指定数量的样本后退出')
    args = parser.parse_args(argv)
    if not 0.5 <= args.interval <= 5 or args.samples < 0:
        parser.error('interval 必须在 0.5–5 秒内，samples 必须非负')
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    monitor(args, stop)


if __name__ == '__main__':
    main()
