"""Tests for the log record types.

The point these tests defend is that the log and the observation are different
types and that the difference is not decorative.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from srd.events import (
    EventLog,
    MalformedLogError,
    ObservedEvent,
    observed_order,
    read_log,
    write_log,
)


def test_a_logged_event_has_no_arrival_time(make_logged):
    event = make_logged(0, 100)
    assert not hasattr(event, "arrival_ms")


def test_an_observed_event_forwards_every_logged_field(make_observed):
    event = make_observed(3, 100, 250, key="a", partition=2, payload=9)
    assert (event.offset, event.partition, event.key, event.event_time_ms, event.payload) == (
        3,
        2,
        "a",
        100,
        9,
    )


def test_lateness_is_arrival_minus_event_time(make_observed):
    assert make_observed(0, 100, 380).lateness_ms() == 280


def test_lateness_can_be_negative_when_a_producer_clock_runs_fast(make_observed):
    assert make_observed(0, 500, 100).lateness_ms() == -400


def test_logged_events_are_frozen(make_logged):
    with pytest.raises(AttributeError):
        make_logged(0, 1).offset = 4


def test_observed_events_are_frozen(make_observed):
    with pytest.raises(AttributeError):
        make_observed(0, 1, 2).arrival_ms = 9


def test_to_json_round_trips_every_field(make_logged):
    event = make_logged(7, 1234, key="z", partition=3, payload=42)
    assert event.to_json() == {
        "offset": 7,
        "partition": 3,
        "key": "z",
        "event_time_ms": 1234,
        "payload": 42,
    }


def test_a_log_refuses_offsets_out_of_order(make_logged):
    with pytest.raises(MalformedLogError, match="offset order"):
        EventLog(events=(make_logged(2, 0), make_logged(1, 0)))


def test_a_log_refuses_duplicate_offsets(make_logged):
    with pytest.raises(MalformedLogError, match="duplicate"):
        EventLog(events=(make_logged(1, 0), make_logged(1, 5)))


def test_an_empty_log_is_legal(make_logged):
    assert len(EventLog(events=())) == 0


def test_a_log_reports_its_partitions_sorted(make_logged):
    log = EventLog(
        events=(
            make_logged(0, 0, partition=3),
            make_logged(1, 0, partition=1),
            make_logged(2, 0, partition=3),
        )
    )
    assert log.partitions == (1, 3)


def test_a_log_reports_its_keys_sorted(make_logged):
    log = EventLog(events=(make_logged(0, 0, key="b"), make_logged(1, 0, key="a")))
    assert log.keys == ("a", "b")


def test_iterating_a_log_yields_its_events_in_order(make_logged):
    events = (make_logged(0, 5), make_logged(1, 2))
    assert tuple(EventLog(events=events)) == events


def test_span_of_an_empty_log_is_zero():
    assert EventLog(events=()).span_ms() == 0


def test_span_is_the_event_time_range(make_logged):
    log = EventLog(events=(make_logged(0, 40), make_logged(1, 10), make_logged(2, 90)))
    assert log.span_ms() == 80


def test_out_of_orderness_is_zero_for_a_sorted_log(make_logged):
    log = EventLog(events=(make_logged(0, 1), make_logged(1, 2), make_logged(2, 3)))
    assert log.max_out_of_orderness_ms() == 0


def test_out_of_orderness_is_zero_for_an_empty_log():
    assert EventLog(events=()).max_out_of_orderness_ms() == 0


def test_out_of_orderness_measures_against_the_running_maximum(make_logged):
    log = EventLog(events=(make_logged(0, 100), make_logged(1, 30), make_logged(2, 60)))
    assert log.max_out_of_orderness_ms() == 70


def test_out_of_orderness_keeps_the_largest_gap_not_the_last(make_logged):
    log = EventLog(events=(make_logged(0, 100), make_logged(1, 10), make_logged(2, 95)))
    assert log.max_out_of_orderness_ms() == 90


def test_write_then_read_round_trips(tmp_path: Path, make_logged):
    events = [make_logged(0, 10, key="a"), make_logged(1, 20, key="b", partition=1)]
    path = tmp_path / "log.json"
    write_log(path, events)
    assert read_log(path).events == tuple(events)


def test_compact_writing_is_smaller_than_readable_writing(tmp_path: Path, make_logged):
    events = [make_logged(index, index * 10) for index in range(50)]
    compact, readable = tmp_path / "c.json", tmp_path / "r.json"
    write_log(compact, events, compact=True)
    write_log(readable, events, compact=False)
    assert compact.stat().st_size < readable.stat().st_size


def test_reading_a_missing_file_is_a_malformed_log_error(tmp_path: Path):
    with pytest.raises(MalformedLogError, match="no such log"):
        read_log(tmp_path / "absent.json")


def test_reading_invalid_json_says_so(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text("{oh no", encoding="utf-8")
    with pytest.raises(MalformedLogError, match="not valid JSON"):
        read_log(path)


def test_reading_json_without_an_events_key_says_so(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"records": []}), encoding="utf-8")
    with pytest.raises(MalformedLogError, match="no 'events' key"):
        read_log(path)


def test_reading_a_top_level_list_says_so(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(MalformedLogError, match="no 'events' key"):
        read_log(path)


def test_reading_events_that_are_not_a_list_says_so(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"events": {}}), encoding="utf-8")
    with pytest.raises(MalformedLogError, match="not a list"):
        read_log(path)


def test_reading_an_event_that_is_not_an_object_says_which_one(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"events": [1]}), encoding="utf-8")
    with pytest.raises(MalformedLogError, match="event 0 is not an object"):
        read_log(path)


def test_reading_an_event_missing_fields_names_all_of_them(tmp_path: Path):
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"events": [{"offset": 0}]}), encoding="utf-8")
    with pytest.raises(MalformedLogError, match="partition, key, event_time_ms, payload"):
        read_log(path)


def test_observed_order_sorts_by_arrival(make_observed):
    late = make_observed(0, 0, 500)
    early = make_observed(1, 0, 100)
    assert observed_order([late, early]) == (early, late)


def test_observed_order_breaks_arrival_ties_by_offset(make_observed):
    second = make_observed(5, 0, 100)
    first = make_observed(2, 0, 100)
    assert observed_order([second, first]) == (first, second)


def test_observed_order_of_nothing_is_nothing():
    assert observed_order([]) == ()


def test_the_log_is_the_only_thing_a_replay_can_read(make_observed):
    """The structural claim, asserted rather than argued.

    Stripping an observed record to what the log holds leaves no field from
    which the arrival time could be recovered, not even approximately.
    """
    observed = make_observed(0, 1_000, 4_321)
    logged = observed.logged
    recovered = {name: getattr(logged, name) for name in ("offset", "partition", "key", "event_time_ms", "payload")}
    assert 4_321 not in recovered.values()
    assert not any(isinstance(value, int) and value == observed.arrival_ms for value in recovered.values())


def test_two_records_with_the_same_log_shape_can_have_different_arrivals(make_logged):
    logged = make_logged(0, 100)
    assert ObservedEvent(logged=logged, arrival_ms=200) != ObservedEvent(logged=logged, arrival_ms=900)
