"""Re-measure every published figure and write docs/metrics.json.

Nothing is typed by hand. The test count and the coverage come from pytest's
machine readable reports rather than from a parsed progress line, and every
other figure comes from an experiment's JSON, which is produced by running the
experiment.

Anchors are the other half. A checker that searches for digits passes while the
sentence around them has gone stale, so each metric carries the two or three
words a document must use with the value substituted in. Anchors are short,
because a phrase longer than a few words straddles a wrapped line and then
matches nothing while the prose it guards is correct. They are also specific:
"{} rows" would match every sentence in the repository that counts rows and
report a correct figure as stale a dozen times over.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_tables import table_values  # noqa: E402

DOCS = ROOT / "docs"
EXPERIMENTS = ROOT / "experiments"
EXPERIMENT_OUTPUT = DOCS / "experiments"
JUNIT = ROOT / "junit.xml"
COVERAGE_JSON = ROOT / "coverage.json"
METRICS_PATH = DOCS / "metrics.json"


def checked_documents() -> list[str]:
    """Every prose document in the repository, found rather than listed.

    An earlier version carried a hardcoded list, and deleting a document from
    the repository then failed the build with "listed as a checked document and
    not present". Removing a document is an ordinary editorial act, and a
    receipts mechanism that treats it as a build failure is a mechanism people
    learn to edit around.

    Discovery makes the set self-maintaining in both directions: a document
    added to docs/ is checked from the moment it exists, and a document deleted
    stops being checked without anyone having to remember a second place.

    Sorted so the output is stable, and relative so the committed metrics file
    does not record the path this happened to be built at.
    """
    found = [ROOT / "README.md"] if (ROOT / "README.md").exists() else []
    found += sorted(p for p in (ROOT / "docs").rglob("*.md") if p.is_file())
    return [str(p.relative_to(ROOT)) for p in found]


ANCHORS: dict[str, list[str]] = {
    "tests_total": ["{} tests,"],
    "coverage_line_pct": ["{}% line"],
    "coverage_branch_pct": ["{}% branch"],
    "records": ["log holds {} records"],
    "original_rows": ["table holds {} rows", "{} rows the original"],
    "replay_rows": ["produces {} rows"],
    "stable_rows": ["{} of them are identical"],
    "replay_stable_pct": ["{}% of the rows"],
    "diverged_rows": ["{} rows differ"],
    "rows_changed_by_drops": ["{} of them because"],
    "rows_changed_by_ties": ["{} differ only in"],
    "rows_only_in_replay": ["invents {} rows"],
    "rows_only_in_original": ["loses {} rows"],
    "keys_that_differ": ["all {} devices"],
    "original_sessions": ["emitted {} sessions"],
    "replay_sessions": ["become {} sessions"],
    "session_loss_pct": ["{}% of the session"],
    "replay_dropped": ["discards {} records"],
    "contingent_records": ["{} records are contingent"],
    "contingent_pct": ["{}% of the log"],
    "contingent_keys": ["names {} device"],
    "reachable_rows": ["{} rows can differ"],
    "reachable_pct": ["{}% of the table"],
    "reachable_span_minutes": ["{} minutes of event"],
    "replay_pairs": ["{} pairs of replays"],
    "replay_rows_outside_the_bound": ["{} rows fall outside"],
    "original_rows_outside_the_bound": ["{} of the rows that differ"],
    "bound_understates_by": ["understates it by {}"],
    "partition_deficit_s": ["{} seconds behind"],
    "allowed_lateness_s": ["allowed lateness of {} seconds"],
    "drops_the_boolean_causes": ["costs {} records"],
    "stable_pct_under_four_lines": ["to {}% and no further"],
    "settings_tried": ["{} combinations"],
    "most_sessions_recovered": ["best of them recovers {}"],
    "slowdown_needed": ["{} times slower"],
    "best_lateness_pct": ["reaches {}%"],
    "ties_floor": ["floor of {} rows"],
    "cliff_jitter_s": ["at {} seconds of spread"],
    "worst_lateness_pct": ["falls to {}%"],
}


def run_tests() -> bool:
    """Run pytest with machine readable reports. Returns whether it was green.

    Deliberately does not raise on a red suite, and that is a fix rather than a
    looseness. The suite contains a test that runs the number checker, the
    number checker compares the documents against the figures this script
    writes, and this script is how a stale figure gets corrected. Refusing to
    measure until the suite is green therefore made a stale figure unfixable:
    the only tool that could repair it declined to run until it had already been
    repaired.

    pytest writes its junit and coverage reports whether it passes or fails, so
    the measurement is available either way. A red suite is a fact to print
    loudly, not a reason to refuse to measure.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--cov=srd", "--cov-branch", f"--cov-report=json:{COVERAGE_JSON}", f"--junitxml={JUNIT}",
        ],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        print(
            "  the suite is red; measuring anyway so a stale figure can be "
            "corrected, then run it again"
        )
    return result.returncode == 0


def require_report(path: Path, produced_by: str) -> Path:
    """Fail with the reason rather than a traceback when a report is absent.

    --skip-tests reads the reports a previous pytest run left behind, and those
    reports are not tracked. A fresh checkout that has not run the suite does
    not have them, which is exactly what a continuous integration job that
    builds the artefacts on a second runner is. The FileNotFoundError this used
    to raise named a path and nothing else, and a path looks like a bug in the
    tool rather than a missing step in the caller.
    """
    if not path.exists():
        raise SystemExit(
            f"{path.name} is not here, and --skip-tests reads it instead of "
            f"running the suite. Run `{produced_by}` first, or drop --skip-tests."
        )
    return path


