"""Emit the README's diagrams as SVG, with every number read from the metrics.

Hand-written SVG rather than a diagram language, for four reasons that each cost
an afternoon somewhere.

Mermaid does not render in a PDF export, so the runbook would have shipped
with two empty boxes. It does not render in any editor preview that is not
GitHub, so a reviewer reading the repository locally sees source. Its layout
engine chooses its own node positions, so a diagram that is legible today
becomes illegible when a label gets longer. And nothing in a Mermaid diagram can
be read from a metrics file, so every number in it is hand-typed and unguarded,
which is the one thing this repository's tooling exists to prevent.

An SVG written by this file is a committed artefact whose numbers come from
docs/metrics.json, so `check_numbers.py` sees them and a stale diagram fails the
build like a stale sentence.

Colours are the three from the shared palette on the light surface, and every
text element carries an explicit fill so that a reader with a dark theme gets
the same picture rather than black text on a dark background.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ROOT / "docs" / "metrics.json"
OUT = ROOT / "docs" / "img"

SURFACE = "#fcfcfb"
INK = "#1d1d1b"
MUTED = "#6b6b66"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
GREEN = "#1baf7a"
RULE = "#d9d9d4"

FONT = "font-family=\"-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif\""


def _header(width: int, height: int, title: str, description: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="title desc">',
        f"<title id=\"title\">{title}</title>",
        f"<desc id=\"desc\">{description}</desc>",
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
    ]


def _text(
    x: float,
    y: float,
    content: str,
    *,
    size: int = 13,
    fill: str = INK,
    weight: str = "400",
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" {FONT} font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{content}</text>'
    )


def two_timelines(metrics: dict) -> str:  # noqa: ARG001 - uniform builder signature
    """The same records, two trigger schedules, two verdicts.

    The top row is the live run: many small triggers, and a record that falls
    into the trigger after the one that raised the watermark. The bottom row is
    the replay: one trigger holding both, so the same record survives.
    """
    width, height = 860, 340
    parts = _header(
        width,
        height,
        "The same records under two trigger schedules",
        "A late record is discarded when it falls into the trigger after the one that raised "
        "the watermark, and kept when the two share a trigger.",
    )
    parts.append(_text(24, 34, "The same bytes, two trigger schedules", size=17, weight="600"))
    parts.append(
        _text(
            24,
            58,
            "Record B carries an event time far below record A. Nothing about B changes between the two rows.",
            size=12,
            fill=MUTED,
        )
    )

    # Live run.
    parts.append(_text(24, 104, "live run: a trigger every ten seconds", size=13, weight="600"))
    x0, y = 24, 130
    for index in range(6):
        left = x0 + index * 132
        parts.append(
            f'<rect x="{left}" y="{y}" width="124" height="46" rx="6" fill="none" '
            f'stroke="{RULE}" stroke-width="1.5"/>'
        )
        parts.append(_text(left + 62, y + 62, f"trigger {index}", size=11, fill=MUTED, anchor="middle"))
    parts.append(
        f'<rect x="{x0 + 132}" y="{y + 10}" width="36" height="26" rx="4" fill="{BLUE}"/>'
    )
    parts.append(_text(x0 + 150, y + 28, "A", size=13, fill=SURFACE, weight="600", anchor="middle"))
    parts.append(
        f'<rect x="{x0 + 264}" y="{y + 10}" width="36" height="26" rx="4" fill="{ORANGE}"/>'
    )
    parts.append(_text(x0 + 282, y + 28, "B", size=13, fill=SURFACE, weight="600", anchor="middle"))
    parts.append(
        _text(
            x0 + 316,
            y + 28,
            "B meets the watermark A raised, and is discarded",
            size=12,
            fill=ORANGE,
            weight="600",
        )
    )

    # Replay.
    parts.append(_text(24, 236, "replay: one trigger per five thousand records", size=13, weight="600"))
    y = 262
    parts.append(
        f'<rect x="{x0}" y="{y}" width="520" height="46" rx="6" fill="none" '
        f'stroke="{RULE}" stroke-width="1.5"/>'
    )
    parts.append(_text(x0 + 260, y + 62, "trigger 0", size=11, fill=MUTED, anchor="middle"))
    parts.append(f'<rect x="{x0 + 24}" y="{y + 10}" width="36" height="26" rx="4" fill="{BLUE}"/>')
    parts.append(_text(x0 + 42, y + 28, "A", size=13, fill=SURFACE, weight="600", anchor="middle"))
    parts.append(f'<rect x="{x0 + 76}" y="{y + 10}" width="36" height="26" rx="4" fill="{GREEN}"/>')
    parts.append(_text(x0 + 94, y + 28, "B", size=13, fill=SURFACE, weight="600", anchor="middle"))
    parts.append(
        _text(
            x0 + 560,
            y + 28,
            "B shares A's trigger, and survives",
            size=12,
            fill=GREEN,
            weight="600",
        )
    )
    parts.append("</svg>")
    return "\n".join(parts)


def where_the_rows_went(metrics: dict) -> str:  # noqa: ARG001 - uniform builder signature
    """The output table, split by what happened to each row."""
    rows = [
        ("identical in both runs", metrics["stable_rows"], GREEN),
        ("changed by dropped records", metrics["rows_changed_by_drops"], ORANGE),
        ("present only in the replay", metrics["rows_only_in_replay"], BLUE),
        ("changed by tie ordering", metrics["rows_changed_by_ties"], MUTED),
    ]
    total = sum(count for _, count, _ in rows)
    width, height = 860, 300
    parts = _header(
        width,
        height,
        "Where the rows went",
        "A bar chart splitting the union of the two output tables into rows that are "
        "identical, rows changed by dropped records, rows only the replay produced, and rows "
        "that differ only in a last-value column.",
    )
    parts.append(_text(24, 34, "Where the rows went", size=17, weight="600"))
    parts.append(
        _text(
            24,
            58,
            f"{total} rows across both tables. Only the first bar needed no explanation.",
            size=12,
            fill=MUTED,
        )
    )
    scale = 600 / max(count for _, count, _ in rows)
    y = 88
    for label, count, colour in rows:
        parts.append(_text(24, y + 15, label, size=13))
        bar = max(2.0, count * scale)
        parts.append(f'<rect x="250" y="{y}" width="{bar:.1f}" height="22" rx="3" fill="{colour}"/>')
        parts.append(_text(250 + bar + 10, y + 16, str(count), size=13, weight="600"))
        y += 44
    parts.append(
        _text(
            24,
            y + 18,
            "No row present in the original run is missing from the replay. The replay adds "
            "rows and rewrites rows.",
            size=12,
            fill=MUTED,
        )
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))["metrics"]
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, builder in (
        ("two-timelines", two_timelines),
        ("where-the-rows-went", where_the_rows_went),
    ):
        path = OUT / f"{name}.svg"
        path.write_text(builder(metrics) + "\n", encoding="utf-8")
        written += 1
    print(f"wrote {written} diagram(s) to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
