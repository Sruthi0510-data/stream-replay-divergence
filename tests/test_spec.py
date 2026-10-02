"""Tests for the pipeline declaration.

The declaration exists so that the settings which change what the output means
are reviewable. A declaration that silently accepts a typo would defeat that, so
most of these tests are about refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srd.spec import PipelineSpec, SpecError

GOOD = """
[job]
window_ms = 1000
session_gap_ms = 500
allowed_lateness_ms = 100
per_partition = false

[runtime]
trigger_interval_ms = 50
replay_batch_records = 200
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "spec.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_good_spec_loads(tmp_path: Path):
    spec = PipelineSpec.from_file(write(tmp_path, GOOD))
    assert spec.window_ms == 1_000 and spec.replay_batch_records == 200


def test_a_spec_builds_the_job_config(tmp_path: Path):
    spec = PipelineSpec.from_file(write(tmp_path, GOOD))
    assert spec.job.watermark.allowed_lateness_ms == 100


def test_a_spec_carries_the_per_partition_choice_into_the_policy(tmp_path: Path):
    spec = PipelineSpec.from_file(write(tmp_path, GOOD.replace("per_partition = false", "per_partition = true")))
    assert spec.job.watermark.per_partition is True


def test_a_missing_spec_says_so(tmp_path: Path):
    with pytest.raises(SpecError, match="no such spec"):
        PipelineSpec.from_file(tmp_path / "absent.toml")


def test_invalid_toml_says_so(tmp_path: Path):
    with pytest.raises(SpecError, match="not valid TOML"):
        PipelineSpec.from_file(write(tmp_path, "[job\n"))


def test_an_unknown_section_is_refused(tmp_path: Path):
    with pytest.raises(SpecError, match="unknown sections: tuning"):
        PipelineSpec.from_file(write(tmp_path, GOOD + "\n[tuning]\nx = 1\n"))


def test_an_unknown_job_key_is_refused(tmp_path: Path):
    """A typo that is silently accepted is how a job runs on a default nobody chose."""
    with pytest.raises(SpecError, match="unknown keys: alowed_lateness_ms"):
        PipelineSpec.from_file(write(tmp_path, GOOD + "alowed_lateness_ms = 5\n"))


def test_an_unknown_runtime_key_is_refused(tmp_path: Path):
    with pytest.raises(SpecError, match="unknown keys: batch_size"):
        PipelineSpec.from_file(write(tmp_path, GOOD + "batch_size = 5\n"))


def test_a_missing_key_is_named(tmp_path: Path):
    text = GOOD.replace("session_gap_ms = 500\n", "")
    with pytest.raises(SpecError, match="missing: session_gap_ms"):
        PipelineSpec.from_file(write(tmp_path, text))


def test_every_missing_key_is_named_at_once(tmp_path: Path):
    with pytest.raises(SpecError, match="allowed_lateness_ms, per_partition"):
        half = (
            "[job]\nwindow_ms = 1\nsession_gap_ms = 1\n\n"
            "[runtime]\ntrigger_interval_ms = 1\nreplay_batch_records = 1\n"
        )
        PipelineSpec.from_file(write(tmp_path, half))


def test_a_value_of_the_wrong_type_is_refused(tmp_path: Path):
    text = GOOD.replace("window_ms = 1000", 'window_ms = "wide"')
    with pytest.raises(SpecError, match="wrong type"):
        PipelineSpec.from_file(write(tmp_path, text))


def test_a_zero_trigger_interval_is_refused(tmp_path: Path):
    text = GOOD.replace("trigger_interval_ms = 50", "trigger_interval_ms = 0")
    with pytest.raises(SpecError, match="trigger interval"):
        PipelineSpec.from_file(write(tmp_path, text))


def test_a_zero_replay_batch_is_refused(tmp_path: Path):
    text = GOOD.replace("replay_batch_records = 200", "replay_batch_records = 0")
    with pytest.raises(SpecError, match="replay batch"):
        PipelineSpec.from_file(write(tmp_path, text))


def test_the_shipped_spec_loads():
    spec = PipelineSpec.from_file(Path(__file__).resolve().parents[1] / "specs" / "pipeline.toml")
    assert spec.allowed_lateness_ms == 30_000


def test_the_shipped_spec_sets_lateness_tighter_than_the_observed_spread():
    """The deliberate misconfiguration, asserted so it cannot be tidied away.

    If someone widens the lateness to make the numbers look better, this fails
    and the README's headline stops being about anything.
    """
    from srd.events import EventLog
    from srd.generator import generate

    spec = PipelineSpec.from_file(Path(__file__).resolve().parents[1] / "specs" / "pipeline.toml")
    log = EventLog(events=tuple(event.logged for event in generate()))
    assert log.max_out_of_orderness_ms() > spec.allowed_lateness_ms
