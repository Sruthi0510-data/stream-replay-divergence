"""Tests for the job under test.

The most important test in this file is the one that pins the order of
operations inside the batch loop. Every other result in the repository is a
consequence of it, and getting it backwards makes the job look deterministic
under replay.
"""

from __future__ import annotations

import pytest

from srd.batching import SingleBatch, ThroughputBatcher, WallClockBatcher, as_observed
from srd.job import (
    DropReason,
    JobConfig,
    JobError,
    RunResult,
    SessionRow,
    WindowRow,
    run,
    window_start,
)
from srd.watermark import WatermarkPolicy


def config(**overrides) -> JobConfig:
    base = {
        "window_ms": 1_000,
        "session_gap_ms": 500,
        "watermark": WatermarkPolicy(allowed_lateness_ms=0),
    }
    base.update(overrides)
    return JobConfig(**base)


def test_window_start_floors_to_the_window(make_observed):
    assert window_start(2_750, 1_000) == 2_000


def test_window_start_is_exact_on_a_boundary():
    assert window_start(3_000, 1_000) == 3_000


def test_window_start_floors_negative_stamps_downwards():
    """Truncation would pull a negative stamp towards zero and into the wrong window."""
    assert window_start(-1, 1_000) == -1_000


def test_window_start_refuses_a_zero_window():
    with pytest.raises(JobError, match="positive"):
        window_start(0, 0)


def test_a_config_refuses_a_zero_window():
    with pytest.raises(JobError, match="window size"):
        config(window_ms=0)


def test_a_config_refuses_a_zero_session_gap():
    with pytest.raises(JobError, match="session gap"):
        config(session_gap_ms=0)


def test_a_config_holds_no_trigger_setting():
    """The argument of the repository, asserted against the type.

    If the trigger interval were part of the job's definition, the divergence
    would be a configuration difference rather than a structural one.
    """
    assert not hasattr(config(), "interval_ms")
    assert not hasattr(config(), "max_records")


def test_running_nothing_produces_nothing():
    result = run(config(), SingleBatch(), [])
    assert result.windows == () and result.sessions == () and result.dropped == ()


def test_the_drop_rate_of_an_empty_run_is_zero():
    assert run(config(), SingleBatch(), []).drop_rate == 0.0


def test_records_are_aggregated_into_their_window(make_observed):
    events = [make_observed(0, 100, 0, payload=3), make_observed(1, 900, 0, payload=4)]
    result = run(config(), SingleBatch(), events)
    assert result.windows == (WindowRow(window_start_ms=0, key="k", count=2, total=7, last_payload=4),)


def test_records_in_different_windows_produce_different_rows(make_observed):
    events = [make_observed(0, 100, 0), make_observed(1, 1_100, 0)]
    result = run(config(), SingleBatch(), events)
    assert [row.window_start_ms for row in result.windows] == [0, 1_000]


def test_different_keys_produce_different_rows(make_observed):
    events = [make_observed(0, 100, 0, key="a"), make_observed(1, 100, 0, key="b")]
    result = run(config(), SingleBatch(), events)
    assert [row.key for row in result.windows] == ["a", "b"]


def test_window_rows_come_out_sorted(make_observed):
    events = [
        make_observed(0, 5_100, 0, key="b"),
        make_observed(1, 100, 0, key="b"),
        make_observed(2, 100, 0, key="a"),
    ]
    result = run(config(), SingleBatch(), events)
    assert [row.identity() for row in result.windows] == [(0, "a"), (0, "b"), (5_000, "b")]


def test_last_payload_is_the_highest_stamp_in_the_window(make_observed):
    events = [make_observed(0, 900, 0, payload=1), make_observed(1, 100, 0, payload=2)]
    result = run(config(), SingleBatch(), events)
    assert result.windows[0].last_payload == 1


