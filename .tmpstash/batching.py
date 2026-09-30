"""How records get grouped into the units of work the job actually processes.

Nothing in a streaming job processes one record at a time. Spark structured
streaming collects a micro-batch per trigger; Flink checkpoints and fires timers
on a schedule. The grouping is the hinge this whole repository turns on, because
the watermark is recomputed per group, and a record's fate depends on which side
of a group boundary it fell.

The original run groups by wall-clock: a trigger fires every `interval_ms` and
takes whatever has arrived. A replay cannot group that way, because every record
is already sitting in the log and arrives as fast as the reader can pull it. It
groups by size, and the sizes are enormous compared to a live run. That is the
mechanism. It is not a bug in anyone's code and no configuration setting turns
it off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from srd.events import LoggedEvent, ObservedEvent, observed_order

__all__ = [
    "Batch",
    "Batcher",
    "WallClockBatcher",
    "ThroughputBatcher",
    "SingleBatch",
    "BatchingError",
    "as_observed",
]


class BatchingError(ValueError):
    """Raised for a batching configuration that cannot produce batches."""


@dataclass(frozen=True, slots=True)
class Batch:
    """One trigger's worth of records, in the order the job will see them.

    `index` is the trigger number, counting from zero. `wall_clock_ms` is when
    the trigger fired on the run that produced it, and is present even on a
    replay: the replay has a wall clock, it just bears no relation to the one
    that produced the original output. Keeping it lets the runbook show two
    timelines side by side, which is the picture that makes the point.
    """

    index: int
    wall_clock_ms: int
    events: tuple[ObservedEvent, ...]

    def __len__(self) -> int:
        return len(self.events)

    @property
    def max_event_time_ms(self) -> int | None:
        """Highest producer stamp in the batch, or None when the batch is empty.

        Empty batches are real: a live trigger with nothing to do still fires,
        and a watermark policy that treated "no records" as "event time zero"
        would send the watermark backwards. Returning None forces the caller to
        decide, and the watermark module decides to hold.
        """
        if not self.events:
            return None
        return max(event.event_time_ms for event in self.events)


class Batcher(Protocol):
    """Anything that can group observed records into ordered batches."""

    def batches(self, events: Sequence[ObservedEvent]) -> tuple[Batch, ...]:
        ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class WallClockBatcher:
    """The live run: a trigger every `interval_ms`, taking whatever has arrived.

    `emit_empty` controls whether triggers with no records are kept. A real
    engine fires them, and they matter to the watermark trace even though they
    change no output, so the default is to keep them. Dropping them is available
    because the experiments compare the two and the difference is instructive:
    it is nil, which is worth being able to show rather than assert.
    """

    interval_ms: int
    emit_empty: bool = True

    def __post_init__(self) -> None:
        if self.interval_ms <= 0:
            raise BatchingError("trigger interval must be positive")

    def batches(self, events: Sequence[ObservedEvent]) -> tuple[Batch, ...]:
        ordered = observed_order(events)
        if not ordered:
            return ()
        start = ordered[0].arrival_ms
        end = ordered[-1].arrival_ms
        out: list[Batch] = []
        index = 0
        tick = start
        pending = list(ordered)
        cursor = 0
        while tick <= end:
            boundary = tick + self.interval_ms
            taken: list[ObservedEvent] = []
            while cursor < len(pending) and pending[cursor].arrival_ms < boundary:
                taken.append(pending[cursor])
                cursor += 1
            if taken or self.emit_empty:
                out.append(Batch(index=index, wall_clock_ms=boundary, events=tuple(taken)))
                index += 1
            tick = boundary
        return tuple(out)


@dataclass(frozen=True, slots=True)
class ThroughputBatcher:
    """The replay: batches bounded by record count, not by time.

    This is what `maxOffsetsPerTrigger` or its equivalent gives you when the
    source is a log that is already full. The wall clock advances by
    `per_batch_ms` per batch, which is how long it takes to read and process a
    batch, and is orders of magnitude smaller than the interval that produced
    the original batches. That gap is the reason session timeouts never fire on
    a replay.
    """

    max_records: int
    per_batch_ms: int = 1

    def __post_init__(self) -> None:
        if self.max_records <= 0:
            raise BatchingError("max records per batch must be positive")
        if self.per_batch_ms <= 0:
            raise BatchingError("per batch duration must be positive")

    def batches(self, events: Sequence[ObservedEvent]) -> tuple[Batch, ...]:
        ordered = tuple(sorted(events, key=lambda event: event.offset))
        out: list[Batch] = []
        for index, start in enumerate(range(0, len(ordered), self.max_records)):
            chunk = ordered[start : start + self.max_records]
            out.append(
                Batch(
                    index=index,
                    wall_clock_ms=(index + 1) * self.per_batch_ms,
                    events=chunk,
                )
            )
        return tuple(out)


@dataclass(frozen=True, slots=True)
class SingleBatch:
    """Everything in one batch, in offset order.

    The friendliest replay there is, and the one an engineer reaches for when
    they say "just reprocess the whole topic". It is also the batching under
    which the watermark never drops anything, because the watermark is computed
    from the previous batch and there is no previous batch. The bounds module
    proves that this is not luck.
    """

    per_batch_ms: int = 1

    def __post_init__(self) -> None:
        if self.per_batch_ms <= 0:
            raise BatchingError("per batch duration must be positive")

    def batches(self, events: Sequence[ObservedEvent]) -> tuple[Batch, ...]:
        if not events:
            return ()
        ordered = tuple(sorted(events, key=lambda event: event.offset))
        return (Batch(index=0, wall_clock_ms=self.per_batch_ms, events=ordered),)


def as_observed(events: Sequence[LoggedEvent], *, arrival_ms: int = 0) -> tuple[ObservedEvent, ...]:
    """Attach a constant arrival time to logged records.

    This is what a replay does, and naming it makes the lie explicit. Every
    record is stamped with the same arrival, because the replay genuinely does
    not know and any other choice would be an invention. Code that wants a
    replay to behave like the original by reconstructing arrival times is
    reaching for a number that was never written down.
    """
    return tuple(ObservedEvent(logged=event, arrival_ms=arrival_ms) for event in events)
