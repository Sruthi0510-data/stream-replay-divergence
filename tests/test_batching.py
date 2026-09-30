"""Tests for how records get grouped into units of work.

The grouping is the hinge. These tests pin the two things that make the rest of
the repository true: a live run groups by wall clock and a replay cannot, and
the grouping a replay produces is enormously coarser.
"""

from __future__ import annotations

import pytest

from srd.batching import (
    Batch,
    BatchingError,
    SingleBatch,
    ThroughputBatcher,
    WallClockBatcher,
    as_observed,
)


def test_a_wall_clock_batcher_refuses_a_zero_interval():
    with pytest.raises(BatchingError, match="positive"):
        WallClockBatcher(interval_ms=0)


def test_a_wall_clock_batcher_refuses_a_negative_interval():
    with pytest.raises(BatchingError, match="positive"):
        WallClockBatcher(interval_ms=-5)


def test_a_throughput_batcher_refuses_a_zero_size():
    with pytest.raises(BatchingError, match="max records"):
        ThroughputBatcher(max_records=0)


def test_a_throughput_batcher_refuses_a_zero_duration():
    with pytest.raises(BatchingError, match="per batch duration"):
        ThroughputBatcher(max_records=5, per_batch_ms=0)


def test_a_single_batch_refuses_a_zero_duration():
    with pytest.raises(BatchingError, match="per batch duration"):
        SingleBatch(per_batch_ms=0)


def test_batching_nothing_yields_nothing():
    assert WallClockBatcher(interval_ms=10).batches([]) == ()
    assert ThroughputBatcher(max_records=10).batches([]) == ()
    assert SingleBatch().batches([]) == ()


def test_a_batch_reports_its_length(make_observed):
    batch = Batch(index=0, wall_clock_ms=1, events=(make_observed(0, 0, 0), make_observed(1, 0, 0)))
    assert len(batch) == 2


def test_an_empty_batch_has_no_maximum_event_time():
    assert Batch(index=0, wall_clock_ms=1, events=()).max_event_time_ms is None


def test_a_batch_reports_its_highest_stamp(make_observed):
    batch = Batch(
        index=0,
        wall_clock_ms=1,
        events=(make_observed(0, 40, 0), make_observed(1, 90, 0), make_observed(2, 10, 0)),
    )
    assert batch.max_event_time_ms == 90


def test_wall_clock_batching_puts_records_in_the_trigger_they_arrived_in(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 0, 5), make_observed(2, 0, 12)]
    batches = WallClockBatcher(interval_ms=10).batches(events)
    assert [tuple(event.offset for event in batch.events) for batch in batches] == [(0, 1), (2,)]


def test_wall_clock_batching_stamps_each_trigger_with_its_boundary(make_observed):
    events = [make_observed(0, 0, 100), make_observed(1, 0, 135)]
    batches = WallClockBatcher(interval_ms=20).batches(events)
    assert [batch.wall_clock_ms for batch in batches] == [120, 140]


def test_wall_clock_batching_emits_empty_triggers_by_default(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 0, 95)]
    batches = WallClockBatcher(interval_ms=10).batches(events)
    assert len(batches) == 10
    assert sum(1 for batch in batches if not batch.events) == 8


def test_wall_clock_batching_can_skip_empty_triggers(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 0, 95)]
    batches = WallClockBatcher(interval_ms=10, emit_empty=False).batches(events)
    assert len(batches) == 2


def test_skipping_empty_triggers_renumbers_without_gaps(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 0, 95)]
    batches = WallClockBatcher(interval_ms=10, emit_empty=False).batches(events)
    assert [batch.index for batch in batches] == [0, 1]


def test_wall_clock_batching_indexes_triggers_from_zero(make_observed):
    events = [make_observed(index, 0, index * 25) for index in range(4)]
    batches = WallClockBatcher(interval_ms=10).batches(events)
    assert [batch.index for batch in batches] == list(range(len(batches)))


def test_wall_clock_batching_orders_by_arrival_not_offset(make_observed):
    late_first = make_observed(0, 0, 500)
    early_second = make_observed(1, 0, 100)
    batches = WallClockBatcher(interval_ms=1_000).batches([late_first, early_second])
    assert tuple(event.offset for event in batches[0].events) == (1, 0)


def test_wall_clock_batching_covers_every_record(make_observed):
    events = [make_observed(index, 0, index * 3) for index in range(50)]
    batches = WallClockBatcher(interval_ms=7).batches(events)
    seen = [event.offset for batch in batches for event in batch.events]
    assert sorted(seen) == list(range(50))


def test_throughput_batching_chunks_by_count(make_observed):
    events = [make_observed(index, 0, 0) for index in range(7)]
    batches = ThroughputBatcher(max_records=3).batches(events)
    assert [len(batch) for batch in batches] == [3, 3, 1]


def test_throughput_batching_uses_offset_order_not_arrival(make_observed):
    events = [make_observed(1, 0, 10), make_observed(0, 0, 900)]
    batches = ThroughputBatcher(max_records=10).batches(events)
    assert tuple(event.offset for event in batches[0].events) == (0, 1)


def test_throughput_batching_advances_the_clock_by_one_step_per_batch(make_observed):
    events = [make_observed(index, 0, 0) for index in range(5)]
    batches = ThroughputBatcher(max_records=2, per_batch_ms=4).batches(events)
    assert [batch.wall_clock_ms for batch in batches] == [4, 8, 12]


def test_a_single_batch_holds_everything(make_observed):
    events = [make_observed(index, 0, index) for index in range(40)]
    batches = SingleBatch().batches(events)
    assert len(batches) == 1
    assert len(batches[0]) == 40


def test_a_single_batch_is_in_offset_order(make_observed):
    events = [make_observed(2, 0, 0), make_observed(0, 0, 0), make_observed(1, 0, 0)]
    batches = SingleBatch().batches(events)
    assert tuple(event.offset for event in batches[0].events) == (0, 1, 2)


def test_a_replay_batching_is_coarser_than_the_live_one_it_replaces(make_observed):
    """The quantitative form of the claim the module docstring makes.

    Same records, same arrival times, two batchers. The live one produces two
    orders of magnitude more batches, and no configuration of the replay
    recovers them, because the replay has no arrival times to trigger on.
    """
    events = [make_observed(index, index * 100, index * 100) for index in range(2_000)]
    live = WallClockBatcher(interval_ms=100).batches(events)
    replay = ThroughputBatcher(max_records=500).batches(events)
    assert len(live) == 2_000
    assert len(replay) == 4
    assert len(live) > 100 * len(replay)


def test_as_observed_stamps_every_record_with_the_same_arrival(make_logged):
    events = [make_logged(index, index * 5) for index in range(4)]
    observed = as_observed(events)
    assert {event.arrival_ms for event in observed} == {0}


def test_as_observed_accepts_a_chosen_arrival(make_logged):
    observed = as_observed([make_logged(0, 0)], arrival_ms=99)
    assert observed[0].arrival_ms == 99


def test_as_observed_preserves_every_logged_field(make_logged):
    logged = make_logged(3, 77, key="q", partition=2, payload=8)
    assert as_observed([logged])[0].logged == logged


def test_as_observed_of_nothing_is_nothing():
    assert as_observed([]) == ()
