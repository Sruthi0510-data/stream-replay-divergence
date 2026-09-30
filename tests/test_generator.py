"""Tests for the synthetic traffic.

These tests do not check parameter values. They check the properties the
parameters exist to produce, because a fixture that has quietly lost one of
those properties still runs, still produces numbers, and demonstrates nothing.
"""

from __future__ import annotations

import pytest

from srd.batching import WallClockBatcher
from srd.events import EventLog
from srd.generator import KEY_NAMES, GeneratorConfig, generate
from srd.job import JobConfig, run
from srd.watermark import WatermarkPolicy


@pytest.fixture(scope="module")
def shipped():
    return generate()


def test_the_generator_refuses_zero_records():
    with pytest.raises(ValueError, match="records"):
        GeneratorConfig(records=0)


def test_the_generator_refuses_zero_partitions():
    with pytest.raises(ValueError, match="partitions"):
        GeneratorConfig(partitions=0)


def test_the_generator_refuses_a_zero_stamp_resolution():
    with pytest.raises(ValueError, match="stamp resolution"):
        GeneratorConfig(stamp_resolution_ms=0)


def test_the_generator_refuses_a_skew_share_above_one():
    with pytest.raises(ValueError, match="fraction"):
        GeneratorConfig(skewed_key_share=1.5)


def test_the_generator_refuses_a_negative_skew_share():
    with pytest.raises(ValueError, match="fraction"):
        GeneratorConfig(skewed_key_share=-0.1)


def test_the_generator_refuses_a_stall_at_the_very_start():
    with pytest.raises(ValueError, match="strictly inside"):
        GeneratorConfig(stall_at_fraction=0.0)


def test_the_generator_refuses_a_stall_at_the_very_end():
    with pytest.raises(ValueError, match="strictly inside"):
        GeneratorConfig(stall_at_fraction=1.0)


def test_the_generator_refuses_a_stall_partition_that_does_not_exist():
    with pytest.raises(ValueError, match="outside the partition count"):
        GeneratorConfig(partitions=2, stall_partition=5)


def test_the_generator_refuses_quiet_keys_it_has_never_heard_of():
    with pytest.raises(ValueError, match="quiet keys not in key set"):
        GeneratorConfig(quiet_keys=("nobody",))


def test_the_same_seed_gives_the_same_traffic():
    assert generate(GeneratorConfig(records=500)) == generate(GeneratorConfig(records=500))


def test_a_different_seed_gives_different_traffic():
    first = generate(GeneratorConfig(records=500, seed=1))
    second = generate(GeneratorConfig(records=500, seed=2))
    assert first != second


def test_calling_the_generator_with_no_config_uses_the_shipped_one(shipped):
    assert len(shipped) == GeneratorConfig().records


def test_offsets_are_dense_and_start_at_zero(shipped):
    assert [event.offset for event in shipped] == list(range(len(shipped)))


def test_the_log_is_a_legal_log(shipped):
    assert len(EventLog(events=tuple(event.logged for event in shipped))) == len(shipped)


def test_every_key_is_one_the_config_declared(shipped):
    assert {event.key for event in shipped} <= set(KEY_NAMES)


def test_every_partition_is_inside_the_configured_count(shipped):
    assert max(event.partition for event in shipped) < GeneratorConfig().partitions


def test_the_traffic_arrives_out_of_event_time_order(shipped):
    """First shipped property: the log is not sorted by stamp."""
    stamps = [event.event_time_ms for event in shipped]
    assert stamps != sorted(stamps)


def test_out_of_orderness_is_wide_enough_to_make_lateness_a_real_decision(shipped):
    """Second shipped property: the spread is comparable to the allowed lateness.

    If it were far smaller, no reasonable setting would drop anything and the
    fixture would demonstrate nothing. If it were far larger, every setting
    would drop everything and the fixture would demonstrate something trivial.
    """
    log = EventLog(events=tuple(event.logged for event in shipped))
    assert log.max_out_of_orderness_ms() > 30_000


