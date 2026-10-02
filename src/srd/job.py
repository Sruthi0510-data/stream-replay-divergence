"""The job under test: a tumbling aggregation and a processing-time sessioniser.

Two stateful operators, chosen because they fail differently.

The aggregation is keyed by event-time window. Its state is bounded by the
watermark: when the line passes the end of a window the window is emitted and
its state released, and any record for that window arriving afterwards is
discarded. Its output depends on the batching only through which records the
watermark discarded, so its divergence is a function of drops.

The sessioniser is keyed by nothing but the key, and its state is bounded by a
processing-time gap: if a key goes quiet for `session_gap_ms` of wall clock, the
session closes and the next record starts a new one. Its output depends on the
batching directly, because wall clock is the batching. On a replay the entire
log arrives inside a few hundred milliseconds of wall clock, no gap ever
elapses, and every key produces exactly one session. This is not a probabilistic
effect and no amount of allowed lateness affects it.

Both operators are simplified relative to any real engine, and the ways in which
they are simplified are listed in ADR-005 so that nobody has to guess whether an
omission was an oversight.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from srd.batching import Batcher
from srd.events import ObservedEvent
from srd.watermark import WatermarkPolicy, WatermarkState, WatermarkStep

__all__ = [
    "JobConfig",
    "WindowRow",
    "SessionRow",
    "DroppedRecord",
    "DropReason",
    "RunResult",
    "JobError",
    "run",
    "window_start",
]


class JobError(ValueError):
    """Raised for a job configuration that cannot be run."""


class DropReason(StrEnum):
    """Why a record never reached an aggregate.

    Only one value today. It is an enum rather than a string because ADR-005
    lists the other reasons a real engine drops records, and a reader comparing
    this model against their own job needs to see that the list is short and
    deliberate rather than assumed to be complete.
    """

    LATE_BEYOND_WATERMARK = "late-beyond-watermark"


def window_start(event_time_ms: int, window_ms: int) -> int:
    """The start of the tumbling window a stamp falls in.

    Floor division rather than truncation so that negative stamps, which a
    relative-time fixture produces the moment anyone subtracts an origin, land
    in the window below rather than being pulled towards zero.
    """
    if window_ms <= 0:
        raise JobError("window size must be positive")
    return (event_time_ms // window_ms) * window_ms


@dataclass(frozen=True, slots=True)
class JobConfig:
    """Everything the job needs, and nothing that depends on how it is run.

    Note what is absent: no trigger interval, no batch size. Those belong to the
    `Batcher`, which is supplied separately, because the entire argument of this
    repository is that they are not part of the job's definition and yet they
    change its output.
    """

    window_ms: int
    session_gap_ms: int
    watermark: WatermarkPolicy

    def __post_init__(self) -> None:
        if self.window_ms <= 0:
            raise JobError("window size must be positive")
        if self.session_gap_ms <= 0:
            raise JobError("session gap must be positive")


@dataclass(frozen=True, slots=True)
class WindowRow:
    """One emitted aggregate.

    `last_payload` is the payload of the highest-stamped record in the window,
    with ties broken by the order the job processed them. It is here to expose
    a divergence that survives even when no record is dropped at all: two runs
    that agree on every count can disagree on a last-value column, because
    "last" is resolved by processing order and processing order is not in the
    log.
    """

    window_start_ms: int
    key: str
    count: int
    total: int
    last_payload: int

    def identity(self) -> tuple[int, str]:
        return (self.window_start_ms, self.key)


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One closed session for one key."""

    key: str
    session_index: int
    records: int
    first_event_time_ms: int
    last_event_time_ms: int

    def identity(self) -> tuple[str, int]:
        return (self.key, self.session_index)


