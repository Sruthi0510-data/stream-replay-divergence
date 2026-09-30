"""Whether any replay setting recovers the session table, and what the drop counter says.

The aggregate's divergence is a function of which records got dropped, so it
responds to allowed lateness. The session table's divergence is a function of
wall clock, and a replay's wall clock is not the original's.

This sweeps every replay setting there is, batch size and the time each batch
takes, and looks for one that reproduces the sessions the original run emitted. Then it
reports what the drop counter, which is the metric every team already has, read
at the setting the declaration actually ships: a number small enough that no
alert would fire, while the session table was losing almost all of its rows.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from srd.batching import ThroughputBatcher, as_observed  # noqa: E402
from srd.cli import _read_output  # noqa: E402
from srd.events import read_log  # noqa: E402
from srd.job import run  # noqa: E402
from srd.spec import PipelineSpec  # noqa: E402

BATCH_SIZES = (100, 500, 2_000, 5_000, 15_000, 30_000)
BATCH_DURATIONS_MS = (1, 10, 100, 1_000, 10_000)


def main() -> dict[str, object]:
    spec = PipelineSpec.from_file(ROOT / "specs" / "pipeline.toml")
    log = read_log(ROOT / "fixtures" / "log.json")
    original = _read_output(ROOT / "fixtures" / "original-output.json")
    events = as_observed(tuple(log))
    target = len(original.sessions)

    grid: list[dict[str, object]] = []
    for size in BATCH_SIZES:
        for duration in BATCH_DURATIONS_MS:
            result = run(spec.job, ThroughputBatcher(max_records=size, per_batch_ms=duration), events)
            grid.append(
                {
                    "batch_records": size,
                    "per_batch_ms": duration,
                    "sessions": len(result.sessions),
                    "dropped": len(result.dropped),
                    "wall_clock_span_ms": result.batch_count * duration,
                }
            )

    matches = [row for row in grid if row["sessions"] == target]
    best = max(grid, key=lambda row: row["sessions"])
    shipped = next(
        row
        for row in grid
        if row["batch_records"] == spec.replay_batch_records and row["per_batch_ms"] == 1
    )
    log_span_ms = log.span_ms()
    return {
        "target_sessions": target,
        "settings_tried": len(grid),
        "settings_that_reproduce_the_table": len(matches),
        "most_sessions_any_setting_produced": best["sessions"],
        "setting_that_came_closest": {
            "batch_records": best["batch_records"],
            "per_batch_ms": best["per_batch_ms"],
        },
        "wall_clock_the_closest_setting_spent_ms": best["wall_clock_span_ms"],
        "event_time_span_of_the_log_ms": log_span_ms,
        "slowdown_needed_to_match_the_original": log_span_ms / max(1, best["wall_clock_span_ms"]),
        "largest_drop_count_across_the_sweep": max(row["dropped"] for row in grid),
        "smallest_drop_count_across_the_sweep": min(row["dropped"] for row in grid),
        "dropped_at_the_shipped_setting": shipped["dropped"],
        "dropped_share_at_the_shipped_setting": shipped["dropped"] / len(log),
        "sessions_at_the_shipped_setting": shipped["sessions"],
        "grid": grid,
    }


if __name__ == "__main__":
    payload = main()
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "exp05.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "grid"}, indent=2))
