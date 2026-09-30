"""What the log alone can tell you about a record's fate, before running anything.

This module holds the only result in the repository that is a proof rather than
a measurement, and it is the reason the rest of the repository is worth reading.

Fix a log and an allowed lateness. Consider any batching that respects offset
order, which is every batching a real consumer can produce, because a consumer
reads a partition forwards. The watermark applied to a record is computed from
the batches that finished before the record's own batch started, so it is
computed from some *prefix* of the records ahead of it in processing order, and
which prefix depends entirely on where the batch boundaries fell.

Two extremes bound every batching in between:

  * put the record in the first batch, alongside everything before it, and the
    watermark in force is the starting one, which is below everything. The
    record survives.
  * put every record in a batch of its own, and the watermark in force is the
    highest stamp among all earlier records, minus the allowed lateness. The
    record survives only if its own stamp clears that line.

So each record is in one of two classes, and there is no third:

  STABLE_KEEP   its stamp clears even the highest possible line, so no batching
                can drop it;
  CONTINGENT    some batchings keep it and some drop it, and nothing in the log
                says which one ran.

The class that does not exist is the interesting one. No record is dropped under
every batching. A replay that reads the whole topic in a single batch keeps
every record in it, including every record the original run discarded. This is
not a tuning problem and no setting fixes it: allowed lateness only moves the
line, and the single-batch watermark starts below every line.

The practical reading is short. Reprocessing from the log does not reproduce
what the job did. It re-decides, from a position that is strictly more
forgiving, and it re-decides differently for exactly the contingent records.
`contingent_share` counts them, and `rewrite_window` says which part of the
output table they can reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from typing import Iterator, Sequence

from srd.events import EventLog, LoggedEvent
from srd.job import window_start
from srd.watermark import NEVER, WatermarkPolicy

__all__ = [
    "Fate",
    "RecordFate",
    "FateReport",
    "classify",
    "rewrite_window",
    "RewriteWindow",
    "enumerate_batchings",
    "exhaustive_fates",
    "ExhaustiveLimitError",
]

EXHAUSTIVE_LIMIT = 16
"""Largest log the exhaustive checker will accept.

Contiguous batchings of n records number two to the power n minus one, so 16
records is 32,768 batchings, which runs in well under a second. Seventeen is
fine too; the limit is a round number chosen to keep the test suite's slowest
test under a second on a laptop, and the checker refuses rather than hanging.
"""


class ExhaustiveLimitError(ValueError):
    """Raised when the exhaustive checker is handed a log it would choke on."""


class Fate(StrEnum):
    """Whether the batching can change what happens to a record."""

    STABLE_KEEP = "stable-keep"
    CONTINGENT = "contingent"


@dataclass(frozen=True, slots=True)
class RecordFate:
    """One record's fate, with the line that decided it.

    `worst_line_ms` is the highest watermark any batching can put in front of
    this record. Carrying it means a reader can check the verdict by hand
    against the record's own stamp, rather than trusting the classifier.
    """

    offset: int
    key: str
    event_time_ms: int
    worst_line_ms: int
    fate: Fate


@dataclass(frozen=True, slots=True)
class FateReport:
    """Every record's fate, plus the counts the report and README quote."""

    fates: tuple[RecordFate, ...]
    allowed_lateness_ms: int
    per_partition: bool

    def __len__(self) -> int:
        return len(self.fates)

    @property
    def contingent(self) -> tuple[RecordFate, ...]:
        return tuple(fate for fate in self.fates if fate.fate is Fate.CONTINGENT)

    @property
    def contingent_share(self) -> float:
        if not self.fates:
            return 0.0
        return len(self.contingent) / len(self.fates)

    def contingent_offsets(self) -> tuple[int, ...]:
        return tuple(fate.offset for fate in self.contingent)

    def contingent_keys(self) -> tuple[str, ...]:
        return tuple(sorted({fate.key for fate in self.contingent}))

    def by_offset(self) -> dict[int, RecordFate]:
        return {fate.offset: fate for fate in self.fates}


def classify(log: EventLog | Sequence[LoggedEvent], policy: WatermarkPolicy) -> FateReport:
    """Classify every record from the log alone, in one pass.

    Linear in the number of records and touches no batching, no job and no
    aggregation. That is the point: an operator holding a topic and a lateness
    setting can run this before deciding whether to reprocess, and the answer
    does not depend on knowing how the original run was triggered, which they do
    not know.
    """
    events = tuple(log)
    highest: dict[int, int] = {}
    fates: list[RecordFate] = []
    for event in events:
        slot = event.partition if policy.per_partition else 0
        seen = highest.get(slot)
        worst = NEVER if seen is None else seen - policy.allowed_lateness_ms
        fate = Fate.STABLE_KEEP if event.event_time_ms >= worst else Fate.CONTINGENT
        fates.append(
            RecordFate(
                offset=event.offset,
                key=event.key,
                event_time_ms=event.event_time_ms,
                worst_line_ms=worst,
                fate=fate,
            )
        )
        if seen is None or event.event_time_ms > seen:
            highest[slot] = event.event_time_ms
    return FateReport(
        fates=tuple(fates),
        allowed_lateness_ms=policy.allowed_lateness_ms,
        per_partition=policy.per_partition,
    )


