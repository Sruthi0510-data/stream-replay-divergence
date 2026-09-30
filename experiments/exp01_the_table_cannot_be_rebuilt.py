"""What a replay of the shipped log produces, against the table the original run wrote.

This is the headline experiment and it answers one question: if the original run
were lost tomorrow and somebody rebuilt the table by reprocessing the topic, how
much of the rebuilt table would match?

The answer is written to reports/exp01.json and quoted in the README. Nothing
here is estimated; every figure is a count of rows produced by code in this
repository, run over a fixture that ships with it.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))

from srd.batching import ThroughputBatcher, as_observed  # noqa: E402
from srd.cli import _read_output  # noqa: E402
from srd.compare import Arm, compare  # noqa: E402
from srd.events import read_log  # noqa: E402
from srd.job import run  # noqa: E402
from srd.spec import PipelineSpec  # noqa: E402


def main() -> dict[str, object]:
    spec = PipelineSpec.from_file(ROOT / "specs" / "pipeline.toml")
    log = read_log(ROOT / "fixtures" / "log.json")
    original = _read_output(ROOT / "fixtures" / "original-output.json")
    replayed = run(
        spec.job,
        ThroughputBatcher(max_records=spec.replay_batch_records),
        as_observed(tuple(log)),
    )
    diff = compare(original, replayed)
    arms = diff.by_arm()
    result = {
        "records": len(log),
        "original_rows": diff.original_rows,
        "replay_rows": diff.replay_rows,
        "stable_rows": diff.stable_rows,
        "replay_stable_share": diff.replay_stable_share,
        "diverged_rows": len(diff.rows),
        "rows_changed_by_drops": arms[Arm.LATE_DROP],
        "rows_changed_by_ties": arms[Arm.TIE_ORDER],
        "rows_only_in_replay": arms[Arm.ONLY_REPLAY],
        "rows_only_in_original": arms[Arm.ONLY_ORIGINAL],
        "keys_with_a_changed_row": len({row.key for row in diff.rows}),
        "original_sessions": diff.original_sessions,
        "replay_sessions": diff.replay_sessions,
        "session_loss_share": diff.session_loss_share,
        "replay_dropped": len(replayed.dropped),
        "replay_batches": replayed.batch_count,
    }
    return result


if __name__ == "__main__":
    payload = main()
    (ROOT / "docs" / "experiments").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "experiments" / "exp01.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