def read_test_count() -> int:
    """Read the test total from the junit report rather than from stdout."""
    root = ET.parse(require_report(JUNIT, "make cover")).getroot()
    suite = root if root.tag == "testsuite" else root[0]
    return int(suite.get("tests", 0))


def read_coverage() -> tuple[float, float]:
    """Read line and branch coverage from the JSON report.

    A report written without --cov-branch carries no branch data, and the
    fallback here used to be 100.0. That is the worst available default: the
    tool published a perfect branch figure that nothing had measured, in a
    repository whose whole argument is that every published figure was. An
    absent measurement is not a passing one, so it refuses instead.
    """
    totals = json.loads(require_report(COVERAGE_JSON, "make cover").read_text())["totals"]
    line = round(float(totals["percent_covered"]), 1)
    branches = totals.get("num_branches", 0)
    if not branches:
        raise SystemExit(
            "coverage.json carries no branch data, so no branch percentage can "
            "be read from it. It was written without --cov-branch."
        )
    return line, round(100.0 * totals.get("covered_branches", 0) / branches, 1)


def run_experiments() -> dict[str, Any]:
    """Run every experiment from scratch and load the JSON each one writes."""
    results: dict[str, Any] = {}
    for script in sorted(EXPERIMENTS.glob("exp*.py")):
        subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        stem = script.stem.split("_")[0]
        results[stem] = json.loads((EXPERIMENT_OUTPUT / f"{stem}.json").read_text())
    return results


def build_metrics(skip_tests: bool) -> dict[str, Any]:
    green = True
    if not skip_tests:
        green = run_tests()
    line_pct, branch_pct = read_coverage()
    experiments = run_experiments()
    headline = experiments["exp01"]
    bound = experiments["exp02"]
    cliff = experiments["exp03"]
    lines = experiments["exp04"]
    sessions = experiments["exp05"]

    metrics = {
        "tests_total": read_test_count(),
        "coverage_line_pct": line_pct,
        "coverage_branch_pct": branch_pct,
        "records": headline["records"],
        "original_rows": headline["original_rows"],
        "replay_rows": headline["replay_rows"],
        "stable_rows": headline["stable_rows"],
        "replay_stable_pct": round(100 * headline["replay_stable_share"], 1),
        "diverged_rows": headline["diverged_rows"],
        "rows_changed_by_drops": headline["rows_changed_by_drops"],
        "rows_changed_by_ties": headline["rows_changed_by_ties"],
        "rows_only_in_replay": headline["rows_only_in_replay"],
        "rows_only_in_original": headline["rows_only_in_original"],
        "keys_that_differ": headline["keys_with_a_changed_row"],
        "original_sessions": headline["original_sessions"],
        "replay_sessions": headline["replay_sessions"],
        "session_loss_pct": round(100 * headline["session_loss_share"], 1),
        "replay_dropped": headline["replay_dropped"],
        "contingent_records": bound["contingent_records"],
        "contingent_pct": round(100 * bound["contingent_share"], 1),
        "contingent_keys": bound["contingent_keys"],
        "reachable_rows": bound["reachable_rows"],
        "reachable_pct": round(100 * bound["reachable_share"], 1),
        "reachable_span_minutes": bound["reachable_span_minutes"],
        "replay_pairs": bound["replay_pairs_compared"],
        "replay_rows_outside_the_bound": bound["replay_rows_outside_the_bound"],
        "original_rows_outside_the_bound": bound["original_rows_outside_the_bound"],
        "bound_understates_by": round(bound["bound_understates_by"], 1),
        "partition_deficit_s": lines["partition_deficit_ms"] // 1_000,
        "allowed_lateness_s": lines["allowed_lateness_ms"] // 1_000,
        "drops_the_boolean_causes": lines["drops_the_boolean_causes"],
        "stable_pct_under_four_lines": round(100 * lines["stable_share_under_four_lines"], 1),
        "settings_tried": sessions["settings_tried"],
        "most_sessions_recovered": sessions["most_sessions_any_setting_produced"],
        "slowdown_needed": round(sessions["slowdown_needed_to_match_the_original"], 1),
        "best_lateness_pct": round(100 * cliff["best_stable_share"], 1),
        "ties_floor": cliff["ties_the_most_generous_lateness_cannot_fix"],
        "cliff_jitter_s": cliff["cliff_jitter_ms"] // 1_000,
        "worst_lateness_pct": round(100 * cliff["worst_stable_share"], 1),
    }
    return {
        "metrics": metrics,
        "table_values": table_values(),
        "anchors": ANCHORS,
        "checked_documents": checked_documents(),
        "measured_despite_a_red_suite": not green,
        "note": (
            "every value here is produced by running the suite and the experiments; "
            "tools/check_numbers.py fails the build when a document quotes a figure the code "
            "no longer produces"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="re-measure every published figure")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    payload = build_metrics(skip_tests=args.skip_tests)
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {METRICS_PATH.relative_to(ROOT)} with {len(payload['metrics'])} metrics")
    for name, value in sorted(payload["metrics"].items()):
        print(f"  {name:<34} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
