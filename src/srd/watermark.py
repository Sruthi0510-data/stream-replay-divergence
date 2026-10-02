"""The watermark: the job's claim about what it will never see again.

A watermark is a promise, not a measurement. The engine asserts that no record
with an event time below the watermark will arrive from here on, and then acts
on that assertion by closing windows and discarding whatever contradicts it. The
assertion is made from one observation only, the highest event time seen so far,
minus a configured slack.

Two properties of that rule do all the damage.

It is monotonic. A watermark never moves backwards, because state that has
already been dropped cannot be un-dropped. So one early record with a wrong
clock raises the watermark permanently, and every genuinely on-time record below
the new line is discarded for the rest of the run.

It is evaluated per batch, against the batch boundary rather than against the
record. An engine computes the watermark from the batch it has just finished and
applies it to the batch it is about to start. A record therefore survives if it
shares a batch with the record that would have condemned it, and dies if it
falls into the next one. Nothing about the record changed. The trigger did.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from srd.batching import Batch

__all__ = [
    "WatermarkPolicy",
    "WatermarkState",
    "WatermarkStep",
    "WatermarkError",
    "trace",
]

NEVER = -(2**62)
"""The watermark before any record has been seen.

Not negative infinity and not zero. Zero would be a real event time in a log
stamped in epoch milliseconds only if the log started in 1970, but a log of
relative timestamps starting at zero is exactly what a test fixture looks like,
and a starting watermark of zero would silently drop its first record. A
sentinel far below any plausible stamp is the honest value, and comparisons
against it behave.
"""


class WatermarkError(ValueError):
    """Raised for a watermark configuration that cannot be applied."""


@dataclass(frozen=True, slots=True)
class WatermarkPolicy:
    """Allowed lateness, and whether the watermark is global or per partition.

    `per_partition` is the setting nobody reads the documentation for. A global
    watermark takes the maximum across every partition, so a fast partition
    drags the line up and starves a slow one. A per-partition watermark tracks
    each independently and is what most people assume they have. Both are
    modelled because the divergence measured by this repository is materially
    different under each, and the experiments show by how much.
    """

    allowed_lateness_ms: int
    per_partition: bool = False

    def __post_init__(self) -> None:
        if self.allowed_lateness_ms < 0:
            raise WatermarkError("allowed lateness cannot be negative")

    def line_for(self, highest_event_time_ms: int | None) -> int:
        """The watermark implied by the highest stamp seen so far.

        None means nothing has been seen, and the answer is the sentinel rather
        than an error: a job that has processed no records has a watermark, and
        it is "below everything".
        """
        if highest_event_time_ms is None:
            return NEVER
        return highest_event_time_ms - self.allowed_lateness_ms


@dataclass(slots=True)
class WatermarkState:
    """The mutable watermark carried between batches.

    Deliberately mutable, and deliberately not a frozen value threaded through a
    fold. The state in a real engine is mutable, the monotonic clamp is a
    property of the mutation, and modelling it as a fold would have let a caller
    reconstruct an earlier watermark by replaying the fold from an earlier point,
    which is precisely the thing a real engine cannot do.
    """

    policy: WatermarkPolicy
    _highest: dict[int, int] = field(default_factory=dict)
    _line: dict[int, int] = field(default_factory=dict)

    _GLOBAL = -1

    def _slot(self, partition: int) -> int:
        return partition if self.policy.per_partition else self._GLOBAL

    def line(self, partition: int = 0) -> int:
        """The watermark currently in force for a partition."""
        return self._line.get(self._slot(partition), NEVER)

    def observe(self, batch: Batch) -> None:
        """Fold a finished batch into the watermark, monotonically.

        Called after the batch has been processed, never before. A batch with no
        records leaves the watermark exactly where it was, which is why
        `max_event_time_ms` returns None rather than a number.
        """
        highest: dict[int, int] = {}
        for event in batch.events:
            slot = self._slot(event.partition)
            current = highest.get(slot)
            if current is None or event.event_time_ms > current:
                highest[slot] = event.event_time_ms
        for slot, stamp in highest.items():
            previous = self._highest.get(slot)
            if previous is None or stamp > previous:
                self._highest[slot] = stamp
            candidate = self.policy.line_for(self._highest[slot])
            if candidate > self._line.get(slot, NEVER):
                self._line[slot] = candidate

    def is_late(self, partition: int, event_time_ms: int) -> bool:
        """Whether a record would be discarded at the current watermark.

        Strictly below, not at or below. A record whose event time equals the
        watermark is exactly as late as the policy said it would tolerate, and
        engines keep it. The choice matters for a fixture whose stamps land on
        round numbers, which most fixtures do.
        """
        return event_time_ms < self.line(partition)


@dataclass(frozen=True, slots=True)
class WatermarkStep:
    """One row of the watermark trace: what the line was when a batch started."""

    batch_index: int
    wall_clock_ms: int
    line_ms: int
    records: int
    dropped: int


def trace(policy: WatermarkPolicy, batches: Sequence[Batch]) -> tuple[WatermarkStep, ...]:
    """Replay the watermark over a batching without running the job.

    Used by the diagrams and by the bounds check. It answers "what would the
    line have been" without committing to any aggregation, so a reader can
    satisfy themselves that the divergence comes from the watermark trajectory
    and not from something in the aggregation logic.
    """
    state = WatermarkState(policy=policy)
    steps: list[WatermarkStep] = []
    for batch in batches:
        dropped = sum(1 for event in batch.events if state.is_late(event.partition, event.event_time_ms))
        steps.append(
            WatermarkStep(
                batch_index=batch.index,
                wall_clock_ms=batch.wall_clock_ms,
                line_ms=state.line(batch.events[0].partition if batch.events else 0),
                records=len(batch.events),
                dropped=dropped,
            )
        )
        state.observe(batch)
    return tuple(steps)
