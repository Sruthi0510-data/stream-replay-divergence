"""Tests for the watermark.

Three properties get pinned here, and every later result rests on them: the
watermark is monotonic, it is computed from finished batches and applied to the
next one, and an empty batch leaves it where it was.
"""

from __future__ import annotations

import pytest

from srd.batching import Batch
from srd.watermark import (
    NEVER,
    WatermarkError,
    WatermarkPolicy,
    WatermarkState,
    WatermarkStep,
    trace,
)


def batch(index: int, events, wall_clock_ms: int = 0) -> Batch:
    return Batch(index=index, wall_clock_ms=wall_clock_ms or index + 1, events=tuple(events))


def test_a_policy_refuses_negative_lateness():
    with pytest.raises(WatermarkError, match="negative"):
        WatermarkPolicy(allowed_lateness_ms=-1)


def test_zero_lateness_is_legal():
    assert WatermarkPolicy(allowed_lateness_ms=0).allowed_lateness_ms == 0


def test_the_line_before_anything_is_seen_is_below_everything():
    assert WatermarkPolicy(allowed_lateness_ms=10).line_for(None) == NEVER


def test_the_sentinel_is_below_any_plausible_stamp():
    """Zero is a real stamp in a relative-time fixture, so the sentinel is not zero."""
    assert NEVER < -(10**15)


def test_the_line_is_the_highest_stamp_minus_the_slack():
    assert WatermarkPolicy(allowed_lateness_ms=30).line_for(100) == 70


def test_a_fresh_state_has_no_line():
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=5))
    assert state.line() == NEVER


def test_nothing_is_late_against_a_fresh_state(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=5))
    assert not state.is_late(0, -10**12)


def test_observing_a_batch_advances_the_line(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=20))
    state.observe(batch(0, [make_observed(0, 100, 0)]))
    assert state.line() == 80


def test_observing_an_empty_batch_leaves_the_line_alone(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=20))
    state.observe(batch(0, [make_observed(0, 100, 0)]))
    state.observe(batch(1, []))
    assert state.line() == 80


def test_the_line_never_moves_backwards(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0))
    state.observe(batch(0, [make_observed(0, 500, 0)]))
    state.observe(batch(1, [make_observed(1, 10, 0)]))
    assert state.line() == 500


def test_one_record_with_a_wrong_clock_raises_the_line_permanently(make_observed):
    """The monotonic clamp, stated as the operational failure it causes."""
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=10))
    state.observe(batch(0, [make_observed(0, 10_000, 0)]))
    assert state.is_late(0, 500)
    for index in range(1, 20):
        state.observe(batch(index, [make_observed(index, 500 + index, 0)]))
    assert state.is_late(0, 500)


def test_a_record_at_exactly_the_line_survives(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=20))
    state.observe(batch(0, [make_observed(0, 100, 0)]))
    assert not state.is_late(0, 80)


def test_a_record_one_below_the_line_does_not(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=20))
    state.observe(batch(0, [make_observed(0, 100, 0)]))
    assert state.is_late(0, 79)


def test_a_batch_takes_its_highest_stamp_not_its_last(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0))
    state.observe(batch(0, [make_observed(0, 900, 0), make_observed(1, 100, 0)]))
    assert state.line() == 900


def test_a_global_watermark_lets_one_partition_drag_the_line_up(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0, per_partition=False))
    state.observe(batch(0, [make_observed(0, 1_000, 0, partition=1)]))
    assert state.is_late(0, 500)


def test_a_per_partition_watermark_does_not(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0, per_partition=True))
    state.observe(batch(0, [make_observed(0, 1_000, 0, partition=1)]))
    assert not state.is_late(0, 500)
    assert state.is_late(1, 500)


def test_per_partition_lines_advance_independently(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0, per_partition=True))
    state.observe(
        batch(0, [make_observed(0, 400, 0, partition=0), make_observed(1, 900, 0, partition=1)])
    )
    assert (state.line(0), state.line(1)) == (400, 900)


