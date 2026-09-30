"""The two record shapes, and why there have to be two.

A broker's log stores, per partition, an offset, a key, a payload and whatever
timestamp the producer stamped on the record. That is `LoggedEvent`. It is
everything a replay can ever read back, and it is complete: nothing was lost in
writing it.

A running consumer sees more. It also knows when the record turned up at the
consumer, which is wall-clock time on the consumer host, and that reading is
never written anywhere the replay can find. Even a broker that stamps its own
ingestion time records when the record reached the *broker*, which is a
different clock and a different queue. That is `ObservedEvent`.

Keeping them as separate types is the whole point of the module. Code that
takes a `LoggedEvent` cannot accidentally reach for `arrival_ms`, because there
is nothing to reach for. When a function in this package needs arrival time, its
signature says so, and the reader can see immediately that a replay cannot call
it with what it read back from the log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

__all__ = [
    "LoggedEvent",
    "ObservedEvent",
    "EventLog",
    "MalformedLogError",
    "observed_order",
    "read_log",
    "write_log",
]

_REQUIRED_FIELDS = ("offset", "partition", "key", "event_time_ms", "payload")


class MalformedLogError(ValueError):
    """Raised when a log file cannot be read as a log.

    A separate type because the CLI turns it into exit code 2, "I was handed
    something I cannot work with", which is a different thing from exit code 3,
    "I worked and found a divergence".
    """


@dataclass(frozen=True, slots=True)
class LoggedEvent:
    """One record exactly as the broker stores it.

    `event_time_ms` is the producer's stamp. It is the only notion of time a
    replay gets, and it is not ordered: a record with a lower offset may carry a
    higher event time, which is what out-of-orderness means.
    """

    offset: int
    partition: int
    key: str
    event_time_ms: int
    payload: int

    def to_json(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "partition": self.partition,
            "key": self.key,
            "event_time_ms": self.event_time_ms,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class ObservedEvent:
    """A logged record plus the one field the log cannot carry.

    `arrival_ms` is wall-clock time on the consumer when the record was pulled.
    The original run had it. The replay does not, and no amount of care in
    writing the log would have given it to the replay, because the consumer that
    produces this number does not exist until someone starts it.
    """

    logged: LoggedEvent
    arrival_ms: int

    @property
    def offset(self) -> int:
        return self.logged.offset

    @property
    def partition(self) -> int:
        return self.logged.partition

    @property
    def key(self) -> str:
        return self.logged.key

    @property
    def event_time_ms(self) -> int:
        return self.logged.event_time_ms

    @property
    def payload(self) -> int:
        return self.logged.payload

    def lateness_ms(self) -> int:
        """How far behind its own arrival the record's event time is.

        Positive for every record that took any time at all to travel, which is
        every record. The interesting quantity is not this but its spread: a
        constant delay changes nothing, a variable one changes which records
        share a micro-batch.
        """
        return self.arrival_ms - self.event_time_ms


@dataclass(frozen=True, slots=True)
class EventLog:
    """A whole topic: the logged records, in offset order per partition.

    Held as one flat sequence sorted by offset, because every consumer in this
    package reads all partitions. `partition` survives on the record so that the
    watermark policy can ask the question that makes partitioning matter, which
    is whether the watermark is per-partition or global.
    """

    events: tuple[LoggedEvent, ...]

    def __post_init__(self) -> None:
        offsets = [event.offset for event in self.events]
        if offsets != sorted(offsets):
            raise MalformedLogError("log is not in offset order")
        if len(set(offsets)) != len(offsets):
            raise MalformedLogError("log contains duplicate offsets")

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self) -> Iterator[LoggedEvent]:
        return iter(self.events)

    @property
    def partitions(self) -> tuple[int, ...]:
        return tuple(sorted({event.partition for event in self.events}))

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted({event.key for event in self.events}))

    def span_ms(self) -> int:
        """Event-time span from earliest to latest stamp.

        Zero for an empty log, which is the only sensible answer and saves every
        caller a guard.
        """
        if not self.events:
            return 0
        stamps = [event.event_time_ms for event in self.events]
        return max(stamps) - min(stamps)

    def max_out_of_orderness_ms(self) -> int:
        """The largest gap between a record and the highest stamp before it.

        This is the number an operator would use to choose allowed lateness, and
        it is measurable from the log alone, which is why it is the number the
        bounds module builds on. It is zero when the log happens to be in event
        time order.
        """
        worst = 0
        highest = None
        for event in self.events:
            if highest is not None and event.event_time_ms < highest:
                worst = max(worst, highest - event.event_time_ms)
            highest = event.event_time_ms if highest is None else max(highest, event.event_time_ms)
        return worst


def write_log(path: Path, events: Iterable[LoggedEvent], *, compact: bool = True) -> None:
    """Write a log as JSON.

    `compact` exists because the shipped fixture is large enough that pretty
    printing it costs more repository size than it buys anyone in readability.
    The tests write the readable form.
    """
    payload = {"events": [event.to_json() for event in events]}
    separators = (",", ":") if compact else None
    indent = None if compact else 2
    path.write_text(json.dumps(payload, separators=separators, indent=indent) + "\n", encoding="utf-8")


def read_log(path: Path) -> EventLog:
    """Read a log written by `write_log`, refusing anything else.

    Every failure here is a `MalformedLogError` so the CLI has exactly one thing
    to catch. Being specific about which field is missing matters more than it
    looks: the usual cause is a hand-edited fixture, and the message is the only
    thing the person editing it will read.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MalformedLogError(f"no such log: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MalformedLogError(f"{path} is not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or "events" not in raw:
        raise MalformedLogError(f"{path} has no 'events' key")
    rows = raw["events"]
    if not isinstance(rows, list):
        raise MalformedLogError(f"{path} 'events' is not a list")
    events: list[LoggedEvent] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise MalformedLogError(f"{path} event {index} is not an object")
        missing = [field for field in _REQUIRED_FIELDS if field not in row]
        if missing:
            raise MalformedLogError(f"{path} event {index} is missing {', '.join(missing)}")
        events.append(
            LoggedEvent(
                offset=int(row["offset"]),
                partition=int(row["partition"]),
                key=str(row["key"]),
                event_time_ms=int(row["event_time_ms"]),
                payload=int(row["payload"]),
            )
        )
    return EventLog(events=tuple(events))


def observed_order(events: Sequence[ObservedEvent]) -> tuple[ObservedEvent, ...]:
    """Sort observed records the way a consumer actually sees them.

    Arrival first, then offset. The tie-break on offset is not cosmetic: two
    records that arrive in the same millisecond are ordered by the broker, and a
    sort that left them in an arbitrary order would make this package's own
    results depend on Python's sort stability rather than on the model.
    """
    return tuple(sorted(events, key=lambda event: (event.arrival_ms, event.offset)))