@dataclass(frozen=True, slots=True)
class DroppedRecord:
    """A record the job saw and refused, with enough context to argue about it."""

    offset: int
    key: str
    event_time_ms: int
    watermark_ms: int
    batch_index: int
    reason: DropReason = DropReason.LATE_BEYOND_WATERMARK


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything one run produced, including what it threw away.

    Keeping the dropped records is the part most jobs do not do. A job that
    counts its drops in a metric can tell you the rate; a job that keeps them
    can tell you which customer's Tuesday went missing, which is the question
    that gets asked.
    """

    windows: tuple[WindowRow, ...]
    sessions: tuple[SessionRow, ...]
    dropped: tuple[DroppedRecord, ...]
    watermark_steps: tuple[WatermarkStep, ...]
    batch_count: int
    processed: int

    @property
    def drop_rate(self) -> float:
        total = self.processed + len(self.dropped)
        if total == 0:
            return 0.0
        return len(self.dropped) / total

    def window_index(self) -> dict[tuple[int, str], WindowRow]:
        return {row.identity(): row for row in self.windows}

    def session_index(self) -> dict[tuple[str, int], SessionRow]:
        return {row.identity(): row for row in self.sessions}


@dataclass(slots=True)
class _WindowAccumulator:
    count: int = 0
    total: int = 0
    last_event_time_ms: int | None = None
    last_payload: int = 0

    def add(self, event: ObservedEvent) -> None:
        self.count += 1
        self.total += event.payload
        if self.last_event_time_ms is None or event.event_time_ms >= self.last_event_time_ms:
            self.last_event_time_ms = event.event_time_ms
            self.last_payload = event.payload


@dataclass(slots=True)
class _SessionAccumulator:
    index: int
    records: int
    first_event_time_ms: int
    last_event_time_ms: int
    last_wall_clock_ms: int

    def add(self, event: ObservedEvent, wall_clock_ms: int) -> None:
        self.records += 1
        self.first_event_time_ms = min(self.first_event_time_ms, event.event_time_ms)
        self.last_event_time_ms = max(self.last_event_time_ms, event.event_time_ms)
        self.last_wall_clock_ms = wall_clock_ms

    def close(self, key: str) -> SessionRow:
        return SessionRow(
            key=key,
            session_index=self.index,
            records=self.records,
            first_event_time_ms=self.first_event_time_ms,
            last_event_time_ms=self.last_event_time_ms,
        )


def run(config: JobConfig, batcher: Batcher, events: Sequence[ObservedEvent]) -> RunResult:
    """Run the job over a batching of the records and return everything it did.

    The order of operations inside the loop is the model's whole content, and it
    mirrors what Spark and Flink both do:

      1. the watermark in force is the one computed from previous batches;
      2. every record in this batch is tested against that line;
      3. surviving records update the aggregates and the session state;
      4. only then is the watermark advanced by this batch;
      5. sessions whose gap has elapsed at this batch's wall clock are closed.

    Swapping steps 2 and 4 is the single most common mistake in a hand-rolled
    implementation, and it makes the job look deterministic under replay because
    a record can then condemn itself. A test pins the order by asserting that a
    record sharing a batch with a much later record survives.
    """
    batches = batcher.batches(events)
    state = WatermarkState(policy=config.watermark)
    windows: dict[tuple[int, str], _WindowAccumulator] = {}
    sessions: dict[str, _SessionAccumulator] = {}
    session_counter: dict[str, int] = {}
    closed: list[SessionRow] = []
    dropped: list[DroppedRecord] = []
    steps: list[WatermarkStep] = []
    processed = 0

    for batch in batches:
        line_for_step = state.line(batch.events[0].partition if batch.events else 0)
        batch_drops = 0
        for event in batch.events:
            if state.is_late(event.partition, event.event_time_ms):
                dropped.append(
                    DroppedRecord(
                        offset=event.offset,
                        key=event.key,
                        event_time_ms=event.event_time_ms,
                        watermark_ms=state.line(event.partition),
                        batch_index=batch.index,
                    )
                )
                batch_drops += 1
                continue
            processed += 1
            identity = (window_start(event.event_time_ms, config.window_ms), event.key)
            windows.setdefault(identity, _WindowAccumulator()).add(event)
            live = sessions.get(event.key)
            if live is None:
                index = session_counter.get(event.key, 0)
                session_counter[event.key] = index + 1
                sessions[event.key] = _SessionAccumulator(
                    index=index,
                    records=1,
                    first_event_time_ms=event.event_time_ms,
                    last_event_time_ms=event.event_time_ms,
                    last_wall_clock_ms=batch.wall_clock_ms,
                )
            else:
                live.add(event, batch.wall_clock_ms)

        steps.append(
            WatermarkStep(
                batch_index=batch.index,
                wall_clock_ms=batch.wall_clock_ms,
                line_ms=line_for_step,
                records=len(batch.events),
                dropped=batch_drops,
            )
        )
        state.observe(batch)

        expired = [
            key
            for key, session in sessions.items()
            if batch.wall_clock_ms - session.last_wall_clock_ms > config.session_gap_ms
        ]
        for key in sorted(expired):
            closed.append(sessions.pop(key).close(key))

    for key in sorted(sessions):
        closed.append(sessions[key].close(key))

    rows = tuple(
        WindowRow(
            window_start_ms=identity[0],
            key=identity[1],
            count=acc.count,
            total=acc.total,
            last_payload=acc.last_payload,
        )
        for identity, acc in sorted(windows.items())
    )
    return RunResult(
        windows=rows,
        sessions=tuple(sorted(closed, key=lambda row: row.identity())),
        dropped=tuple(dropped),
        watermark_steps=tuple(steps),
        batch_count=len(batches),
        processed=processed,
    )
