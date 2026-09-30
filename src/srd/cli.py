"""The command line, whose exit codes carry the argument.

Four codes, and the distinction between two of them is the reason there are
four rather than two.

  0  the run agreed with the original, or the question asked had a clean answer
  2  the tool was handed something it cannot work with
  3  a divergence was found: the replay does not reproduce the original
  4  the question cannot be answered from what was supplied

Three is a finding. Four is a refusal. A pipeline that treats every non-zero
code as a failure will page somebody for both, which is exactly backwards: a
three means the output is not reproducible and somebody should know before they
reprocess, while a four means this tool has nothing to say and reprocessing is
no worse an idea than it was before.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from srd.batching import SingleBatch, ThroughputBatcher, WallClockBatcher, as_observed
from srd.bounds import Fate, classify, rewrite_window
from srd.compare import Arm, compare
from srd.events import EventLog, MalformedLogError, read_log, write_log
from srd.generator import GeneratorConfig, generate
from srd.job import RunResult, SessionRow, WindowRow, run
from srd.metrics import partition_deficit_ms, render_exposition
from srd.spec import PipelineSpec, SpecError

__all__ = ["main", "OK", "BAD_INPUT", "DIVERGED", "UNDETERMINED"]

OK = 0
BAD_INPUT = 2
DIVERGED = 3
UNDETERMINED = 4

DEFAULT_SPEC = Path("specs/pipeline.toml")
DEFAULT_LOG = Path("fixtures/log.json")
DEFAULT_OUTPUT = Path("fixtures/original-output.json")


def _write_output(path: Path, result: RunResult) -> None:
    """Persist a run's output tables, and nothing else.

    Deliberately not the watermark trace and not the dropped records. The file
    stands in for the table an operator finds in their warehouse the morning
    after, and a warehouse does not hold the job's internal state. Writing more
    would build a fixture that answers questions nobody can ask in the field.
    """
    payload = {
        "windows": [
            {
                "window_start_ms": row.window_start_ms,
                "key": row.key,
                "count": row.count,
                "total": row.total,
                "last_payload": row.last_payload,
            }
            for row in result.windows
        ],
        "sessions": [
            {
                "key": row.key,
                "session_index": row.session_index,
                "records": row.records,
                "first_event_time_ms": row.first_event_time_ms,
                "last_event_time_ms": row.last_event_time_ms,
            }
            for row in result.sessions
        ],
    }
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def _read_output(path: Path) -> RunResult:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MalformedLogError(f"no such output: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MalformedLogError(f"{path} is not valid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or "windows" not in raw or "sessions" not in raw:
        raise MalformedLogError(f"{path} is not an output table")
    return RunResult(
        windows=tuple(WindowRow(**row) for row in raw["windows"]),
        sessions=tuple(SessionRow(**row) for row in raw["sessions"]),
        dropped=(),
        watermark_steps=(),
        batch_count=0,
        processed=0,
    )


def _load(spec_path: Path, log_path: Path) -> tuple[PipelineSpec, EventLog]:
    return PipelineSpec.from_file(spec_path), read_log(log_path)


def _batcher(spec: PipelineSpec, name: str):
    """Build the batching a command was asked for, refusing the one that lies.

    "live" is accepted only by `generate`, which has the arrival times because
    it has just invented them. Every other command reads from the log, where
    every record carries the same arrival time, so a wall-clock batcher would
    quietly produce one enormous batch and label it a live run. An early version
    allowed it, printed "batches 1" for a six-hour topic, and looked like a bug
    in the batcher rather than the category error it was.
    """
    if name == "single":
        return SingleBatch()
    if name == "live":
        raise SpecError(
            "a live batching cannot be rebuilt from the log: every record in it "
            "carries the same arrival time, so the result would be one batch "
            "wearing a live run's name"
        )
    return ThroughputBatcher(max_records=spec.replay_batch_records)


def _cmd_generate(args: argparse.Namespace) -> int:
    spec = PipelineSpec.from_file(args.spec)
    events = generate(GeneratorConfig(records=args.records, seed=args.seed))
    args.log.parent.mkdir(parents=True, exist_ok=True)
    write_log(args.log, [event.logged for event in events])
    original = run(spec.job, WallClockBatcher(interval_ms=spec.trigger_interval_ms), events)
    _write_output(args.output, original)
    print(f"wrote {len(events)} records to {args.log}")
    print(
        f"wrote {len(original.windows)} window rows and {len(original.sessions)} "
        f"session rows to {args.output}"
    )
    print(f"the original run discarded {len(original.dropped)} records; the log does not say so")
    return OK


def _cmd_run(args: argparse.Namespace) -> int:
    spec, log = _load(args.spec, args.log)
    events = as_observed(tuple(log))
    result = run(spec.job, _batcher(spec, args.batching), events)
    print(f"batching       {args.batching}")
    print(f"batches        {result.batch_count}")
    print(f"processed      {result.processed}")
    print(f"dropped        {len(result.dropped)} ({result.drop_rate:.2%})")
    print(f"window rows    {len(result.windows)}")
    print(f"session rows   {len(result.sessions)}")
    return OK


def _cmd_replay(args: argparse.Namespace) -> int:
    spec, log = _load(args.spec, args.log)
    original = _read_output(args.output)
    replayed = run(spec.job, _batcher(spec, args.batching), as_observed(tuple(log)))
    diff = compare(original, replayed)
    arms = diff.by_arm()
    print(f"rows in the original table   {diff.original_rows}")
    print(f"rows the replay produced     {diff.replay_rows}")
    print(f"rows identical               {diff.stable_rows}")
    print(f"replay-stable share          {diff.replay_stable_share:.2%}")
    print(f"  changed by dropped records {arms[Arm.LATE_DROP]}")
    print(f"  changed by tie ordering    {arms[Arm.TIE_ORDER]}")
    print(f"  present only in the replay {arms[Arm.ONLY_REPLAY]}")
    print(f"  present only in the table  {arms[Arm.ONLY_ORIGINAL]}")
    print(f"sessions {diff.original_sessions} became {diff.replay_sessions}")
    if not diff.diverged:
        print("the replay reproduced the table")
        return OK
    print("the replay did not reproduce the table")
    return DIVERGED


def _cmd_bounds(args: argparse.Namespace) -> int:
    spec, log = _load(args.spec, args.log)
    report = classify(log, spec.job.watermark)
    original = _read_output(args.output)
    window = rewrite_window(
        report, window_ms=spec.window_ms, emitted_rows=len(original.windows)
    )
    print(f"records                      {len(report)}")
    print(f"contingent on batching       {len(report.contingent)} ({report.contingent_share:.2%})")
    print(f"keys affected                {len(report.contingent_keys())}")
    if not report.contingent:
        print("no record's fate depends on the batching, so two replays cannot disagree")
        print("this says nothing about how either compares with the original run")
        return UNDETERMINED
    print(f"rows two replays can differ  {len(window.identities)} ({window.reachable_share:.2%})")
    print(f"event-time span of those rows {window.span_ms // 60_000} minutes")
    print("this bounds replay against replay, not replay against the original run")
    return OK


def _cmd_witness(args: argparse.Namespace) -> int:
    spec, log = _load(args.spec, args.log)
    report = classify(log, spec.job.watermark)
    fates = report.by_offset()
    if args.offset not in fates:
        print(f"offset {args.offset} is not in {args.log}", file=sys.stderr)
        return BAD_INPUT
    fate = fates[args.offset]
    print(f"offset          {fate.offset}")
    print(f"key             {fate.key}")
    print(f"event time      {fate.event_time_ms} ms")
    print(f"worst watermark {fate.worst_line_ms} ms")
    print(f"fate            {fate.fate.value}")
    if fate.fate is Fate.STABLE_KEEP:
        print("no batching of this log can discard this record")
        return OK
    print("a batching that closes before this record discards it; one that does not keeps it")
    print("nothing in the log says which of the two ran")
    return DIVERGED


def _cmd_metrics(args: argparse.Namespace) -> int:
    spec, log = _load(args.spec, args.log)
    result = run(spec.job, _batcher(spec, args.batching), as_observed(tuple(log)))
    report = classify(log, spec.job.watermark)
    print(
        render_exposition(
            {
                "srd_records_processed_total": result.processed,
                "srd_records_dropped_total": len(result.dropped),
                "srd_partition_watermark_deficit_ms": partition_deficit_ms(log),
                "srd_batch_records": result.watermark_steps[-1].records if result.watermark_steps else 0,
                "srd_open_sessions": len(result.sessions),
                "srd_contingent_records_total": len(report.contingent),
            }
        ),
        end="",
    )
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srd",
        description="Measure what a replay of a stateful streaming job cannot reproduce.",
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="pipeline declaration")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="the commit log")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="the original run's table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="write the log and the original table")
    generate_parser.add_argument("--records", type=int, default=GeneratorConfig().records)
    generate_parser.add_argument("--seed", type=int, default=GeneratorConfig().seed)
    generate_parser.set_defaults(handler=_cmd_generate)

    for name, handler, help_text in (
        ("run", _cmd_run, "run the job over the log"),
        ("replay", _cmd_replay, "replay the log and diff against the original table"),
        ("metrics", _cmd_metrics, "print the scrape body a run would expose"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument(
            "--batching",
            choices=("replay", "single", "live"),
            default="replay",
            help="how to group records into triggers",
        )
        sub.set_defaults(handler=handler)

    bounds_parser = subparsers.add_parser("bounds", help="classify every record from the log alone")
    bounds_parser.set_defaults(handler=_cmd_bounds)

    witness_parser = subparsers.add_parser("witness", help="explain one record's fate")
    witness_parser.add_argument("offset", type=int)
    witness_parser.set_defaults(handler=_cmd_witness)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (MalformedLogError, SpecError) as exc:
        print(str(exc), file=sys.stderr)
        return BAD_INPUT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
