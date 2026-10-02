"""Run the repository's own commands and commit what they actually printed.

A README can claim a suite passes. This runs it and keeps the transcript, so the
claim and the evidence are the same artefact. Every block below is produced by
executing the command in a subprocess from a clean environment and capturing
stdout, stderr and the exit code. Nothing is transcribed by hand and nothing is
edited afterwards except the two normalisations described next.

Two things are normalised, and both are disclosed in the rendered image itself
rather than only here, because a transcript that has been touched and does not
say so is worse than no transcript.

Elapsed times are replaced with a placeholder. Without that, regenerating the
transcript changes the committed SVG on every run and the drift check in CI
starts failing for reasons nobody can act on, which teaches people to ignore
drift checks. The durations are real; they are simply not stable.

Absolute paths are replaced with a repository-relative form, because the path a
build happened to run under is not evidence of anything and leaks the machine.

How long each command took is not recorded. It is the only genuinely unstable
thing about a run, it is evidence of nothing, and keeping it left the committed
sidecar dirty after every build.

Long output is truncated to a line budget per command, with the number of
omitted lines stated in place. The full output is written to a JSON sidecar
alongside the image, with a SHA-256 of the untruncated bytes, so the picture is
a summary and the sidecar is the record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ELAPSED = re.compile(r"\bin \d+(?:\.\d+)?s\b")
"""Only the shape a test runner prints its own duration in.

A first version normalised anything that looked like a duration, which turned an
event time of 10765000 ms in a CLI transcript into a placeholder and destroyed
the very number the command was being run to show. The rule is now narrow: the
word "in", a number, and a trailing s, which is what pytest emits and what no
payload in these repositories looks like.
"""
ABS_PATH = re.compile(re.escape(str(ROOT)))
PREFIX = re.compile(re.escape(sys.prefix))
"""The interpreter's own installation root, scrubbed out of captured output.

A traceback or a coverage path from inside a virtual environment carries the
path the environment happens to live at, which is a fact about one machine and
makes the committed transcript unreproducible anywhere else.
"""
INTERPRETER = re.compile(r"(?:\S*/)?python[0-9.]*(?=\s|$)")
"""Any interpreter, however it was invoked, rendered as `python3`.

The first version matched only a path ending in `python3`, which is what a
system interpreter looks like and not what a virtual environment's looks like:
inside a venv `sys.executable` ends in `/bin/python`, with no digit, so the
pattern missed and the full absolute path went into the committed transcript.

That was not a cosmetic leak. The transcript is under a drift check, so a
capture made in one environment could never match a capture made in another,
and continuous integration failed on every run with a diff nobody could act on.
The rule is now about the command name rather than about one spelling of it.
"""

BG = "#14161a"
FG = "#d8dee6"
DIM = "#7c8694"
PROMPT = "#5aa9f5"
OK = "#4cc38a"
BAD = "#f2734f"
LINE_HEIGHT = 18
CHAR_WIDTH = 7.75
PAD = 18
WRAP = 112
"""Characters per line before the renderer wraps.

