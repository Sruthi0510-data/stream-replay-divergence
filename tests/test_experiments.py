"""Tests that each experiment runs and reports what the documents quote.

These are not tests of the experiments' conclusions, which are measurements and
would be circular to assert. They check that each one still runs offline, still
produces every key the receipts pipeline reads, and still produces values of the
right shape, so a refactor that quietly drops a field fails here rather than in
a stale README six commits later.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = sorted((ROOT / "experiments").glob("exp*.py"))


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_there_are_five_experiments():
    assert len(EXPERIMENTS) == 5


@pytest.mark.parametrize("path", EXPERIMENTS, ids=lambda path: path.stem)
def test_an_experiment_runs_and_returns_a_mapping(path: Path):
    payload = load(path).main()
    assert isinstance(payload, dict) and payload


@pytest.mark.parametrize("path", EXPERIMENTS, ids=lambda path: path.stem)
def test_an_experiment_returns_only_json_safe_values(path: Path):
    import json

    json.dumps(load(path).main())


def test_the_headline_experiment_reports_every_field_the_readme_quotes():
    payload = load(ROOT / "experiments" / "exp01_the_table_cannot_be_rebuilt.py").main()
    for field in (
        "replay_stable_share",
        "rows_changed_by_drops",
        "rows_changed_by_ties",
        "rows_only_in_replay",
        "rows_only_in_original",
        "original_sessions",
        "replay_sessions",
    ):
        assert field in payload


def test_the_replay_loses_no_row_it_had(make_logged):
    """A claim the README makes in words, pinned as a number.

    Reprocessing does not lose rows. It adds them, and rewrites the ones it
    keeps, which is a harder failure to notice than losing them would be.
    """
    payload = load(ROOT / "experiments" / "exp01_the_table_cannot_be_rebuilt.py").main()
    assert payload["rows_only_in_original"] == 0
    assert payload["replay_rows"] > payload["original_rows"]


def test_the_bound_holds_across_every_replay_pair():
    payload = load(ROOT / "experiments" / "exp02_the_bound_and_the_gap.py").main()
    assert payload["replay_rows_outside_the_bound"] == 0
    assert payload["replay_pairs_compared"] == 10


def test_the_bound_does_not_hold_against_the_original():
    payload = load(ROOT / "experiments" / "exp02_the_bound_and_the_gap.py").main()
    assert payload["original_rows_outside_the_bound"] > 0


def test_no_replay_setting_reproduces_the_session_table():
    payload = load(
        ROOT / "experiments" / "exp05_no_batch_size_brings_the_sessions_back.py"
    ).main()
    assert payload["settings_that_reproduce_the_table"] == 0
    assert payload["settings_tried"] == 30
