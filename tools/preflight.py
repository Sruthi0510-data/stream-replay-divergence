"""The checks that have nothing to do with correctness and everything to do with trust.

Five of them, and each exists because the absence of the check is invisible
until somebody else finds it.

Authorship: one author across the whole history, and no trailer, footer or file
header crediting a tool. A repository presented as a person's work should be
that person's work in its own history, and a stray co-author line months later
is worse than never having claimed it.

No non-ASCII in tracked text. An em dash or a smart quote that survives into a
README renders differently in every terminal, and a non-breaking space inside a
code block silently breaks a copied command.

Exit codes: the four documented codes are asserted against the real CLI, because
a documented exit code that has drifted is worse than an undocumented one.

Determinism: the whole suite under several hash seeds, because Python randomises
string hashing and this repository has already been bitten by it once.

Published URLs: no badge or clone URL still carries the placeholder handle. A
warning on a laptop, where the account may genuinely not be chosen yet, and a
failure in continuous integration, where it is knowable by definition.

Offline: no runtime module imports a network client. The tests can be run with
the network unplugged and this asserts that they will keep being able to be.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SELF = {"tools/preflight.py", "tests/test_preflight.py"}
"""The two files that necessarily contain the words the scan looks for.

Excluding them is not a loophole. One holds the pattern and the other holds the
fixtures that prove the pattern fires, so scanning them would make the check
impossible to test and impossible to write. Every other tracked file is scanned,
and the exclusion is two named paths rather than a glob so that nothing new can
slip under it.
"""

ATTRIBUTION = re.compile(
    r"co-authored-by|generated with|written by an ai|"
    r"chatgpt|copilot|assistant|gpt-\d|\bllm-generated\b",
    re.IGNORECASE,
)
NETWORK_MODULES = {
    "requests", "httpx", "aiohttp", "urllib3", "urllib.request", "socket",
    "http.client", "boto3", "google.cloud", "openai", "anthropic",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def check_authorship(problems: list[str]) -> None:
    authors = {line for line in _git("log", "--format=%an <%ae>").splitlines() if line}
    if len(authors) != 1:
        problems.append(f"expected one author, found {len(authors)}: {sorted(authors)}")
    else:
        print(f"  author: {authors.pop()}")
    messages = _git("log", "--format=%B")
    hits = sorted({match.group(0).lower() for match in ATTRIBUTION.finditer(messages)})
    if hits:
        problems.append(f"commit messages mention a tool: {hits}")
    else:
        print("  commit messages: no tool attribution")


def check_no_attribution_in_files(problems: list[str]) -> None:
    tracked = [line for line in _git("ls-files").splitlines() if line]
    hits: list[str] = []
    for name in tracked:
        path = ROOT / name
        if name in SELF or path.suffix in {".png", ".json"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no such file is tracked
            continue
        if ATTRIBUTION.search(text):
            hits.append(name)
    if hits:
        problems.append(f"files mention a tool: {hits}")
    else:
        print(f"  {len(tracked) - len(SELF)} tracked files: no tool attribution")


def check_ascii(problems: list[str]) -> None:
    offenders: list[str] = []
    for name in _git("ls-files").splitlines():
        path = ROOT / name
        if name in SELF or path.suffix in {".png"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no such file is tracked
            continue
        bad = {character for character in text if ord(character) > 127}
        if bad:
            offenders.append(f"{name}: {sorted(bad)[:5]}")
    if offenders:
        problems.append(f"non-ASCII in tracked text: {offenders}")
    else:
        print("  tracked text is ASCII throughout")


def check_exit_codes(problems: list[str]) -> None:
    cases = [
        (["bounds"], 0),
        (["--log", "does-not-exist.json", "run"], 2),
        (["--spec", "does-not-exist.toml", "run"], 2),
        (["run", "--batching", "live"], 2),
        (["replay"], 3),
        (["witness", "15179"], 3),
        (["witness", "14806"], 0),
        (["witness", "999999"], 2),
        (["metrics"], 0),
    ]
    for argv, expected in cases:
        result = subprocess.run(
            [sys.executable, "-m", "srd.cli", *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(SRC)},
        )
        if result.returncode != expected:
            problems.append(
                f"srd {' '.join(argv)} exited {result.returncode}, expected {expected}"
            )
        else:
            print(f"  srd {' '.join(argv)} -> {expected}")


def check_determinism(problems: list[str]) -> None:
    for seed in ("1", "7", "4096"):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONHASHSEED": seed},
        )
        if result.returncode != 0:
            problems.append(f"the suite fails under PYTHONHASHSEED={seed}")
        else:
            print(f"  suite passes under PYTHONHASHSEED={seed}")


def check_offline(problems: list[str]) -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == blocked or name.startswith(blocked + ".") for blocked in NETWORK_MODULES):
                    offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    if offenders:
        problems.append(f"runtime code reaches the network: {offenders}")
    else:
        print("  no runtime module imports a network client")



PLACEHOLDER = "LAXMI-GITHUB-USERNAME"
IN_CI = os.environ.get("CI") == "true"


def check_placeholder(problems: list[str]) -> None:
    """Catch a badge URL that still points at a placeholder handle.

    A badge whose URL does not match the repository's real path renders as a
    broken image, and a reader who sees one concludes the build is failing. The
    repository cannot know the handle before it is published, so this is a
    warning on a laptop and a failure in continuous integration, where the
    handle is knowable by definition because the code is already on GitHub.
    """
    tracked = [name for name in _git("ls-files").splitlines() if name]
    hits = []
    for name in tracked:
        path = ROOT / name
        # SELF holds the two files that necessarily contain the strings this
        # preflight searches for. The placeholder is defined here, so scanning
        # this file would report the checker to itself, which is the same trap
        # the attribution scan already documents.
        if name in SELF or path.suffix in {".png"} or not path.is_file():
            continue
        try:
            if PLACEHOLDER in path.read_text(encoding="utf-8"):
                hits.append(name)
        except UnicodeDecodeError:  # pragma: no cover - no such file is tracked
            continue
    if not hits:
        print("  badge and clone URLs name a real account")
        return
    message = (
        f"{len(hits)} file(s) still contain {PLACEHOLDER}: "
        f"{', '.join(hits)}. Replace it with the account the repository is "
        f"published under, or the CI badge renders as a broken image."
    )
    if IN_CI:
        problems.append(message)
    else:
        print(f"  WARNING  {message}")


def main() -> int:
    problems: list[str] = []
    for name, check in (
        ("authorship", check_authorship),
        ("published urls", check_placeholder),
        ("file attribution", check_no_attribution_in_files),
        ("ascii", check_ascii),
        ("exit codes", check_exit_codes),
        ("offline", check_offline),
        ("determinism", check_determinism),
    ):
        print(f"{name}:")
        check(problems)
    if problems:
        print("\npreflight failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("\npreflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
