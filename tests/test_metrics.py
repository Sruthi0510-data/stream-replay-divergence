"""Tests for the exposed metrics and the alerts on them."""

from __future__ import annotations

import pytest

from srd.metrics import ALERTS, METRICS, MetricError, metric_names, render_exposition


def test_metric_names_are_unique():
    assert len(set(metric_names())) == len(METRICS)


def test_every_metric_name_is_prefixed():
    assert all(name.startswith("srd_") for name in metric_names())


def test_every_counter_name_ends_in_total():
    assert all(
        metric.name.endswith("_total") for metric in METRICS if metric.kind.monotonic
    )


def test_no_gauge_name_ends_in_total():
    assert not any(
        metric.name.endswith("_total") for metric in METRICS if not metric.kind.monotonic
    )


def test_every_metric_says_what_it_catches():
    assert all(metric.catches.strip() for metric in METRICS)


def test_every_alert_says_what_it_does_not_mean():
    """The field that makes an alert reviewable rather than merely present."""
    assert all(rule.does_not_mean.strip() for rule in ALERTS)


def test_every_alert_expression_mentions_a_metric_that_exists():
    names = metric_names()
    assert all(any(name in rule.expression for name in names) for rule in ALERTS)


def test_alert_names_are_unique():
    assert len({rule.name for rule in ALERTS}) == len(ALERTS)


def test_rendering_nothing_is_an_empty_body():
    assert render_exposition({}) == ""


def test_rendering_emits_help_type_and_value():
    body = render_exposition({"srd_records_dropped_total": 3})
    assert body.splitlines() == [
        "# HELP srd_records_dropped_total Records discarded for arriving below the watermark.",
        "# TYPE srd_records_dropped_total counter",
        "srd_records_dropped_total 3",
    ]


def test_rendering_keeps_the_declared_order_not_the_caller_order():
    body = render_exposition(
        {"srd_partition_watermark_deficit_ms": 1, "srd_records_processed_total": 2}
    )
    assert body.index("srd_records_processed_total") < body.index("srd_partition_watermark_deficit_ms")


def test_an_integral_float_renders_without_a_decimal_point():
    assert "srd_partition_watermark_deficit_ms 5\n" in render_exposition({"srd_partition_watermark_deficit_ms": 5.0})


def test_a_fractional_value_keeps_its_fraction():
    assert "srd_partition_watermark_deficit_ms 2.5\n" in render_exposition({"srd_partition_watermark_deficit_ms": 2.5})


def test_an_unknown_metric_is_refused():
    with pytest.raises(MetricError, match="no such metric: srd_made_up"):
        render_exposition({"srd_made_up": 1})


def test_every_unknown_metric_is_named():
    with pytest.raises(MetricError, match="srd_a, srd_b"):
        render_exposition({"srd_b": 1, "srd_a": 1})


def test_a_negative_counter_is_refused():
    with pytest.raises(MetricError, match="cannot be"):
        render_exposition({"srd_records_dropped_total": -1})


def test_a_negative_gauge_is_allowed():
    assert "srd_partition_watermark_deficit_ms -3" in render_exposition({"srd_partition_watermark_deficit_ms": -3})


def test_rendering_accepts_a_narrowed_metric_set():
    body = render_exposition({"srd_open_sessions": 4}, metrics=[METRICS[4]])
    assert "srd_open_sessions 4" in body


def test_the_drop_counter_says_what_it_cannot_tell_you():
    """The sentence that is the point of the module, pinned so it survives edits."""
    drop = next(metric for metric in METRICS if metric.name == "srd_records_dropped_total")
    assert "replayable" in drop.catches


def test_the_deficit_of_a_single_partition_log_is_zero(make_logged):
    """The failure mode belongs to sharing one line across partitions."""
    from srd.metrics import partition_deficit_ms

    assert partition_deficit_ms([make_logged(index, index * 10) for index in range(5)]) == 0


def test_the_deficit_of_an_empty_log_is_zero():
    from srd.metrics import partition_deficit_ms

    assert partition_deficit_ms([]) == 0


def test_the_deficit_measures_how_far_ahead_the_leading_partition_is(make_logged):
    from srd.metrics import partition_deficit_ms

    events = [
        make_logged(0, 1_000, partition=0),
        make_logged(1, 100, partition=1),
        make_logged(2, 9_000, partition=0),
    ]
    assert partition_deficit_ms(events) == 8_900


def test_the_deficit_does_not_fall_when_a_partition_catches_up(make_logged):
    from srd.metrics import partition_deficit_ms

    events = [
        make_logged(0, 9_000, partition=0),
        make_logged(1, 100, partition=1),
        make_logged(2, 9_000, partition=1),
    ]
    assert partition_deficit_ms(events) == 8_900


def test_a_records_own_stamp_never_counts_against_its_partition(make_logged):
    from srd.metrics import partition_deficit_ms

    assert partition_deficit_ms([make_logged(0, 5_000, partition=3)]) == 0


def test_the_shipped_log_exceeds_its_own_allowed_lateness(make_logged):
    """The alert condition, present in the fixture and asserted so it stays."""
    from pathlib import Path

    from srd.events import read_log
    from srd.metrics import partition_deficit_ms
    from srd.spec import PipelineSpec

    root = Path(__file__).resolve().parents[1]
    spec = PipelineSpec.from_file(root / "specs" / "pipeline.toml")
    log = read_log(root / "fixtures" / "log.json")
    assert partition_deficit_ms(log) > spec.allowed_lateness_ms