def test_the_stalled_partition_arrives_much_later_than_the_others(shipped):
    """Third shipped property: one partition really does stall."""
    config = GeneratorConfig()
    stalled = [
        event.lateness_ms() for event in shipped if event.partition == config.stall_partition
    ]
    others = [event.lateness_ms() for event in shipped if event.partition != config.stall_partition]
    assert max(stalled) > max(others) + 50_000


def test_the_quiet_keys_really_do_go_quiet(shipped):
    """Fourth shipped property: there are gaps long enough to close a session."""
    config = GeneratorConfig()
    for key in config.quiet_keys:
        stamps = sorted(event.event_time_ms for event in shipped if event.key == key)
        gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
        assert max(gaps) >= config.quiet_gap_ms


def test_a_key_that_is_not_quiet_has_no_such_gap(shipped):
    config = GeneratorConfig()
    noisy = [key for key in KEY_NAMES if key not in config.quiet_keys]
    stamps = sorted(event.event_time_ms for event in shipped if event.key == noisy[0])
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert max(gaps) < config.quiet_gap_ms


def test_stamps_land_on_the_configured_resolution(shipped):
    """Fifth shipped property: producers stamp coarsely, so ties exist."""
    resolution = GeneratorConfig().stamp_resolution_ms
    assert all(event.event_time_ms % resolution == 0 for event in shipped)


def test_the_fixture_contains_genuine_stamp_ties_inside_one_window(shipped):
    config = JobConfig(
        window_ms=5 * 60 * 1_000,
        session_gap_ms=30_000,
        watermark=WatermarkPolicy(allowed_lateness_ms=30_000),
    )
    seen: dict[tuple[int, str, int], int] = {}
    ties = 0
    for event in shipped:
        identity = (event.event_time_ms // config.window_ms, event.key, event.event_time_ms)
        seen[identity] = seen.get(identity, 0) + 1
        if seen[identity] == 2:
            ties += 1
    assert ties > 100


def test_at_least_one_key_carries_a_skewed_clock(shipped):
    """A key whose stamps sit systematically behind its arrivals."""
    by_key: dict[str, list[int]] = {}
    for event in shipped:
        by_key.setdefault(event.key, []).append(event.lateness_ms())
    medians = {
        key: sorted(values)[len(values) // 2] for key, values in by_key.items()
    }
    assert max(medians.values()) > min(medians.values()) + 40_000


def test_the_shape_stream_and_the_delay_stream_are_independent():
    """Changing the delay distribution moves no record in event time.

    This is what lets an experiment attribute a change in output to the delivery
    path rather than to a different log, and it holds only because the generator
    draws shape and delay from two separate streams.
    """
    base = generate(GeneratorConfig(records=800, jitter_ms=1_000))
    shifted = generate(GeneratorConfig(records=800, jitter_ms=1_000, base_delay_ms=99_000))
    assert [event.logged for event in base] == [event.logged for event in shifted]
    assert [event.arrival_ms for event in base] != [event.arrival_ms for event in shifted]


def test_the_original_run_over_the_shipped_fixture_drops_a_meaningful_share(shipped):
    config = JobConfig(
        window_ms=5 * 60 * 1_000,
        session_gap_ms=30_000,
        watermark=WatermarkPolicy(allowed_lateness_ms=30_000),
    )
    result = run(config, WallClockBatcher(interval_ms=10_000), shipped)
    assert 0.05 < result.drop_rate < 0.20


def test_the_original_run_produces_more_than_one_session_per_quiet_key(shipped):
    config = JobConfig(
        window_ms=5 * 60 * 1_000,
        session_gap_ms=30_000,
        watermark=WatermarkPolicy(allowed_lateness_ms=30_000),
    )
    result = run(config, WallClockBatcher(interval_ms=10_000), shipped)
    per_key: dict[str, int] = {}
    for row in result.sessions:
        per_key[row.key] = per_key.get(row.key, 0) + 1
    assert all(per_key[key] > 1 for key in GeneratorConfig().quiet_keys)
