"""Tests for the preflight checks.

The preflight is the thing that would otherwise be trusted without ever being
exercised. These tests run its pure parts and assert that each reports a problem
when there is one, using a temporary repository rather than this one, so a
passing preflight here is evidence rather than a tautology.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import preflight  # noqa: E402


@pytest.fixture
def repo(tmp_path: Path, monkeypatch):
    def _build(files: dict[str, str], *, authors=(("A Person", "a@example.invalid"),), message="first"):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        for name, text in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for index, (name, email) in enumerate(authors):
            subprocess.run(["git", "config", "user.name", name], cwd=tmp_path, check=True)
            subprocess.run(["git", "config", "user.email", email], cwd=tmp_path, check=True)
            subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-q", "--allow-empty", "-m", f"{message} {index}"],
                cwd=tmp_path,
                check=True,
            )
        monkeypatch.setattr(preflight, "ROOT", tmp_path)
        monkeypatch.setattr(preflight, "SRC", tmp_path / "src")
        return tmp_path

    return _build


def test_a_single_author_passes(repo, capsys):
    repo({"a.txt": "hello"})
    problems: list[str] = []
    preflight.check_authorship(problems)
    assert problems == []


def test_two_authors_fail(repo):
    repo({"a.txt": "hello"}, authors=(("A Person", "a@example.invalid"), ("B Person", "b@example.invalid")))
    problems: list[str] = []
    preflight.check_authorship(problems)
    assert problems and "expected one author" in problems[0]


def test_a_tool_credit_in_a_commit_message_fails(repo):
    repo({"a.txt": "hello"}, message="Co-Authored-By: something")
    problems: list[str] = []
    preflight.check_authorship(problems)
    assert any("mention a tool" in problem for problem in problems)


def test_a_tool_credit_in_a_file_fails(repo):
    repo({"a.txt": "Generated with a helper."})
    problems: list[str] = []
    preflight.check_no_attribution_in_files(problems)
    assert problems and "mention a tool" in problems[0]


def test_clean_files_pass_the_attribution_check(repo):
    repo({"a.txt": "a plain sentence."})
    problems: list[str] = []
    preflight.check_no_attribution_in_files(problems)
    assert problems == []


def test_an_em_dash_fails_the_ascii_check(repo):
    repo({"a.md": "a sentence — with punctuation"})
    problems: list[str] = []
    preflight.check_ascii(problems)
    assert problems and "non-ASCII" in problems[0]


def test_plain_ascii_passes(repo):
    repo({"a.md": "a sentence, with punctuation"})
    problems: list[str] = []
    preflight.check_ascii(problems)
    assert problems == []


def test_a_network_import_fails_the_offline_check(repo):
    repo({"src/pkg/mod.py": "import requests\n"})
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems and "reaches the network" in problems[0]


def test_a_from_import_of_a_network_module_fails(repo):
    repo({"src/pkg/mod.py": "from urllib.request import urlopen\n"})
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems


def test_a_submodule_of_a_blocked_package_fails(repo):
    repo({"src/pkg/mod.py": "import google.cloud.storage\n"})
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems


def test_a_name_that_merely_starts_with_a_blocked_one_passes(repo):
    """'socketserver' is not 'socket', and a prefix match would have said it was."""
    repo({"src/pkg/mod.py": "import socketserver\n"})
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems == []


def test_ordinary_imports_pass(repo):
    repo({"src/pkg/mod.py": "import json\nfrom pathlib import Path\n"})
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems == []


def test_this_repository_has_one_author():
    problems: list[str] = []
    preflight.check_authorship(problems)
    assert problems == []


def test_this_repository_imports_no_network_client():
    problems: list[str] = []
    preflight.check_offline(problems)
    assert problems == []
