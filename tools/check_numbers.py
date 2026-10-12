"""Fail the build when a document quotes a figure the code no longer produces.

The checker matches anchor phrases rather than bare digits. Searching for digits passes
happily while the sentence around them has gone stale: "10 keys" is found in a document that
now says "10 events". An anchor pairs the value with two or three words of its context, and
any one of a metric's anchors matching is enough, because the same figure reads differently
in a table and in a paragraph.

Whitespace in every document is collapsed before matching. Without that, an anchor whose
words happen to straddle a line break in wrapped markdown matches nothing while the prose it
guards is correct, and the first thing anyone learns is to pass --no-verify.

Three passes, and the third exists because the first two were not enough. Forwards: every
metric must appear, with its current value, somewhere in the checked documents. Backwards:
every number in the README must correspond to a metric, so a figure nothing re-measures
cannot quietly appear. Code fences, inline code spans and HTML attributes are excluded from
the backward pass, because an example invocation is allowed to contain a made-up row count and
flagging it trains the reader to ignore the section.

The third pass is per document and looks for stale wording. Breaking a README figure on
purpose showed the hole: the forward pass searches the union of the documents, so a README
that has gone stale still passes when another document happens to quote the figure correctly,
and the backward pass only catches it when the wrong value is not itself some other metric.
Changing "finds 18 of" to "finds 32 of" defeated both. So every document is now searched
separately for each anchor's wording, and a match carrying any value other than the current
one fails the build and names the file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = ROOT / "docs" / "metrics.json"

FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]*`")
HTML_ATTR = re.compile(r'\w+="[^"]*"')
LINK_TARGET = re.compile(r"\]\([^)]*\)")
NUMBER_GROUP = r"(\d(?:[\d,]*\d)?(?:\.\d+)?)"
r"""The shape of a number, as a capturing group, shared by both passes.

The anchor pass used a looser group that ended in `[\d,.]*`, which swallowed the comma after a
figure at the end of a clause: "wrong by 0.923, and" captured "0.923," and reported a correct
sentence as stale. Both passes now agree on what a number looks like.
"""

NUMBER = re.compile(r"(?<![\w.-])" + NUMBER_GROUP + r"(?!\d)")
"""Numbers in prose, with a lookahead that only refuses more digits.

An earlier version refused any word character after the match, which quietly changed what it
matched rather than refusing to match: in "64236.36x" the lookahead failed after the decimal,
the engine backtracked, and it reported an unguarded "64236" that appears nowhere. A unit
suffix is normal in a table cell, so the rule is now the narrow one it should always have been.

The group also cannot end on a comma. Without that, "returns 3, because" produced a number
token of "3," which matched no metric and was reported as an unguarded figure in a sentence
containing no figures.
"""

ALLOWED_BARE_NUMBERS = {
    "0", "1", "2", "3", "4", "5", "7", "95", "100", "2026",
    "3.11", "3.12", "3.13", "20260930",
    "160", "640",
}
"""Numbers a document may contain without a metric behind them.

Python versions, exit codes, small structural counts, the generator seed, and 95 as the
confidence level every interval in these documents is stated at, and the two chunk sizes that
name a configuration rather than a measurement. Everything else must be a figure the collector
re-measures. The list is deliberately short: each entry is a hole in the
guarantee, so an addition should be argued for rather than made to get a build green.
"""


QUOTE_MARKER = re.compile(r"(?m)^[ \t]*>+[ \t]?")
"""The markdown quote marker, which carries no wording of its own.

