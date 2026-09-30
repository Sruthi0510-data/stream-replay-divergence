"""Tests for the command line, and above all for its exit codes.

The codes are the interface. A finding and a refusal are different answers and a
pipeline that cannot tell them apart will page somebody for both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from srd.cli import BAD_INPUT, DIVERGED, OK, UNDETERMINED, main

SPEC = """
[job]
window_ms = 1000
session_gap_ms = 500
allowed_lateness_ms = {lateness}
per_partition = false

[runtime]
trigger_interval_ms = 600000
replay_batch_records = 1
"""


@pytest.fixture
def workspace(tmp_path: Path):
    def _build(*, lateness: int = 0, events=None, windows=None, sessions=None):
        spec = tmp_path / "spec.toml"
        spec.write_text(SPEC.format(lateness=lateness), encoding="utf-8")
        log = tmp_path / "log.json"
        rows = events if events is not None else [
            {"offset": 0, "partition": 0, "key": "k", "event_time_ms": 9_000, "payload": 1},
            {"offset": 1, "partition": 0, "key": "k", "event_time_ms": 10, "payload": 2},
        ]
        log.write_text(json.dumps({"events": rows}), encoding="utf-8")
        output = tmp_path / "out.json"
        output.write_text(
            json.dumps(
                {
                    "windows": windows if windows is not None else [],
                    "sessions": sessions if sessions is not None else [],
                }
            ),
            encoding="utf-8",
        )
        return ["--spec", str(spec), "--log", str(log), "--output", str(output)]

    return _build


def test_no_command_is_a_usage_error():
    with pytest.raises(SystemExit):
        main([])


def test_generate_writes_a_log_and_a_table(tmp_path: Path, capsys):
    spec = tmp_path / "spec.toml"
    spec.write_text(SPEC.format(lateness=30_000), encoding="utf-8")
    log, output = tmp_path / "log.json", tmp_path / "out.json"
    code = main(
        [
            "--spec", str(spec), "--log", str(log), "--output", str(output),
            "generate", "--records", "300",
        ]
    )
    assert code == OK
    assert json.loads(log.read_text())["events"]
    assert "windows" in json.loads(output.read_text())


def test_generate_says_the_log_does_not_record_the_drops(tmp_path: Path, capsys):
    spec = tmp_path / "spec.toml"
    spec.write_text(SPEC.format(lateness=0), encoding="utf-8")
    main(
        [
            "--spec", str(spec), "--log", str(tmp_path / "l.json"),
            "--output", str(tmp_path / "o.json"), "generate", "--records", "300",
        ]
    )
    assert "the log does not say so" in capsys.readouterr().out


def test_generate_creates_the_fixture_directory(tmp_path: Path):
    spec = tmp_path / "spec.toml"
    spec.write_text(SPEC.format(lateness=0), encoding="utf-8")
    log = tmp_path / "nested" / "deeper" / "log.json"
    main(
        [
            "--spec", str(spec), "--log", str(log), "--output", str(tmp_path / "o.json"),
            "generate", "--records", "50",
        ]
    )
    assert log.exists()


def test_run_reports_what_it_did(workspace, capsys):
    assert main(workspace() + ["run"]) == OK
    assert "window rows" in capsys.readouterr().out


def test_run_with_a_single_batch_drops_nothing(workspace, capsys):
    main(workspace() + ["run", "--batching", "single"])
    assert "dropped        0" in capsys.readouterr().out


def test_run_with_small_batches_drops_the_late_record(workspace, capsys):
    main(workspace() + ["run", "--batching", "replay"])
    assert "dropped        1" in capsys.readouterr().out


def test_a_live_batching_is_refused_with_a_reason(workspace, capsys):
    """The category error an early version allowed, now refused."""
    assert main(workspace() + ["run", "--batching", "live"]) == BAD_INPUT
    assert "one batch wearing a live run's name" in capsys.readouterr().err


def test_a_missing_spec_is_a_bad_input(tmp_path: Path, capsys):
    code = main(["--spec", str(tmp_path / "nope.toml"), "run"])
    assert code == BAD_INPUT
    assert "no such spec" in capsys.readouterr().err


def test_a_missing_log_is_a_bad_input(tmp_path: Path, workspace, capsys):
    args = workspace()
    args[args.index("--log") + 1] = str(tmp_path / "absent.json")
    assert main(args + ["run"]) == BAD_INPUT


def test_a_replay_that_reproduces_the_table_exits_zero(workspace, capsys):
    windows = [
        {"window_start_ms": 0, "key": "k", "count": 1, "total": 2, "last_payload": 2},
        {"window_start_ms": 9_000, "key": "k", "count": 1, "total": 1, "last_payload": 1},
    ]
    sessions = [
        {
            "key": "k",
            "session_index": 0,
            "records": 2,
            "first_event_time_ms": 10,
            "last_event_time_ms": 9_000,
        }
    ]
    args = workspace(lateness=100_000, windows=windows, sessions=sessions)
    assert main(args + ["replay", "--batching", "single"]) == OK
    assert "the replay reproduced the table" in capsys.readouterr().out


def test_a_replay_that_does_not_reproduce_the_table_exits_three(workspace, capsys):
    assert main(workspace() + ["replay"]) == DIVERGED
    assert "did not reproduce" in capsys.readouterr().out


def test_a_replay_reports_every_arm(workspace, capsys):
    main(workspace() + ["replay"])
    out = capsys.readouterr().out
    for label in ("dropped records", "tie ordering", "only in the replay", "only in the table"):
        assert label in out


def test_a_missing_output_table_is_a_bad_input(tmp_path: Path, workspace, capsys):
    args = workspace()
    args[args.index("--output") + 1] = str(tmp_path / "absent.json")
    assert main(args + ["replay"]) == BAD_INPUT
    assert "no such output" in capsys.readouterr().err


def test_an_output_table_that_is_not_json_is_a_bad_input(tmp_path: Path, workspace, capsys):
    args = workspace()
    Path(args[args.index("--output") + 1]).write_text("{", encoding="utf-8")
    assert main(args + ["replay"]) == BAD_INPUT
    assert "not valid JSON" in capsys.readouterr().err


def test_an_output_table_missing_its_sections_is_a_bad_input(tmp_path: Path, workspace, capsys):
    args = workspace()
    Path(args[args.index("--output") + 1]).write_text(json.dumps({"windows": []}), encoding="utf-8")
    assert main(args + ["replay"]) == BAD_INPUT
    assert "not an output table" in capsys.readouterr().err


def test_bounds_reports_the_contingent_share(workspace, capsys):
    assert main(workspace() + ["bounds"]) == OK
    assert "contingent on batching       1" in capsys.readouterr().out


def test_bounds_says_what_it_does_not_bound(workspace, capsys):
    main(workspace() + ["bounds"])
    assert "not replay against the original run" in capsys.readouterr().out


def test_bounds_with_nothing_contingent_exits_four(workspace, capsys):
    """A refusal, not a finding: the tool has nothing to say about this log."""
    events = [
        {"offset": 0, "partition": 0, "key": "k", "event_time_ms": 10, "payload": 1},
        {"offset": 1, "partition": 0, "key": "k", "event_time_ms": 20, "payload": 1},
    ]
    assert main(workspace(events=events) + ["bounds"]) == UNDETERMINED


def test_an_undetermined_bounds_still_says_what_it_cannot_tell_you(workspace, capsys):
    events = [{"offset": 0, "partition": 0, "key": "k", "event_time_ms": 10, "payload": 1}]
    main(workspace(events=events) + ["bounds"])
    assert "says nothing about how either compares" in capsys.readouterr().out


def test_witness_explains_a_stable_record(workspace, capsys):
    assert main(workspace() + ["witness", "0"]) == OK
    assert "no batching of this log can discard this record" in capsys.readouterr().out


def test_witness_explains_a_contingent_record(workspace, capsys):
    assert main(workspace() + ["witness", "1"]) == DIVERGED
    assert "nothing in the log says which of the two ran" in capsys.readouterr().out


def test_witness_on_an_unknown_offset_is_a_bad_input(workspace, capsys):
    assert main(workspace() + ["witness", "99"]) == BAD_INPUT
    assert "is not in" in capsys.readouterr().err


def test_metrics_prints_a_scrape_body(workspace, capsys):
    assert main(workspace() + ["metrics"]) == OK
    out = capsys.readouterr().out
    assert "# TYPE srd_records_dropped_total counter" in out


def test_metrics_includes_the_count_no_engine_exposes(workspace, capsys):
    main(workspace() + ["metrics"])
    assert "srd_contingent_records_total" in capsys.readouterr().out


def test_metrics_of_an_empty_log_reports_zeroes(workspace, capsys):
    assert main(workspace(events=[]) + ["metrics"]) == OK
    assert "srd_records_processed_total 0" in capsys.readouterr().out


def test_the_four_exit_codes_are_distinct():
    assert len({OK, BAD_INPUT, DIVERGED, UNDETERMINED}) == 4
