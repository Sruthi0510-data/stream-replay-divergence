"""Tests for the closed form, including the brute-force check that pins it.

The classifier is a one-pass argument about every possible batching. An argument
like that is exactly the kind of thing that is wrong in a corner nobody thought
of, so it is checked against exhaustive enumeration on every small log a
property test can build.
"""

from __future__ import annotations

import random

import pytest

from srd.bounds import (
    EXHAUSTIVE_LIMIT,
    ExhaustiveLimitError,
    Fate,
    RewriteWindow,
    classify,
    enumerate_batchings,
    exhaustive_fates,
    rewrite_window,
)
from srd.events import EventLog, LoggedEvent
from srd.watermark import NEVER, WatermarkPolicy


def log(*stamps: int, partitions: tuple[int, ...] | None = None, key: str = "k") -> EventLog:
    parts = partitions or (0,) * len(stamps)
    return EventLog(
        events=tuple(
            LoggedEvent(offset=index, partition=parts[index], key=key, event_time_ms=stamp, payload=1)
            for index, stamp in enumerate(stamps)
        )
    )


def test_classifying_an_empty_log_gives_an_empty_report():
    report = classify(log(), WatermarkPolicy(allowed_lateness_ms=0))
    assert len(report) == 0 and report.contingent_share == 0.0


