"""How the shipped result moves when the two settings that produced it move.

A single number from a single fixture is an anecdote. This sweeps the setting an
operator actually controls, the allowed lateness, and the property of the
delivery path they do not, the spread of arrival delays, and reports where the
result changes character.

It exists because the shipped fixture sits close to a cliff, and a repository
that quoted the headline without saying so would be quoting a number that looks
robust and is not. The cliff is at the point where the delivery spread passes
the allowed lateness: below it a few records are late, above it a late record
raises the watermark past the records behind it and the drops compound.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srd.batching import ThroughputBatcher, WallClockBatcher, as_observed  # noqa: E402
from srd.compare import Arm, compare  # noqa: E402
from srd.generator import GeneratorConfig, generate  # noqa: E402
from srd.job import JobConfig, run  # noqa: E402
from srd.spec import PipelineSpec  # noqa: E402
from srd.watermark import WatermarkPolicy  # noqa: E402

LATENESS_SWEEP = (5_000, 15_000, 30_000, 60_000, 120_000, 300_000)
JITTER_SWEEP = (5_000, 20_000, 30_000, 38_000, 45_000, 60_000)
RECORDS = 12_000


def _pair(spec: PipelineSpec, *, lateness_ms: int, jitter_ms: int) -> tuple[float, float, int, int]:
    events = generate(GeneratorConfig(records=RECORDS, jitter_ms=jitter_ms))
    config = JobConfig(
        window_ms=spec.window_ms,
        session_gap_ms=spec.session_gap_ms,
        watermark=WatermarkPolicy(allowed_lateness_ms=lateness_ms),
    )
    original = run(config, WallClockBatcher(interval_ms=spec.trigger_interval_ms), events)
    replayed = run(
        config,
        ThroughputBatcher(max_records=spec.replay_batch_records),
        as_observed([event.logged for event in events]),
    )
    diff = compare(original, replayed)
    arms = diff.by_arm()
    return diff.replay_stable_share, original.drop_rate, len(diff.rows), arms[Arm.TIE_ORDER]


def main() -> dict[str, object]:
    spec = PipelineSpec.from_file(ROOT / "specs" / "pipeline.toml")
    by_lateness = []
    for lateness in LATENESS_SWEEP:
        stable, drop_rate, rows, ties = _pair(spec, lateness_ms=lateness, jitter_ms=38_000)
        by_lateness.append(
            {
                "allowed_lateness_ms": lateness,
                "replay_stable_share": stable,
                "original_drop_rate": drop_rate,
                "diverged_rows": rows,
                "rows_changed_by_ties": ties,
            }
        )
    by_jitter = []
    for jitter in JITTER_SWEEP:
        stable, drop_rate, rows, ties = _pair(spec, lateness_ms=spec.allowed_lateness_ms, jitter_ms=jitter)
        by_jitter.append(
            {
                "jitter_ms": jitter,
                "replay_stable_share": stable,
                "original_drop_rate": drop_rate,
                "diverged_rows": rows,
            }
        )

    best = max(by_lateness, key=lambda row: row["replay_stable_share"])
    worst = min(by_lateness, key=lambda row: row["replay_stable_share"])
    cliff = next(row for row in by_jitter if row["replay_stable_share"] < 0.6)
    return {
        "records_per_point": RECORDS,
        "by_lateness": by_lateness,
        "by_jitter": by_jitter,
        "best_lateness_ms": best["allowed_lateness_ms"],
        "best_stable_share": best["replay_stable_share"],
        "worst_lateness_ms": worst["allowed_lateness_ms"],
        "worst_stable_share": worst["replay_stable_share"],
        "cliff_jitter_ms": cliff["jitter_ms"],
        "stable_share_before_the_cliff": by_jitter[JITTER_SWEEP.index(cliff["jitter_ms"]) - 1][
            "replay_stable_share"
        ],
        "stable_share_at_the_cliff": cliff["replay_stable_share"],
        "shipped_jitter_ms": 38_000,
        "rows_the_most_generous_lateness_cannot_fix": best["diverged_rows"],
        "ties_the_most_generous_lateness_cannot_fix": best["rows_changed_by_ties"],
    }


if __name__ == "__main__":
    payload = main()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "exp03.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