@dataclass(frozen=True, slots=True)
class RewriteWindow:
    """The part of the output table that reprocessing can reach.

    `identities` are the (window start, key) pairs holding at least one
    contingent record, so they are exactly the rows whose numbers a replay can
    change. `span_ms` is how wide a stretch of event time they cover, which is
    the number to put in a runbook: outside it, reprocessing is safe.

    `reachable_share` is that count over the number of rows the original run
    emitted, not over the number of rows in the universe, because the question
    an operator asks is what fraction of the table they already have is at risk.
    """

    identities: tuple[tuple[int, str], ...]
    span_ms: int
    earliest_ms: int
    latest_ms: int
    emitted_rows: int

    @property
    def reachable_share(self) -> float:
        if self.emitted_rows == 0:
            return 0.0
        return len(self.identities) / self.emitted_rows


def rewrite_window(
    report: FateReport,
    *,
    window_ms: int,
    emitted_rows: int,
) -> RewriteWindow:
    """Turn a fate report into the set of output rows a replay can rewrite.

    Takes the emitted row count as an argument rather than computing it, because
    the number that belongs in the denominator is the size of the table the
    operator actually has, and this module has no business guessing it.
    """
    contingent = report.contingent
    identities = sorted(
        {(window_start(fate.event_time_ms, window_ms), fate.key) for fate in contingent}
    )
    if not identities:
        return RewriteWindow(
            identities=(),
            span_ms=0,
            earliest_ms=0,
            latest_ms=0,
            emitted_rows=emitted_rows,
        )
    starts = [start for start, _ in identities]
    earliest, latest = min(starts), max(starts)
    return RewriteWindow(
        identities=tuple(identities),
        span_ms=latest + window_ms - earliest,
        earliest_ms=earliest,
        latest_ms=latest + window_ms,
        emitted_rows=emitted_rows,
    )


def enumerate_batchings(count: int) -> Iterator[tuple[int, ...]]:
    """Every way of cutting `count` records into contiguous batches.

    Yields tuples of batch sizes. A cut either happens or does not happen at
    each of the `count - 1` internal gaps, so the enumeration is over bit
    patterns and is complete by construction rather than by cleverness.
    """
    if count <= 0:
        return
    for cuts in product((False, True), repeat=count - 1):
        sizes: list[int] = []
        current = 1
        for cut in cuts:
            if cut:
                sizes.append(current)
                current = 1
            else:
                current += 1
        sizes.append(current)
        yield tuple(sizes)


def exhaustive_fates(
    log: EventLog | Sequence[LoggedEvent], policy: WatermarkPolicy
) -> dict[int, Fate]:
    """Decide each record's fate by trying every batching, for small logs.

    The slow reference implementation. It exists so that `classify`, which is a
    one-pass argument, can be pinned against brute force on every small log a
    property test can generate. When the two disagree the argument is wrong, and
    a test saying so is worth more than a paragraph saying it is not.
    """
    events = tuple(log)
    if len(events) > EXHAUSTIVE_LIMIT:
        raise ExhaustiveLimitError(
            f"exhaustive check refuses {len(events)} records; the limit is {EXHAUSTIVE_LIMIT}"
        )
    kept_somewhere: set[int] = set()
    dropped_somewhere: set[int] = set()
    for sizes in enumerate_batchings(len(events)):
        highest: dict[int, int] = {}
        cursor = 0
        for size in sizes:
            chunk = events[cursor : cursor + size]
            cursor += size
            for event in chunk:
                slot = event.partition if policy.per_partition else 0
                seen = highest.get(slot)
                line = NEVER if seen is None else seen - policy.allowed_lateness_ms
                if event.event_time_ms < line:
                    dropped_somewhere.add(event.offset)
                else:
                    kept_somewhere.add(event.offset)
            for event in chunk:
                slot = event.partition if policy.per_partition else 0
                seen = highest.get(slot)
                if seen is None or event.event_time_ms > seen:
                    highest[slot] = event.event_time_ms
    return {
        event.offset: (
            Fate.CONTINGENT
            if event.offset in dropped_somewhere and event.offset in kept_somewhere
            else Fate.STABLE_KEEP
        )
        for event in events
    }
