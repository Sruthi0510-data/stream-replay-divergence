"""A seeded log that behaves like a real one, and the run that consumed it.

The generator produces two things and they are not interchangeable.

It produces a log: offsets, partitions, keys, producer stamps, payloads. That is
what goes in `fixtures/log.json`, and it is everything a replay can read.

It also produces arrival times, which it keeps in memory, feeds to the original
run, and then throws away. The arrival times are never written to the fixture,
because writing them would build a fixture unlike any log an incident responder
has ever been handed, and every result measured against it would be a result
about a system nobody operates.

What the fixture does contain, alongside the log, is the output the original run
produced. That is also what a responder has: yesterday's table, sitting in the
warehouse, and the topic it came from. The question this repository answers is
what happens when they try to rebuild the first from the second.

The shape of the traffic is chosen to make three things true at once, and each
is pinned by a test so that a later tweak to the parameters cannot quietly
remove them:

  * records arrive out of event-time order, by a spread wide enough that the
    choice of allowed lateness is a real decision rather than a formality;
  * one partition stalls partway through, which is the thing that actually
    happens, and its backlog lands in a burst behind a watermark that the other
    partitions carried on raising;
  * several keys fall quiet for longer than the session gap, so the original run
    genuinely has multiple sessions per key to lose;
  * producers stamp to the nearest second rather than the nearest millisecond,
    which is what most of them do and which puts genuine ties in the data, so a
    last-value aggregate has records to be ambiguous about.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from srd.events import LoggedEvent, ObservedEvent

__all__ = ["GeneratorConfig", "generate"]

KEY_NAMES = (
    "device-north",
    "device-south",
    "device-east",
    "device-west",
    "gateway-alpha",
    "gateway-beta",
    "meter-01",
    "meter-02",
    "meter-03",
    "meter-04",
    "probe-red",
    "probe-blue",
)


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Everything about the synthetic traffic, in one reviewable object.

    Defaults are the shipped fixture. They are not arbitrary: `stall_ms` is
    larger than `allowed lateness` in the shipped spec, which is what makes the
    stalled partition's backlog droppable, and `quiet_gap_ms` is larger than the
    session gap, which is what makes the sessions real. Changing one without the
    other produces a fixture that no longer demonstrates anything, so the tests
    assert the properties rather than the parameter values.
    """

    records: int = 30_000
    partitions: int = 4
    span_ms: int = 6 * 60 * 60 * 1000
    seed: int = 20260930
    base_delay_ms: int = 800
    jitter_ms: int = 38_000
    clock_skew_ms: int = 45_000
    stamp_resolution_ms: int = 1_000
    skewed_key_share: float = 0.08
    stall_at_fraction: float = 0.42
    stall_ms: int = 95_000
    stall_partition: int = 2
    quiet_keys: tuple[str, ...] = ("meter-03", "meter-04", "probe-blue")
    quiet_gap_ms: int = 25 * 60 * 1000
    quiet_windows: int = 3
    keys: tuple[str, ...] = field(default=KEY_NAMES)

    def __post_init__(self) -> None:
        if self.records <= 0:
            raise ValueError("records must be positive")
        if self.stamp_resolution_ms <= 0:
            raise ValueError("stamp resolution must be positive")
        if self.partitions <= 0:
            raise ValueError("partitions must be positive")
        if not 0.0 <= self.skewed_key_share <= 1.0:
            raise ValueError("skewed key share must be a fraction")
        if not 0.0 < self.stall_at_fraction < 1.0:
            raise ValueError("stall fraction must lie strictly inside the run")
        if self.stall_partition >= self.partitions:
            raise ValueError("stall partition is outside the partition count")
        unknown = [key for key in self.quiet_keys if key not in self.keys]
        if unknown:
            raise ValueError(f"quiet keys not in key set: {', '.join(unknown)}")


def _quiet_intervals(config: GeneratorConfig) -> tuple[tuple[int, int], ...]:
    """Event-time stretches during which the quiet keys emit nothing.

    Spread evenly across the span rather than placed randomly, so that a reader
    checking the fixture by eye finds them where the docstring says they are.
    """
    out: list[tuple[int, int]] = []
    stride = config.span_ms // (config.quiet_windows + 1)
    for index in range(config.quiet_windows):
        start = stride * (index + 1)
        out.append((start, start + config.quiet_gap_ms))
    return tuple(out)


def generate(config: GeneratorConfig | None = None) -> tuple[ObservedEvent, ...]:
    """Build the traffic, with arrival times attached.

    Returns observed records because that is what the original run consumed. The
    caller strips them down to `LoggedEvent` before writing the fixture, and the
    stripping is a one-liner in the CLI rather than something this function does
    for you, so that the discarding of arrival time is visible at the call site.

    Two random streams, not one. `shape_rng` places records in event time and
    `delay_rng` decides how long each took to arrive. They are separate so that
    an experiment can sweep the delay distribution without moving a single
    record in event time, which is the only way to attribute a change in output
    to the delivery path rather than to a different log.
    """
    config = config or GeneratorConfig()
    shape_rng = random.Random(config.seed)
    delay_rng = random.Random(config.seed + 1)
    quiet = _quiet_intervals(config)
    stall_start = int(config.span_ms * config.stall_at_fraction)

    stamps: list[tuple[int, str, int]] = []
    while len(stamps) < config.records:
        key = shape_rng.choice(config.keys)
        stamp = shape_rng.randrange(config.span_ms)
        if key in config.quiet_keys and any(start <= stamp < end for start, end in quiet):
            continue
        stamps.append((stamp, key, shape_rng.randrange(1, 500)))
    stamps.sort()

    skewed = set(
        shape_rng.sample(list(config.keys), max(1, round(len(config.keys) * config.skewed_key_share)))
    )

    events: list[ObservedEvent] = []
    for offset, (stamp, key, payload) in enumerate(stamps):
        partition = abs(hash((key, offset % 7))) % config.partitions
        raw = stamp - config.clock_skew_ms if key in skewed else stamp
        emitted = (raw // config.stamp_resolution_ms) * config.stamp_resolution_ms
        delay = config.base_delay_ms + delay_rng.randrange(config.jitter_ms)
        if partition == config.stall_partition and stall_start <= stamp < stall_start + config.stall_ms:
            delay += config.stall_ms + delay_rng.randrange(config.jitter_ms)
        events.append(
            ObservedEvent(
                logged=LoggedEvent(
                    offset=offset,
                    partition=partition,
                    key=key,
                    event_time_ms=emitted,
                    payload=payload,
                ),
                arrival_ms=stamp + delay,
            )
        )
    return tuple(events)
