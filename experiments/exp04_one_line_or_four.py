"""How much of the damage comes from sharing one watermark across four partitions.

A global watermark takes the highest stamp anywhere in the topic. A partition
that falls behind is measured against a line its own data never raised, so its
records arrive already condemned. A per-partition watermark tracks each
independently, which is what most engineers believe they have configured.

The setting is one boolean in the declaration. This measures what the boolean
is worth, on the same log, with nothing else changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srd.batching import ThroughputBatcher, WallClockBatcher, as_observed  # noqa: E402
from srd.bounds import classify  # noqa: E402
from srd.compare import compare  # noqa: E402
from srd.events import read_log  # noqa: E402
from srd.generator import generate  # noqa: E402
from srd.job import JobConfig, run  # noqa: E402
from srd.metrics import partition_deficit_ms  # noqa: E402
from srd.spec import PipelineSpec  # noqa: E402
from srd.watermark import WatermarkPolicy  # noqa: E402


def main() -> dict[str, object]:
    spec = PipelineSpec.from_file(ROOT / "specs" / "pipeline.toml")
    log = read_log(ROOT / "fixtures" / "log.json")
    events = generate()
    logged = as_observed([event.logged for event in events])

    rows: list[dict[str, object]] = []
    for per_partition in (False, True):
        config = JobConfig(
            window_ms=spec.window_ms,
            session_gap_ms=spec.session_gap_ms,
            watermark=WatermarkPolicy(
                allowed_lateness_ms=spec.allowed_lateness_ms, per_partition=per_partition
            ),
        )
        original = run(config, WallClockBatcher(interval_ms=spec.trigger_interval_ms), events)
        replayed = run(config, ThroughputBatcher(max_records=spec.replay_batch_records), logged)
        diff = compare(original, replayed)
        report = classify(log, config.watermark)
        rows.append(
            {
                "per_partition": per_partition,
                "original_dropped": len(original.dropped),
                "original_drop_rate": original.drop_rate,
                "contingent_records": len(report.contingent),
                "replay_stable_share": diff.replay_stable_share,
                "diverged_rows": len(diff.rows),
            }
        )

    shared, split = rows
    return {
        "partitions": len(log.partitions),
        "partition_deficit_ms": partition_deficit_ms(log),
        "allowed_lateness_ms": spec.allowed_lateness_ms,
        "rows": rows,
        "dropped_under_one_line": shared["original_dropped"],
        "dropped_under_four_lines": split["original_dropped"],
        "drops_the_boolean_causes": shared["original_dropped"] - split["original_dropped"],
        "contingent_under_one_line": shared["contingent_records"],
        "contingent_under_four_lines": split["contingent_records"],
        "stable_share_under_one_line": shared["replay_stable_share"],
        "stable_share_under_four_lines": split["replay_stable_share"],
        "diverged_rows_the_boolean_causes": shared["diverged_rows"] - split["diverged_rows"],
    }


if __name__ == "__main__":
    payload = main()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "exp04.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