def test_last_payload_breaks_stamp_ties_by_processing_order(make_observed):
    """The tie-order arm in miniature: same records, two orders, two answers."""
    first = make_observed(0, 500, 10, payload=7)
    second = make_observed(1, 500, 20, payload=9)
    by_offset = run(config(), SingleBatch(), [first, second])
    by_arrival = run(config(), WallClockBatcher(interval_ms=1_000), [second, first])
    assert by_offset.windows[0].last_payload == 9
    assert by_arrival.windows[0].last_payload == 9
    reversed_arrival = [
        make_observed(0, 500, 90, payload=7),
        make_observed(1, 500, 10, payload=9),
    ]
    assert run(config(), WallClockBatcher(interval_ms=1_000), reversed_arrival).windows[0].last_payload == 7


def test_a_record_shares_a_batch_with_the_record_that_would_condemn_it(make_observed):
    """The order of operations, pinned.

    The watermark in force is the one from previous batches. In one batch the
    late record survives; split, it does not. If the implementation advanced
    the watermark before testing the batch, both would drop.
    """
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    together = run(config(), SingleBatch(), events)
    split = run(config(), ThroughputBatcher(max_records=1), events)
    assert together.dropped == ()
    assert len(split.dropped) == 1


def test_a_dropped_record_carries_the_line_that_killed_it(make_observed):
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    dropped = result.dropped[0]
    assert (dropped.offset, dropped.event_time_ms, dropped.watermark_ms) == (1, 10, 10_000)


def test_a_dropped_record_names_the_batch_it_died_in(make_observed):
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert result.dropped[0].batch_index == 1


def test_a_dropped_record_has_exactly_one_reason(make_observed):
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert result.dropped[0].reason is DropReason.LATE_BEYOND_WATERMARK


def test_a_dropped_record_reaches_no_aggregate(make_observed):
    events = [make_observed(0, 10_000, 0, payload=5), make_observed(1, 10, 0, payload=99)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert all(row.total != 99 for row in result.windows)


def test_the_drop_rate_counts_drops_over_everything_seen(make_observed):
    events = [make_observed(0, 10_000, 0)] + [make_observed(index, 10, 0) for index in range(1, 4)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert result.drop_rate == pytest.approx(3 / 4)


def test_processed_counts_only_records_that_survived(make_observed):
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert result.processed == 1


def test_every_record_starts_a_session(make_observed):
    result = run(config(), SingleBatch(), [make_observed(0, 0, 0)])
    assert result.sessions == (
        SessionRow(key="k", session_index=0, records=1, first_event_time_ms=0, last_event_time_ms=0),
    )


def test_a_session_stays_open_while_batches_keep_arriving(make_observed):
    events = [make_observed(index, index * 10, index * 100) for index in range(5)]
    result = run(config(session_gap_ms=1_000), WallClockBatcher(interval_ms=100), events)
    assert len(result.sessions) == 1


def test_a_session_closes_after_a_processing_time_gap(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 10, 100_000)]
    result = run(config(session_gap_ms=500), WallClockBatcher(interval_ms=100), events)
    assert len(result.sessions) == 2


def test_closed_sessions_are_numbered_from_zero_per_key(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 10, 100_000)]
    result = run(config(session_gap_ms=500), WallClockBatcher(interval_ms=100), events)
    assert [row.session_index for row in result.sessions] == [0, 1]


def test_a_session_records_its_event_time_span(make_observed):
    events = [make_observed(0, 400, 0), make_observed(1, 100, 10), make_observed(2, 900, 20)]
    result = run(config(watermark=WatermarkPolicy(allowed_lateness_ms=10_000)), SingleBatch(), events)
    session = result.sessions[0]
    assert (session.first_event_time_ms, session.last_event_time_ms) == (100, 900)


def test_sessions_close_in_key_order_so_the_output_is_stable(make_observed):
    events = [
        make_observed(0, 0, 0, key="b"),
        make_observed(1, 0, 0, key="a"),
        make_observed(2, 0, 100_000, key="a"),
    ]
    result = run(config(session_gap_ms=500), WallClockBatcher(interval_ms=100), events)
    assert [row.identity() for row in result.sessions] == [("a", 0), ("a", 1), ("b", 0)]