def test_a_global_line_is_shared_across_partitions(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0))
    state.observe(
        batch(0, [make_observed(0, 400, 0, partition=0), make_observed(1, 900, 0, partition=1)])
    )
    assert state.line(0) == state.line(1) == 900


def test_an_unseen_partition_has_no_line_under_a_per_partition_policy(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0, per_partition=True))
    state.observe(batch(0, [make_observed(0, 900, 0, partition=1)]))
    assert state.line(7) == NEVER


def test_a_later_batch_with_a_lower_maximum_does_not_lower_a_partition_line(make_observed):
    state = WatermarkState(policy=WatermarkPolicy(allowed_lateness_ms=0, per_partition=True))
    state.observe(batch(0, [make_observed(0, 900, 0, partition=1)]))
    state.observe(batch(1, [make_observed(1, 100, 0, partition=1)]))
    assert state.line(1) == 900


def test_trace_of_nothing_is_nothing():
    assert trace(WatermarkPolicy(allowed_lateness_ms=0), []) == ()


def test_the_first_step_of_a_trace_has_no_line(make_observed):
    steps = trace(WatermarkPolicy(allowed_lateness_ms=0), [batch(0, [make_observed(0, 5, 0)])])
    assert steps[0].line_ms == NEVER


def test_a_trace_reports_the_line_in_force_when_each_batch_started(make_observed):
    batches = [
        batch(0, [make_observed(0, 100, 0)]),
        batch(1, [make_observed(1, 200, 0)]),
        batch(2, [make_observed(2, 300, 0)]),
    ]
    steps = trace(WatermarkPolicy(allowed_lateness_ms=10), batches)
    assert [step.line_ms for step in steps] == [NEVER, 90, 190]


def test_a_trace_counts_the_records_a_batch_would_lose(make_observed):
    batches = [
        batch(0, [make_observed(0, 1_000, 0)]),
        batch(1, [make_observed(1, 10, 0), make_observed(2, 20, 0), make_observed(3, 5_000, 0)]),
    ]
    steps = trace(WatermarkPolicy(allowed_lateness_ms=0), batches)
    assert [step.dropped for step in steps] == [0, 2]


def test_a_trace_reports_batch_sizes(make_observed):
    batches = [batch(0, [make_observed(0, 1, 0), make_observed(1, 2, 0)]), batch(1, [])]
    steps = trace(WatermarkPolicy(allowed_lateness_ms=0), batches)
    assert [step.records for step in steps] == [2, 0]


def test_a_trace_keeps_batch_indices_and_clocks(make_observed):
    batches = [Batch(index=4, wall_clock_ms=77, events=(make_observed(0, 1, 0),))]
    steps = trace(WatermarkPolicy(allowed_lateness_ms=0), batches)
    assert (steps[0].batch_index, steps[0].wall_clock_ms) == (4, 77)


def test_an_empty_batch_in_a_trace_reports_the_partition_zero_line(make_observed):
    batches = [batch(0, [make_observed(0, 100, 0)]), batch(1, [])]
    steps = trace(WatermarkPolicy(allowed_lateness_ms=0), batches)
    assert steps[1].line_ms == 100


def test_a_watermark_step_is_frozen():
    step = WatermarkStep(batch_index=0, wall_clock_ms=0, line_ms=0, records=0, dropped=0)
    with pytest.raises(AttributeError):
        step.line_ms = 5


def test_the_same_records_in_one_batch_drop_nothing(make_observed):
    """The property the whole bounds argument rests on, in miniature.

    Two records, the second far behind the first. Split across batches the
    second dies; in one batch it lives. Nothing about either record changed.
    """
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    policy = WatermarkPolicy(allowed_lateness_ms=0)
    split = trace(policy, [batch(0, events[:1]), batch(1, events[1:])])
    together = trace(policy, [batch(0, events)])
    assert sum(step.dropped for step in split) == 1
    assert sum(step.dropped for step in together) == 0