Not a cosmetic limit. SVG text does not wrap, so a long line simply runs past
the viewBox and is cut off by the frame, and the first version of this tool lost
the tail of the one message it was capturing to prove a refusal. Wrapping with a
visible continuation marker keeps the evidence in the picture.
"""


@dataclass(frozen=True)
class Step:
    """One captured command.

    `label` is what the reader is being shown, and it is required, because a
    transcript of eight commands with no commentary is a wall that gets scrolled
    past. `expect` is the exit code the README claims; a mismatch fails the
    build rather than silently committing a red transcript as if it were green.
    """

    label: str
    argv: Sequence[str]
    expect: int = 0
    max_lines: int = 14
    tail_lines: int = 0
    """How many lines from the end to keep when the output is truncated.

    A coverage report's most interesting line is its last one, and a truncation
    that keeps only the head throws it away. Steps that end in a total set this.
    """


def _normalise(text: str) -> str:
    text = ABS_PATH.sub(".", text)
    text = ELAPSED.sub("in <elapsed>", text)
    text = PREFIX.sub("<env>", text)
    text = ELAPSED.sub("in <elapsed>", text)
    return text.replace("\r", "")


def _display(argv: Sequence[str]) -> str:
    """The command as a reader should retype it.

    The interpreter is shown as `python3` rather than by its absolute path. The
    path is a fact about the build machine and not about the repository, and a
    transcript that leaks it invites somebody to copy a command that will not
    run anywhere else.
    """
    return " ".join(INTERPRETER.sub("python3", part) for part in argv)


def run(step: Step, env: dict[str, str]) -> dict[str, object]:
    result = subprocess.run(
        list(step.argv),
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    raw = (result.stdout + result.stderr).rstrip("\n")
    return {
        "label": step.label,
        "command": _display(step.argv),
        "exit_code": result.returncode,
        "expected_exit_code": step.expect,
        "output_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "output_lines": len(raw.splitlines()),
        "output": _normalise(raw),
        "max_lines": step.max_lines,
        "tail_lines": step.tail_lines,
    }


def _wrap(text: str) -> list[str]:
    """Break a line at the width the frame can hold, marking each continuation."""
    if len(text) <= WRAP:
        return [text]
    rows = [text[:WRAP]]
    rest = text[WRAP:]
    while rest:
        rows.append("  " + rest[: WRAP - 2])
        rest = rest[WRAP - 2 :]
    return rows


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def render(captures: list[dict[str, object]], title: str) -> str:
    """Draw the transcript as an SVG terminal.

    SVG rather than a PNG so the text is selectable, searchable and diffable,
    and so a reader can copy a command out of the picture. Every colour is set
    explicitly on the element; nothing inherits, because an inherited colour
    renders as black on a dark background in some viewers.
    """
    lines: list[tuple[str, str]] = []
    for capture in captures:
        for index, row in enumerate(_wrap(f"$ {capture['command']}")):
            lines.append(("prompt" if index == 0 else "out", row))
        body = str(capture["output"]).splitlines()
        budget = int(capture["max_lines"])
        tail = int(capture.get("tail_lines", 0))
        if len(body) > budget:
            head = body[: budget - 1 - tail]
            kept_tail = body[len(body) - tail :] if tail else []
            omitted = len(body) - len(head) - len(kept_tail)
            body = (
                head
                + [f"... {omitted} more lines, full output in docs/proof-of-work.json"]
                + kept_tail
            )
        for row in body:
            lines.extend(("out", wrapped) for wrapped in _wrap(row))
        status = "ok" if capture["exit_code"] == capture["expected_exit_code"] else "bad"
        lines.append((status, f"[exit {capture['exit_code']}]"))
        lines.append(("gap", ""))

    widest = max((len(text) for _, text in lines), default=40)
    width = int(PAD * 2 + min(max(widest, 60), WRAP + 2) * CHAR_WIDTH)
    height = PAD * 2 + 34 + LINE_HEIGHT * len(lines)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{_escape(title)}</title>",
        "<desc id=\"d\">A captured terminal transcript. Every command was executed by "
        "tools/capture_terminal.py and its exit code recorded; elapsed times and absolute "
        "paths are normalised so the committed image does not change on every run.</desc>",
        f'<rect width="{width}" height="{height}" rx="8" fill="{BG}"/>',
        f'<text x="{PAD}" y="{PAD + 14}" font-family="monospace" font-size="12" fill="{DIM}">'
        f"{_escape(title)}</text>",
    ]
    y = PAD + 34
    for kind, text in lines:
        if kind == "gap":
            y += LINE_HEIGHT
            continue
        fill = {"prompt": PROMPT, "out": FG, "ok": OK, "bad": BAD}[kind]
        weight = "600" if kind in {"prompt", "ok", "bad"} else "400"
        parts.append(
            f'<text x="{PAD}" y="{y}" font-family="ui-monospace, SFMono-Regular, Menlo, '
            f'Consolas, monospace" font-size="12.5" font-weight="{weight}" fill="{fill}" '
            f'xml:space="preserve">{_escape(text)}</text>'
        )
        y += LINE_HEIGHT
    parts.append("</svg>")
    return "\n".join(parts)


def capture(steps: list[Step], *, title: str, out_svg: Path, out_json: Path) -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    env["PYTHONHASHSEED"] = "0"
    env["COLUMNS"] = "100"
    captures = [run(step, env) for step in steps]
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text(render(captures, title) + "\n", encoding="utf-8")
    out_json.write_text(
        json.dumps(
            {
                "note": (
                    "every command below was executed by tools/capture_terminal.py; "
                    "exit codes are as returned, and elapsed times and absolute paths are "
                    "normalised so the committed transcript does not change on every run"
                ),
                "captures": captures,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    wrong = [c for c in captures if c["exit_code"] != c["expected_exit_code"]]
    for c in captures:
        marker = "ok " if c["exit_code"] == c["expected_exit_code"] else "BAD"
        print(f"  {marker} exit {c['exit_code']:>2}  {c['command']}")
    if wrong:
        print("capture failed: a command did not exit as the README claims")
        for c in wrong:
            print(f"  {c['command']} exited {c['exit_code']}, expected {c['expected_exit_code']}")
        return 1
    print(f"captured {len(captures)} command(s) to {out_svg.name} and {out_json.name}")
    return 0
