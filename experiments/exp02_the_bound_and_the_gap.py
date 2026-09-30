"""The bound the log can compute, and the distance between it and the answer.

An operator holding a topic can classify every record in one pass and learn
exactly which output rows two reprocessings can disagree about. That bound is
correct and it is tight, and this experiment checks both claims against five
replays at batch sizes spanning two orders of magnitude.

Then it measures the quantity the operator actually wanted, which is how far a
reprocessing lands from the run that produced the table they already have, and
finds it several times larger. The bound is not wrong. It is about the wrong
pair of runs, because the original processed in arrival order and that ordering
was never written down.
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srd.batching import ThroughputBatcher, as_observed  # noqa: E402
from srd.bounds import classify, rewrite_window  # noqa: E402
from srd.cli import _read_output  # noqa: E402
from srd.compare import compare  # noqa: E402
from srd.events import read_log  # noqa: E402
from srd.job import run  # noqa: E402
from srd.spec import PipelineSpec  # noqa: E402

BATCH_SIZES = (250, 1_000, 5_000, 20_000, 30_000)


def main() -> dict[str, object]:
    spec = PipelineSpec.from_file(ROOT / "specs" / "pipeline.toml")
    log = read_log(ROOT / "fixtures" / "log.json")
    original = _read_output(ROOT / "fixtures" / "original-output.json")
    events = as_observed(tuple(log))

    report = classify(log, spec.job.watermark)
    window = rewrite_window(
        report, window_ms=spec.window_ms, emitted_rows=len(original.windows)
    )
    reachable = set(window.identities)

    replays = [run(spec.job, ThroughputBatcher(max_records=size), events) for size in BATCH_SIZES]

    replay_pairs = 0
    replay_diff_rows = 0
    replay_rows_outside = 0
    for left, right in combinations(replays, 2):
        replay_pairs += 1
        for row in compare(left, right).rows:
            replay_diff_rows += 1
            if (row.window_start_ms, row.key) not in reachable:
                replay_rows_outside += 1

    against_original = compare(original, replays[BATCH_SIZES.index(spec.replay_batch_records)])
    original_rows_outside = sum(
        1 for row in against_original.rows if (row.window_start_ms, row.key) not in reachable
    )

    return {
        "contingent_records": len(report.contingent),
        "contingent_share": report.contingent_share,
        "contingent_keys": len(report.contingent_keys()),
        "contingent_key_names": list(report.contingent_keys()),
        "reachable_rows": len(reachable),
        "reachable_share": window.reachable_share,
        "reachable_span_minutes": window.span_ms // 60_000,
        "replay_batch_sizes": list(BATCH_SIZES),
        "replay_pairs_compared": replay_pairs,
        "replay_rows_that_differ": replay_diff_rows,
        "replay_rows_outside_the_bound": replay_rows_outside,
        "original_rows_that_differ": len(against_original.rows),
        "original_rows_outside_the_bound": original_rows_outside,
        "bound_understates_by": len(against_original.rows) / len(reachable),
        "keys_the_bound_names": len(report.contingent_keys()),
        "keys_that_actually_differ": len({row.key for row in against_original.rows}),
    }


if __name__ == "__main__":
    payload = main()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "exp02.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
