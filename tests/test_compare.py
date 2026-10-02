"""Tests for the diff and its attribution.

The attribution order is the thing worth defending. A row whose record sets
differ is charged to the drop even when its last-value column also disagrees,
which keeps the tie-order count a floor on what would still be wrong after the
drop problem was fixed.
"""

from __future__ import annotations

import pytest

from srd.batching import SingleBatch, WallClockBatcher, as_observed
from srd.compare import Arm, Divergence, RowDiff, SessionDiff, compare
from srd.job import JobConfig, RunResult, SessionRow, WindowRow, run
from srd.watermark import WatermarkPolicy


def config(**overrides) -> JobConfig:
    base = {
        "window_ms": 1_000,
        "session_gap_ms": 500,
        "watermark": WatermarkPolicy(allowed_lateness_ms=0),
    }
    base.update(overrides)
    return JobConfig(**base)


def result(windows=(), sessions=()) -> RunResult:
    return RunResult(
        windows=tuple(windows),
        sessions=tuple(sessions),
        dropped=(),
        watermark_steps=(),
        batch_count=0,
        processed=0,
    )


def row(start: int = 0, key: str = "k", count: int = 1, total: int = 1, last: int = 1) -> WindowRow:
    return WindowRow(window_start_ms=start, key=key, count=count, total=total, last_payload=last)


def test_two_empty_runs_do_not_diverge():
    assert not compare(result(), result()).diverged


def test_the_stable_share_of_two_empty_runs_is_zero():
    assert compare(result(), result()).replay_stable_share == 0.0


def test_identical_rows_are_stable():
    diff = compare(result([row()]), result([row()]))
    assert diff.stable_rows == 1 and diff.rows == ()


def test_the_stable_share_of_two_identical_runs_is_one():
    assert compare(result([row()]), result([row()])).replay_stable_share == 1.0


def test_a_row_only_the_original_has_is_charged_to_only_original():
    diff = compare(result([row()]), result())
    assert diff.rows[0].arm is Arm.ONLY_ORIGINAL


def test_a_row_only_the_replay_has_is_charged_to_only_replay():
    diff = compare(result(), result([row()]))
    assert diff.rows[0].arm is Arm.ONLY_REPLAY


def test_a_differing_count_is_charged_to_the_drop():
    diff = compare(result([row(count=1, total=1)]), result([row(count=2, total=2)]))
    assert diff.rows[0].arm is Arm.LATE_DROP


def test_a_differing_total_at_the_same_count_is_charged_to_the_drop():
    diff = compare(result([row(count=2, total=5)]), result([row(count=2, total=7)]))
    assert diff.rows[0].arm is Arm.LATE_DROP


def test_a_differing_last_value_at_the_same_count_and_total_is_charged_to_the_tie():
    diff = compare(result([row(count=2, total=6, last=1)]), result([row(count=2, total=6, last=5)]))
    assert diff.rows[0].arm is Arm.TIE_ORDER


def test_a_row_with_both_problems_is_charged_only_to_the_drop():
    """The attribution order, pinned.

    Letting this row into the tie-order arm would destroy that arm's meaning as
    a floor on what remains wrong after the drop problem is fixed.
    """
    diff = compare(result([row(count=1, total=1, last=1)]), result([row(count=2, total=9, last=5)]))
    assert diff.by_arm()[Arm.TIE_ORDER] == 0
    assert diff.by_arm()[Arm.LATE_DROP] == 1


def test_a_row_diff_reports_the_signed_count_delta():
    diff = compare(result([row(count=2)]), result([row(count=5, total=5)]))
    assert diff.rows[0].count_delta() == 3


def test_a_row_diff_present_on_one_side_only_has_no_delta():
    diff = compare(result([row(count=9)]), result())
    assert diff.rows[0].count_delta() == 0


def test_a_replay_only_row_also_has_no_delta():
    diff = compare(result(), result([row(count=9)]))
    assert diff.rows[0].count_delta() == 0


def test_rows_come_out_sorted_by_identity():
    left = result([row(start=1_000, key="b"), row(start=0, key="a")])
    diff = compare(left, result())
    assert [(item.window_start_ms, item.key) for item in diff.rows] == [(0, "a"), (1_000, "b")]


def test_rows_for_an_arm_returns_only_that_arm():
    left = result([row(start=0, count=1), row(start=1_000, count=1)])
    right = result([row(start=0, count=2, total=2)])
    diff = compare(left, right)
    assert len(diff.rows_for(Arm.LATE_DROP)) == 1
    assert len(diff.rows_for(Arm.ONLY_ORIGINAL)) == 1


