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

`srd_watermark_lag_ms` is the gauge that would have caught it. It is the distance
between the watermark and the highest stamp the job has seen, and it grows
whenever a partition falls behind. Alerting on it is alerting on the cause.

`srd_contingent_records_total` is the one no engine exposes, because no engine
computes it. It is the count from the bounds module, and it is the number that
answers "can we reprocess this window", which is the question actually asked
during an incident.

The exposition format is the Prometheus text format, written by hand. A client
library would be one dependency and one more thing to install offline, and the
format is four lines of rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "Metric",
    "MetricKind",
    "METRICS",
    "AlertRule",
    "ALERTS",
    "render_exposition",
    "metric_names",
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
        name="srd_watermark_lag_ms",
        kind=GAUGE,
        help_text="Distance from the watermark to the highest event time seen.",
        catches="A partition falling behind, before the drops start. The cause, not the symptom.",
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
        name="WatermarkLagGrowing",
        expression="srd_watermark_lag_ms > 60000",
        for_duration="10m",
        summary="The watermark is more than a minute behind the newest record.",
        does_not_mean="That records are being dropped yet. It means they are about to be.",
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
