"""What the job would expose to a scrape, and what an alert on it can and cannot say.

A streaming job in production is watched through a handful of counters and
gauges. The ones here are the ones that would have caught the thing this
repository is about, and one that would not, which is included because it is the
one most teams have.

`srd_records_dropped_total` is the counter every team has. It is a true number
and it is not enough: it tells an operator that records were discarded, not that
the output table can no longer be rebuilt. A job can run with this counter at
zero and still be unreplayable, because the session operator loses its output to
a replay without dropping a single record.

`srd_partition_watermark_deficit_ms` is the gauge that would have caught it, and
it is here in place of a plainer watermark-lag gauge that was tried first. Lag,
defined as the highest stamp seen minus the watermark, is exactly the allowed
lateness whenever the line has caught up, which under a fixed policy is almost
always. It is constant by construction and a dashboard of it is a flat line.
The deficit is the useful quantity: how far the global watermark's driver has
run ahead of a given partition's own newest record. It is zero when every
partition keeps pace, it crosses the allowed lateness exactly when that
partition's fresh records start being discarded as late, and no engine exposes
it.

`srd_contingent_records_total` is the one no engine exposes, because no engine
computes it. It is the count from the bounds module, and it is the number that
answers "can we reprocess this window", which is the question actually asked
during an incident.

The exposition format is the Prometheus text format, written by hand. A client
library would be one dependency and one more thing to install offline, and the
format is four lines of rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from srd.events import LoggedEvent

__all__ = [
    "Metric",
    "MetricKind",
    "METRICS",
    "AlertRule",
    "ALERTS",
    "render_exposition",
    "metric_names",
    "partition_deficit_ms",
    "MetricError",
]


class MetricError(ValueError):
    """Raised when a metric is rendered with a value that cannot be exposed."""


@dataclass(frozen=True, slots=True)
class MetricKind:
    """A Prometheus metric type and whether it may go down."""

    name: str
    monotonic: bool


COUNTER = MetricKind(name="counter", monotonic=True)
GAUGE = MetricKind(name="gauge", monotonic=False)


@dataclass(frozen=True, slots=True)
class Metric:
    """One exposed series, with the sentence an on-call engineer needs.

    `catches` is not decoration. A metric whose docstring cannot say what
    incident it catches is a metric that will be scraped for years and read
    once, and writing the sentence is the cheapest way to find out there is no
    answer.
    """

    name: str
    kind: MetricKind
    help_text: str
    catches: str


METRICS: tuple[Metric, ...] = (
    Metric(
        name="srd_records_processed_total",
        kind=COUNTER,
        help_text="Records that reached an aggregate.",
        catches="A source that has stopped producing, when read against its own rate.",
    ),
    Metric(
        name="srd_records_dropped_total",
        kind=COUNTER,
        help_text="Records discarded for arriving below the watermark.",
        catches="Data loss in progress. Says nothing about whether the output is replayable.",
    ),
    Metric(
        name="srd_partition_watermark_deficit_ms",
        kind=GAUGE,
        help_text="How far the global watermark's driver leads the slowest partition's newest record.",
        catches=(
            "A partition falling behind, before the drops start. Crosses the "
            "allowed lateness exactly when its fresh records begin to be discarded."
        ),
    ),
    Metric(
        name="srd_batch_records",
        kind=GAUGE,
        help_text="Records in the most recent trigger.",
        catches="A trigger interval that has stopped matching the arrival rate.",
    ),
    Metric(
        name="srd_open_sessions",
        kind=GAUGE,
        help_text="Sessions currently open across all keys.",
        catches="A session gap so long that state never releases, which is a memory incident.",
    ),
    Metric(
        name="srd_contingent_records_total",
        kind=COUNTER,
        help_text="Records whose fate depends on where the batch boundaries fell.",
        catches="Loss of replayability, which no engine reports and which the drop counter misses.",
    ),
)


@dataclass(frozen=True, slots=True)
class AlertRule:
    """One alert, with the thing it must not be allowed to imply.

    `does_not_mean` is the field that makes the rule reviewable. Every alert
    gets read at three in the morning by somebody who will act on what they
    think it means, and the gap between what it fires on and what they think it
    means is where the outage gets longer instead of shorter.
    """

    name: str
    expression: str
    for_duration: str
    summary: str
    does_not_mean: str


ALERTS: tuple[AlertRule, ...] = (
    AlertRule(
        name="PartitionFallingBehind",
        expression="srd_partition_watermark_deficit_ms > 30000",
        for_duration="10m",
        summary="A partition's newest record is further behind the watermark's driver than the allowed lateness.",
        does_not_mean="That records are being dropped yet. It means the next one that arrives will be.",
    ),
    AlertRule(
        name="RecordsDropped",
        expression="increase(srd_records_dropped_total[15m]) > 0",
        for_duration="0m",
        summary="The job discarded records in the last fifteen minutes.",
        does_not_mean="That reprocessing will recover them identically. It will not.",
    ),
    AlertRule(
        name="OutputNoLongerReplayable",
        expression="increase(srd_contingent_records_total[1h]) > 0",
        for_duration="0m",
        summary="Some of the last hour's output cannot be reproduced by reprocessing.",
        does_not_mean="That the output is wrong. It means a second run would disagree with it.",
    ),
)


def metric_names() -> tuple[str, ...]:
    return tuple(metric.name for metric in METRICS)


def render_exposition(values: Mapping[str, float], *, metrics: Sequence[Metric] = METRICS) -> str:
    """Render a scrape body in the Prometheus text format.

    Refuses a value for a metric it does not know about, and refuses a negative
    value for a counter. Both are the kind of thing that gets noticed six months
    later as a dashboard that has been quietly wrong, and both are one line to
    catch here.
    """
    known = {metric.name: metric for metric in metrics}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise MetricError(f"no such metric: {', '.join(unknown)}")
    lines: list[str] = []
    for metric in metrics:
        if metric.name not in values:
            continue
        value = values[metric.name]
        if metric.kind.monotonic and value < 0:
            raise MetricError(f"{metric.name} is a counter and cannot be {value}")
        lines.append(f"# HELP {metric.name} {metric.help_text}")
        lines.append(f"# TYPE {metric.name} {metric.kind.name}")
        rendered = str(int(value)) if float(value).is_integer() else f"{value:g}"
        lines.append(f"{metric.name} {rendered}")
    return "\n".join(lines) + "\n" if lines else ""


def partition_deficit_ms(events: Iterable[LoggedEvent]) -> int:
    """The largest gap between the global running maximum and a partition's own.

    Computed from the log in offset order and nothing else, so it needs no
    batching and no run. At each record the global watermark is driven by the
    global running maximum, while the freshest thing that partition has produced
    is its own running maximum; the difference is how much head start the line
    has over that partition. When it exceeds the allowed lateness, that
    partition's next record arrives already condemned.

    Zero for a log with one partition, and zero under a per-partition policy,
    which is the honest way to say that the whole failure mode is a consequence
    of sharing one line across partitions that do not keep pace.

    The comparison is against every partition seen so far, not against the
    partition the current record belongs to. A first version measured only the
    current one and reported 900 milliseconds on a log whose real deficit was
    8,900, because a partition that has gone silent produces no records to
    trigger a measurement, and going silent is exactly the condition this gauge
    exists to catch.
    """
    global_max: int | None = None
    per_partition: dict[int, int] = {}
    worst = 0
    for event in events:
        per_partition[event.partition] = max(
            per_partition.get(event.partition, event.event_time_ms), event.event_time_ms
        )
        global_max = event.event_time_ms if global_max is None else max(global_max, event.event_time_ms)
        worst = max(worst, global_max - min(per_partition.values()))
    return worst