Deleting a document that happened to quote a figure on one line left the same figure alive in
a blockquote in the README, wrapped so that "produces" ended one line and "820 rows" began the
next. Collapsing whitespace put the marker between the two words, the anchor missed, and the
build reported a correct sentence as a figure nothing quotes. Stripping the marker before the
collapse means a reflow of a quoted paragraph cannot change what an anchor matches.
"""


def collapse(text: str) -> str:
    """Collapse whitespace, quote markers and case, so formatting cannot fail a correct anchor.

    Lowercasing was added after an anchor failed on "The paired test returns 0.2181": the same
    phrase, at the start of a sentence. An anchor is a wording check, and neither a
    sentence-initial capital nor a line break inside a blockquote is a change of wording.
    """
    return re.sub(r"\s+", " ", QUOTE_MARKER.sub("", text)).lower()


def strip_code(text: str) -> str:
    """Remove fenced blocks, inline spans, HTML attributes and link targets."""
    for pattern in (FENCE, INLINE_CODE, HTML_ATTR, LINK_TARGET):
        text = pattern.sub(" ", text)
    return text


def format_value(value: Any) -> list[str]:
    """Render a metric the ways a document might legitimately write it.

    Floats need more forms than a percentage does. `:g` drops to six significant figures, so a
    design effect of 64236.36 rendered as 64236.4 and the checker reported a correct sentence as
    stale. `str` keeps the value the collector actually wrote, which is what a document quotes.
    """
    if isinstance(value, str):
        # Not every metric is a number. The name of the class that moved most is measured by an
        # experiment and quoted in prose exactly like a figure, so it is anchored the same way.
        rendered = [value]
    elif isinstance(value, float):
        rendered = [str(value), f"{value:g}", f"{value:.1f}", f"{value:,}"]
    else:
        rendered = [f"{value:,}", str(value)]
    seen: list[str] = []
    for candidate in rendered:
        if candidate not in seen:
            seen.append(candidate)
    return seen


def check_forward(payload: dict[str, Any], documents: dict[str, str]) -> list[str]:
    """Every metric must appear with its current value in at least one checked document."""
    corpus = collapse(" ".join(documents.values()))
    failures: list[str] = []
    for name, value in sorted(payload["metrics"].items()):
        anchors = payload["anchors"].get(name)
        if not anchors:
            failures.append(f"GAP     {name}: no anchor phrase, so nothing guards this figure")
            continue
        if any(
            collapse(anchor.format(rendered)) in corpus
            for anchor in anchors
            for rendered in format_value(value)
        ):
            continue
        stale = _stale_variant(anchors, corpus)
        detail = f"found a stale variant: {stale!r}" if stale else "not quoted anywhere"
        failures.append(f"FAIL    {name} = {value}: {detail}")
    return failures


def _stale_variant(anchors: list[str], corpus: str) -> str | None:
    """Find an anchor whose wording is present with some other number in it."""
    for anchor in anchors:
        pattern = re.escape(collapse(anchor)).replace(r"\{\}", NUMBER_GROUP)
        match = re.search(pattern, corpus)
        if match:
            return collapse(anchor).replace("{}", match.group(1))
    return None


def check_stale_per_document(payload: dict[str, Any], documents: dict[str, str]) -> list[str]:
    """Any document using a metric's wording must carry that metric's current value.

    Searched per document rather than over the union, because the union lets a stale sentence
    in one file hide behind a correct one in another.
    """
    failures: list[str] = []
    for name, value in sorted(payload["metrics"].items()):
        allowed = set(format_value(value))
        for anchor in payload["anchors"].get(name, []):
            if isinstance(value, str):
                continue
            pattern = re.escape(collapse(anchor)).replace(r"\{\}", NUMBER_GROUP)
            for document, text in documents.items():
                for match in re.finditer(pattern, collapse(text)):
                    found = match.group(1)
                    if found not in allowed and found.rstrip(".") not in allowed:
                        failures.append(
                            f"STALE   {document}: {name} is {value} but the document says "
                            f"{collapse(anchor).replace('{}', found)!r}"
                        )
    return failures


def check_backward(payload: dict[str, Any], readme: str) -> list[str]:
    """Every number in the README prose must be a figure something re-measures."""
    known = set()
    for value in payload["metrics"].values():
        known.update(format_value(value))
    # The README's data tables are generated by tools/render_tables.py from the experiment
    # JSON, and their cells are published here so the backward pass recognises them. They are
    # not anchored, because a table cell has no sentence around it to go stale: CI regenerates
    # the blocks and fails if the committed README differs, which is a stronger guarantee.
    for value in payload.get("table_values", ()):
        known.update(format_value(value))
    prose = strip_code(readme)
    unguarded: list[str] = []
    for match in NUMBER.finditer(prose):
        token = match.group(0)
        if token in known or token in ALLOWED_BARE_NUMBERS or token.replace(",", "") in known:
            continue
        context = collapse(prose[max(0, match.start() - 40) : match.end() + 40]).strip()
        unguarded.append(f"UNGUARDED {token}: nothing re-measures this. context: ...{context}...")
    return unguarded


def main() -> int:
    if not METRICS_PATH.exists():
        print("docs/metrics.json is missing; run tools/collect_metrics.py first")
        return 2
    payload = json.loads(METRICS_PATH.read_text())

    documents: dict[str, str] = {}
    missing: list[str] = []
    for relative in payload["checked_documents"]:
        path = ROOT / relative
        if not path.exists():
            missing.append(relative)
            continue
        documents[relative] = path.read_text(encoding="utf-8")

    # A document that the metrics file lists and the tree no longer has is an
    # editorial deletion that happened after the last measurement, not a fault.
    # Failing on it was the single most common way this build broke: somebody
    # removed a document, the receipts refused to run, and the only way back was
    # to edit a list in a tool nobody expected to have to edit.
    #
    # The README is the exception. It is the repository's front page and the
    # target of the backward pass, so its absence is a real problem.
    problems = [
        f"MISSING {name}: listed as a checked document and not present"
        for name in missing
        if name == "README.md"
    ]
    for name in missing:
        if name != "README.md":
            print(f"  note: {name} has been removed since the last measurement; run the collector")
    problems += check_forward(payload, documents)
    problems += check_stale_per_document(payload, documents)
    problems += check_backward(payload, documents.get("README.md", ""))

    if problems:
        print(f"number check failed with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(
        f"number check passed: {len(payload['metrics'])} metrics verified against "
        f"{len(documents)} documents, forwards, per document and backwards"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