def test_by_arm_reports_every_arm_including_the_empty_ones():
    diff = compare(result([row()]), result([row()]))
    assert set(diff.by_arm()) == set(Arm)


def test_the_stable_share_uses_the_union_as_its_denominator():
    """A replay that invents rows has not reproduced the table either."""
    left = result([row(start=0)])
    right = result([row(start=0), row(start=1_000), row(start=2_000)])
    assert compare(left, right).replay_stable_share == pytest.approx(1 / 3)


def test_the_union_row_count_is_reported():
    left = result([row(start=0)])
    right = result([row(start=1_000)])
    diff = compare(left, right)
    assert (diff.original_rows, diff.replay_rows, diff.union_rows) == (1, 1, 2)


def test_a_key_losing_sessions_is_reported():
    left = result(sessions=[SessionRow("a", 0, 1, 0, 0), SessionRow("a", 1, 1, 0, 0)])
    right = result(sessions=[SessionRow("a", 0, 2, 0, 0)])
    diff = compare(left, right)
    assert diff.sessions == (SessionDiff(key="a", original_sessions=2, replay_sessions=1),)


def test_a_key_with_the_same_session_count_is_not_reported():
    rows = [SessionRow("a", 0, 1, 0, 0)]
    assert compare(result(sessions=rows), result(sessions=rows)).sessions == ()


def test_session_loss_is_counted_per_key():
    left = result(sessions=[SessionRow("a", index, 1, 0, 0) for index in range(4)])
    right = result(sessions=[SessionRow("a", 0, 4, 0, 0)])
    assert compare(left, right).sessions[0].lost == 3


def test_a_key_that_gained_sessions_has_lost_none():
    left = result(sessions=[SessionRow("a", 0, 1, 0, 0)])
    right = result(sessions=[SessionRow("a", 0, 1, 0, 0), SessionRow("a", 1, 1, 0, 0)])
    assert compare(left, right).sessions[0].lost == 0


def test_a_key_present_only_in_the_replay_is_reported():
    right = result(sessions=[SessionRow("b", 0, 1, 0, 0)])
    assert compare(result(), right).sessions[0].key == "b"


def test_the_session_loss_share_is_over_the_original_count():
    left = result(sessions=[SessionRow("a", index, 1, 0, 0) for index in range(10)])
    right = result(sessions=[SessionRow("a", 0, 10, 0, 0)])
    assert compare(left, right).session_loss_share == pytest.approx(0.9)


def test_the_session_loss_share_of_a_run_with_no_sessions_is_zero():
    assert compare(result(), result()).session_loss_share == 0.0


def test_a_replay_that_gained_sessions_has_no_loss_share():
    left = result(sessions=[SessionRow("a", 0, 1, 0, 0)])
    right = result(sessions=[SessionRow("a", 0, 1, 0, 0), SessionRow("a", 1, 1, 0, 0)])
    assert compare(left, right).session_loss_share == 0.0


def test_session_merges_are_counted_in_the_arm_table():
    left = result(sessions=[SessionRow("a", 0, 1, 0, 0), SessionRow("a", 1, 1, 0, 0)])
    right = result(sessions=[SessionRow("a", 0, 2, 0, 0)])
    assert compare(left, right).by_arm()[Arm.SESSION_MERGE] == 1


def test_a_run_diverges_if_only_its_sessions_changed():
    left = result(sessions=[SessionRow("a", 0, 1, 0, 0), SessionRow("a", 1, 1, 0, 0)])
    right = result(sessions=[SessionRow("a", 0, 2, 0, 0)])
    assert compare(left, right).diverged


def test_a_divergence_is_frozen():
    diff = Divergence(
        rows=(),
        sessions=(),
        stable_rows=0,
        original_rows=0,
        replay_rows=0,
        union_rows=0,
        original_sessions=0,
        replay_sessions=0,
    )
    with pytest.raises(AttributeError):
        diff.stable_rows = 3


def test_a_row_diff_is_frozen():
    with pytest.raises(AttributeError):
        RowDiff(0, "k", Arm.LATE_DROP, None, None).key = "z"


def test_end_to_end_a_replay_of_a_late_heavy_run_diverges(make_observed):
    live = [
        make_observed(0, 10_000, 0, payload=1),
        make_observed(1, 10, 1_000, payload=2),
        make_observed(2, 20, 2_000, payload=3),
    ]
    original = run(config(), WallClockBatcher(interval_ms=500), live)
    replayed = run(config(), SingleBatch(), as_observed([event.logged for event in live]))
    assert compare(original, replayed).diverged