def test_the_first_record_is_always_stable():
    report = classify(log(5), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.fates[0].fate is Fate.STABLE_KEEP


def test_the_first_record_faces_the_sentinel_line():
    report = classify(log(5), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.fates[0].worst_line_ms == NEVER


def test_a_record_above_the_worst_line_is_stable():
    report = classify(log(100, 90), WatermarkPolicy(allowed_lateness_ms=20))
    assert report.fates[1].fate is Fate.STABLE_KEEP


def test_a_record_exactly_on_the_worst_line_is_stable():
    report = classify(log(100, 80), WatermarkPolicy(allowed_lateness_ms=20))
    assert report.fates[1].fate is Fate.STABLE_KEEP


def test_a_record_below_the_worst_line_is_contingent():
    report = classify(log(100, 79), WatermarkPolicy(allowed_lateness_ms=20))
    assert report.fates[1].fate is Fate.CONTINGENT


def test_the_worst_line_uses_the_running_maximum_not_the_previous_record():
    report = classify(log(100, 10, 20), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.fates[2].worst_line_ms == 100


def test_no_record_is_ever_certainly_dropped():
    """The result that makes reprocessing dangerous rather than merely lossy."""
    report = classify(log(1_000, 1, 2, 3, 4), WatermarkPolicy(allowed_lateness_ms=0))
    assert {fate.fate for fate in report.fates} <= {Fate.STABLE_KEEP, Fate.CONTINGENT}


def test_a_sorted_log_has_nothing_contingent():
    report = classify(log(1, 2, 3, 4, 5), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.contingent == ()


def test_generous_lateness_makes_everything_stable():
    report = classify(log(1_000, 1, 2), WatermarkPolicy(allowed_lateness_ms=10_000))
    assert report.contingent_share == 0.0


def test_the_contingent_share_is_over_every_record():
    report = classify(log(1_000, 1, 2, 3), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.contingent_share == pytest.approx(3 / 4)


def test_the_report_lists_contingent_offsets():
    report = classify(log(1_000, 1, 2_000, 5), WatermarkPolicy(allowed_lateness_ms=0))
    assert report.contingent_offsets() == (1, 3)


def test_the_report_lists_contingent_keys_once_and_sorted():
    events = EventLog(
        events=(
            LoggedEvent(offset=0, partition=0, key="z", event_time_ms=1_000, payload=1),
            LoggedEvent(offset=1, partition=0, key="b", event_time_ms=1, payload=1),
            LoggedEvent(offset=2, partition=0, key="a", event_time_ms=2, payload=1),
            LoggedEvent(offset=3, partition=0, key="b", event_time_ms=3, payload=1),
        )
    )
    report = classify(events, WatermarkPolicy(allowed_lateness_ms=0))
    assert report.contingent_keys() == ("a", "b")


def test_the_report_indexes_by_offset():
    report = classify(log(5, 6), WatermarkPolicy(allowed_lateness_ms=0))
    assert set(report.by_offset()) == {0, 1}


def test_the_report_remembers_the_policy_it_was_built_with():
    report = classify(log(5), WatermarkPolicy(allowed_lateness_ms=17, per_partition=True))
    assert (report.allowed_lateness_ms, report.per_partition) == (17, True)


def test_a_per_partition_policy_classifies_each_partition_independently():
    report = classify(log(1_000, 5, partitions=(0, 1)), WatermarkPolicy(allowed_lateness_ms=0, per_partition=True))
    assert report.contingent == ()


def test_a_global_policy_lets_one_partition_condemn_another():
    report = classify(log(1_000, 5, partitions=(0, 1)), WatermarkPolicy(allowed_lateness_ms=0))
    assert len(report.contingent) == 1


def test_classify_accepts_a_bare_sequence_of_events():
    events = log(1_000, 5).events
    assert len(classify(events, WatermarkPolicy(allowed_lateness_ms=0))) == 2


def test_enumerating_batchings_of_nothing_yields_nothing():
    assert list(enumerate_batchings(0)) == []


def test_enumerating_batchings_of_a_negative_count_yields_nothing():
    assert list(enumerate_batchings(-3)) == []


def test_one_record_has_one_batching():
    assert list(enumerate_batchings(1)) == [(1,)]


def test_three_records_have_four_batchings():
    assert sorted(enumerate_batchings(3)) == [(1, 1, 1), (1, 2), (2, 1), (3,)]


def test_the_number_of_batchings_doubles_with_each_record():
    for count in range(1, 9):
        assert len(list(enumerate_batchings(count))) == 2 ** (count - 1)


def test_every_batching_covers_every_record():
    for sizes in enumerate_batchings(6):
        assert sum(sizes) == 6


def test_the_exhaustive_checker_refuses_a_log_it_would_choke_on():
    big = log(*range(EXHAUSTIVE_LIMIT + 1))
    with pytest.raises(ExhaustiveLimitError, match="refuses"):
        exhaustive_fates(big, WatermarkPolicy(allowed_lateness_ms=0))


def test_the_exhaustive_checker_accepts_a_log_at_the_limit():
    at_limit = log(*range(EXHAUSTIVE_LIMIT))
    assert len(exhaustive_fates(at_limit, WatermarkPolicy(allowed_lateness_ms=0))) == EXHAUSTIVE_LIMIT


def test_the_exhaustive_checker_agrees_with_the_closed_form_on_a_worked_example():
    events = log(1_000, 5, 2_000, 1_500)
    policy = WatermarkPolicy(allowed_lateness_ms=100)
    closed = {fate.offset: fate.fate for fate in classify(events, policy).fates}
    assert closed == exhaustive_fates(events, policy)


@pytest.mark.parametrize("seed", range(40))
def test_the_closed_form_matches_brute_force_on_random_logs(seed: int):
    """The property test that earns the closed form its place.

    Random stamps, random partitions, random lateness, both watermark modes. If
    the one-pass argument is wrong anywhere, this finds it.
    """
    rng = random.Random(seed)
    count = rng.randrange(1, 11)
    events = EventLog(
        events=tuple(
            LoggedEvent(
                offset=index,
                partition=rng.randrange(3),
                key="k",
                event_time_ms=rng.randrange(0, 200),
                payload=1,
            )
            for index in range(count)
        )
    )
    for per_partition in (False, True):
        policy = WatermarkPolicy(allowed_lateness_ms=rng.randrange(0, 60), per_partition=per_partition)
        closed = {fate.offset: fate.fate for fate in classify(events, policy).fates}
        assert closed == exhaustive_fates(events, policy)


def test_a_rewrite_window_over_nothing_contingent_is_empty():
    report = classify(log(1, 2, 3), WatermarkPolicy(allowed_lateness_ms=0))
    window = rewrite_window(report, window_ms=1_000, emitted_rows=10)
    assert window.identities == () and window.span_ms == 0


def test_an_empty_rewrite_window_has_a_zero_reachable_share():
    report = classify(log(1, 2, 3), WatermarkPolicy(allowed_lateness_ms=0))
    assert rewrite_window(report, window_ms=1_000, emitted_rows=10).reachable_share == 0.0


def test_a_rewrite_window_names_the_rows_holding_contingent_records():
    report = classify(log(9_000, 100), WatermarkPolicy(allowed_lateness_ms=0))
    window = rewrite_window(report, window_ms=1_000, emitted_rows=4)
    assert window.identities == ((0, "k"),)


def test_a_rewrite_window_spans_from_the_first_row_to_the_end_of_the_last():
    report = classify(log(9_000, 100, 2_100), WatermarkPolicy(allowed_lateness_ms=0))
    window = rewrite_window(report, window_ms=1_000, emitted_rows=4)
    assert (window.earliest_ms, window.latest_ms, window.span_ms) == (0, 3_000, 3_000)


def test_a_rewrite_window_reports_its_share_of_the_emitted_table():
    report = classify(log(9_000, 100), WatermarkPolicy(allowed_lateness_ms=0))
    assert rewrite_window(report, window_ms=1_000, emitted_rows=4).reachable_share == 0.25


def test_a_rewrite_window_over_an_empty_table_has_a_zero_share():
    report = classify(log(9_000, 100), WatermarkPolicy(allowed_lateness_ms=0))
    assert rewrite_window(report, window_ms=1_000, emitted_rows=0).reachable_share == 0.0


def test_a_rewrite_window_is_frozen():
    window = RewriteWindow(identities=(), span_ms=0, earliest_ms=0, latest_ms=0, emitted_rows=0)
    with pytest.raises(AttributeError):
        window.span_ms = 5


_SHIPPED: list = []


def _shipped_runs():
    """The shipped fixture, one original run and several replays.

    Built here rather than in a fixture because only two tests need it and both
    of them are about the same discovery, so keeping the construction next to
    them saves a reader a trip to conftest. Memoised because building it twice
    costs more than the two tests together.
    """
    if _SHIPPED:
        return _SHIPPED[0]
    from srd.batching import ThroughputBatcher, WallClockBatcher, as_observed
    from srd.compare import compare as compare_runs
    from srd.generator import generate
    from srd.job import JobConfig
    from srd.job import run as run_job

    events = generate()
    config = JobConfig(
        window_ms=300_000,
        session_gap_ms=30_000,
        watermark=WatermarkPolicy(allowed_lateness_ms=30_000),
    )
    logged = [event.logged for event in events]
    original = run_job(config, WallClockBatcher(interval_ms=10_000), events)
    replays = [
        run_job(config, ThroughputBatcher(max_records=size), as_observed(logged))
        for size in (250, 1_000, 5_000, 20_000, 30_000)
    ]
    report = classify(EventLog(tuple(logged)), config.watermark)
    window = rewrite_window(report, window_ms=config.window_ms, emitted_rows=len(original.windows))
    _SHIPPED.append((original, replays, window, compare_runs))
    return _SHIPPED[0]


def test_the_window_bounds_what_two_replays_can_disagree_about():
    """The scope the classification actually covers.

    Five replays at batch sizes spanning two orders of magnitude, every pair
    compared. Not one differing row falls outside the window, which is what the
    theorem promises for a fixed processing order.
    """
    from itertools import combinations

    _, replays, window, compare_runs = _shipped_runs()
    reachable = set(window.identities)
    outside = [
        (diff.window_start_ms, diff.key)
        for left, right in combinations(replays, 2)
        for diff in compare_runs(left, right).rows
        if (diff.window_start_ms, diff.key) not in reachable
    ]
    assert outside == []


def test_the_window_does_not_bound_divergence_from_the_original_run():
    """The scope it does not cover, and the reason the module exists.

    The original processed in arrival order. That ordering is not in the log, so
    no function in this module can see it, and the rows that actually differ
    outnumber the reachable ones several times over. An earlier version of
    RewriteWindow claimed otherwise; this is the test that caught it, and it is
    written as an assertion about the gap rather than a comment about it so that
    a future change cannot quietly restore the wrong claim.
    """
    original, replays, window, compare_runs = _shipped_runs()
    reachable = set(window.identities)
    diverged = {(diff.window_start_ms, diff.key) for diff in compare_runs(original, replays[2]).rows}
    assert not diverged <= reachable
    assert len(diverged) > 4 * len(reachable)
