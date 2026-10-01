"""Capture this repository's own commands and commit the transcript.

The command list is the README's claims, in the order the README makes them. If
a claim changes, the list changes with it, and the capture fails the build when
a command does not exit the way the README says it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_terminal import Step, capture  # noqa: E402

PY = sys.executable

SELF_REFERENTIAL = (
    "--deselect",
    "tests/test_tools.py::test_every_captured_command_exited_as_expected",
    "--deselect",
    "tests/test_tools.py::test_the_proof_of_work_transcript_covers_the_commands_the_readme_shows",
)
"""The tests that read the transcript this run is about to overwrite.

Capturing them creates a cycle: a transcript recording a failing suite makes
these tests fail, which makes the suite fail, which is what the next capture
records. They still run in the full suite and in CI.
"""

STEPS = [
    Step(
        label="the suite, with branch coverage",
        argv=[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=srd",
              "--cov-branch", "--cov-report=term-missing", "--cov-fail-under=100",
              *SELF_REFERENTIAL],
        max_lines=17,
        tail_lines=4,
    ),
    Step(
        label="classify every record from the log alone",
        argv=[PY, "-m", "srd.cli", "bounds"],
        max_lines=9,
    ),
    Step(
        label="rebuild the table from the topic and diff it",
        argv=[PY, "-m", "srd.cli", "replay"],
        expect=3,
        max_lines=12,
    ),
    Step(
        label="one record whose fate the log does not determine",
        argv=[PY, "-m", "srd.cli", "witness", "15179"],
        expect=3,
        max_lines=9,
    ),
    Step(
        label="a live batching cannot be rebuilt, and is refused",
        argv=[PY, "-m", "srd.cli", "run", "--batching", "live"],
        expect=2,
        max_lines=4,
    ),
    Step(
        label="every published figure re-checked against the prose",
        argv=[PY, "tools/check_numbers.py"],
        max_lines=3,
    ),
]


def main() -> int:
    return capture(
        STEPS,
        title=("stream-replay-divergence: captured by tools/proof_of_work.py, exit codes as\n               "
               "returned. The suite run deselects the tests that read this transcript."),
        out_svg=ROOT / "docs" / "img" / "proof-of-work.svg",
        out_json=ROOT / "docs" / "proof-of-work.json",
    )


if __name__ == "__main__":
    raise SystemExit(main())