def test_a_replay_collapses_every_session_into_one(make_observed, make_logged):
    """The session-merge arm, which is structural rather than probabilistic.

    Nine records spread over hours of wall clock become nine records inside a
    few milliseconds. No gap elapses and no allowed lateness affects it.
    """
    live = [make_observed(index, index * 10, index * 3_600_000) for index in range(9)]
    forgiving = config(session_gap_ms=1_000, watermark=WatermarkPolicy(allowed_lateness_ms=10**9))
    original = run(forgiving, WallClockBatcher(interval_ms=1_000), live)
    replayed = run(
        config(session_gap_ms=1_000, watermark=WatermarkPolicy(allowed_lateness_ms=10**9)),
        SingleBatch(),
        as_observed([event.logged for event in live]),
    )
    assert len(original.sessions) == 9
    assert len(replayed.sessions) == 1


def test_a_session_open_at_the_end_of_the_run_is_still_emitted(make_observed):
    result = run(config(session_gap_ms=10**9), SingleBatch(), [make_observed(0, 0, 0)])
    assert len(result.sessions) == 1


def test_dropped_records_do_not_extend_a_session(make_observed):
    events = [make_observed(0, 10_000, 0), make_observed(1, 10, 0)]
    result = run(config(), ThroughputBatcher(max_records=1), events)
    assert sum(row.records for row in result.sessions) == 1


def test_the_result_reports_how_many_batches_it_saw(make_observed):
    events = [make_observed(index, 0, 0) for index in range(6)]
    result = run(config(watermark=WatermarkPolicy(allowed_lateness_ms=10**9)), ThroughputBatcher(max_records=2), events)
    assert result.batch_count == 3


def test_the_watermark_trace_has_one_step_per_batch(make_observed):
    events = [make_observed(index, 0, 0) for index in range(6)]
    result = run(config(watermark=WatermarkPolicy(allowed_lateness_ms=10**9)), ThroughputBatcher(max_records=2), events)
    assert len(result.watermark_steps) == 3


def test_the_window_index_is_keyed_by_identity(make_observed):
    result = run(config(), SingleBatch(), [make_observed(0, 100, 0)])
    assert list(result.window_index()) == [(0, "k")]


def test_the_session_index_is_keyed_by_identity(make_observed):
    result = run(config(), SingleBatch(), [make_observed(0, 100, 0)])
    assert list(result.session_index()) == [("k", 0)]


def test_a_run_result_is_frozen():
    result = RunResult(windows=(), sessions=(), dropped=(), watermark_steps=(), batch_count=0, processed=0)
    with pytest.raises(AttributeError):
        result.processed = 4


def test_a_window_row_is_frozen():
    with pytest.raises(AttributeError):
        WindowRow(window_start_ms=0, key="k", count=1, total=1, last_payload=1).count = 2


def test_an_empty_batch_still_produces_a_watermark_step(make_observed):
    events = [make_observed(0, 0, 0), make_observed(1, 0, 900)]
    forgiving = config(watermark=WatermarkPolicy(allowed_lateness_ms=10**9))
    result = run(forgiving, WallClockBatcher(interval_ms=100), events)
    assert any(step.records == 0 for step in result.watermark_steps)


def test_a_per_partition_watermark_keeps_records_a_global_one_drops(make_observed):
    events = [
        make_observed(0, 10_000, 0, partition=0),
        make_observed(1, 10, 0, partition=1),
    ]
    global_run = run(config(), ThroughputBatcher(max_records=1), events)
    per_partition = run(
        config(watermark=WatermarkPolicy(allowed_lateness_ms=0, per_partition=True)),
        ThroughputBatcher(max_records=1),
        events,
    )
    assert len(global_run.dropped) == 1
    assert per_partition.dropped == ()
