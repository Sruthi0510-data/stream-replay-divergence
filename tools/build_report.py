"""Build the standalone HTML report from the experiment JSON.

One file, no assets, no network. It is what somebody reads when they have been
handed a link and have four minutes, so it leads with the verdict and puts the
method underneath.

Every number comes from docs/experiments/*.json. Nothing here is typed by hand,
which means the report cannot disagree with the README even when only one of
them was regenerated.

The verdict badge carries an explicit colour rather than inheriting one from a
table cell. That is not fussiness: a badge class and a cell class colliding at
equal specificity once rendered a headline in a colour that matched its own
background, and the only thing in the pipeline that can see pixels is the
screenshot tool, so it checks the computed style.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "docs" / "experiments"
OUT = ROOT / "docs" / "report.html"

STYLE = """
:root {
  --surface: #fcfcfb;
  --ink: #1d1d1b;
  --muted: #6b6b66;
  --rule: #d9d9d4;
  --blue: #2a78d6;
  --orange: #eb6834;
  --green: #1baf7a;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 48px 24px 96px; max-width: 900px; background: var(--surface);
  color: var(--ink); line-height: 1.55;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
}
h1 { font-size: 30px; margin: 0 0 8px; letter-spacing: -0.01em; }
h2 { font-size: 20px; margin: 48px 0 12px; padding-top: 18px; border-top: 1px solid var(--rule); }
p { margin: 0 0 14px; }
.lede { color: var(--muted); font-size: 15px; margin-bottom: 28px; }
.verdict {
  display: inline-block; padding: 6px 14px; border-radius: 999px; font-weight: 600;
  font-size: 13px; color: #ffffff; background: var(--orange);
}
.figures { display: flex; flex-wrap: wrap; gap: 16px; margin: 24px 0 8px; }
.figure {
  flex: 1 1 200px; border: 1px solid var(--rule); border-radius: 10px; padding: 16px 18px;
  background: #ffffff;
}
.figure .value { font-size: 28px; font-weight: 600; letter-spacing: -0.02em; }
.figure .label { font-size: 12px; color: var(--muted); margin-top: 4px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em;
     color: var(--muted); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { font-size: 13px; color: var(--muted); }
.bar { height: 10px; border-radius: 5px; background: var(--blue); display: inline-block; }
"""


def _figure(value: str, label: str) -> str:
    return f'<div class="figure"><div class="value">{value}</div><div class="label">{label}</div></div>'


def build() -> str:
    headline = json.loads((EXPERIMENTS / "exp01.json").read_text())
    bound = json.loads((EXPERIMENTS / "exp02.json").read_text())
    cliff = json.loads((EXPERIMENTS / "exp03.json").read_text())
    lines = json.loads((EXPERIMENTS / "exp04.json").read_text())
    sessions = json.loads((EXPERIMENTS / "exp05.json").read_text())

    arms = [
        ("identical in both", headline["stable_rows"], "nothing to do"),
        ("changed by dropped records", headline["rows_changed_by_drops"], "allowed lateness"),
        ("changed by tie ordering", headline["rows_changed_by_ties"], "the query"),
        ("present only in the replay", headline["rows_only_in_replay"], "allowed lateness"),
        ("present only in the original", headline["rows_only_in_original"], "nothing to do"),
    ]
    widest = max(count for _, count, _ in arms)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Replay divergence report</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>Can this table be rebuilt from the topic?</h1>",
        '<p class="lede">One seeded log, one stateful job, and the table the original run '
        "wrote. Every figure below is produced by running the experiments in this "
        "repository against the committed fixture.</p>",
        '<p><span class="verdict">no: the replay does not reproduce the table</span></p>',
        "<h2 id=\"summary\">Summary</h2>",
        '<div class="figures">',
        _figure(f"{100 * headline['replay_stable_share']:.1f}%", "of output rows survive a replay"),
        _figure(str(headline["diverged_rows"]), "rows differ"),
        _figure(
            f"{headline['original_sessions']} to {headline['replay_sessions']}",
            "sessions before and after",
        ),
        _figure(str(headline["replay_dropped"]), "records the replay itself discarded"),
        "</div>",
        '<p class="note">The replay loses no row it had. It produces '
        f"{headline['replay_rows']} rows against the original's {headline['original_rows']}, "
        f"invents {headline['rows_only_in_replay']}, and rewrites rows belonging to "
        f"{headline['keys_with_a_changed_row']} of the topic's devices while discarding "
        f"{headline['replay_dropped']} records of its own.</p>",
        "<h2 id=\"arms\">Where the rows went</h2>",
        "<table><thead><tr><th>rows</th><th>count</th><th></th><th>owner</th></tr></thead><tbody>",
    ]
    for label, count, owner in arms:
        # A zero gets no bar at all. Giving it the minimum width drew a mark next to
        # "present only in the original: 0", which is the one row on the page whose
        # whole point is that it is empty.
        width = round(240 * count / widest) if count else 0
        bar = f'<span class="bar" style="width:{width}px"></span>' if width else ""
        parts.append(
            f"<tr><td>{html.escape(label)}</td><td class=\"num\">{count}</td>"
            f"<td>{bar}</td>"
            f"<td>{html.escape(owner)}</td></tr>"
        )
    parts += [
        "</tbody></table>",
        '<p class="note">A row whose record sets differ is charged to the drop even when its '
        "last-value column also disagrees, so the tie-ordering count is a floor on what would "
        "still be wrong after the lateness was fixed.</p>",
        "<h2 id=\"bound\">The bound, and what it is about</h2>",
        f"<p>{bound['contingent_records']:,} records are contingent on the batching, which is "
        f"{100 * bound['contingent_share']:.1f}% of the log, and they reach "
        f"{bound['reachable_rows']} output rows.</p>",
        "<table><thead><tr><th>comparison</th><th>rows that differ</th>"
        "<th>rows outside the bound</th></tr></thead><tbody>"
        f"<tr><td>replay against replay, {bound['replay_pairs_compared']} pairs</td>"
        f"<td class=\"num\">{bound['replay_rows_that_differ']}</td>"
        f"<td class=\"num\">{bound['replay_rows_outside_the_bound']}</td></tr>"
        f"<tr><td>replay against the original run</td>"
        f"<td class=\"num\">{bound['original_rows_that_differ']}</td>"
        f"<td class=\"num\">{bound['original_rows_outside_the_bound']}</td></tr>"
        "</tbody></table>",
        '<p class="note">The bound is correct and tight between replays, which all read the '
        "log in offset order. The original run processed in arrival order, and that ordering "
        "is not in the log.</p>",
        "<h2 id=\"sessions\">The sessions were never replayable</h2>",
        f"<p>{sessions['settings_tried']} combinations of batch size and time per batch, and "
        f"{sessions['settings_that_reproduce_the_table']} of them reproduce the original's "
        f"{sessions['target_sessions']} sessions. The best recovers "
        f"{sessions['most_sessions_any_setting_produced']}, and matching the original's "
        f"wall-clock span would need a further "
        f"{sessions['slowdown_needed_to_match_the_original']:.1f}x slowdown.</p>",
        '<p class="note">Meanwhile the drop counter read '
        f"{sessions['dropped_at_the_shipped_setting']} records at the shipped setting. "
        "Nothing would have alerted.</p>",
        "<h2 id=\"settings\">What the settings buy</h2>",
        "<table><thead><tr><th>allowed lateness</th><th>records the original dropped</th>"
        "<th>replay-stable rows</th><th>of the residue, tie ordering</th></tr></thead><tbody>",
    ]
    for record in cliff["by_lateness"]:
        parts.append(
            f"<tr><td>{record['allowed_lateness_ms'] // 1000} s</td>"
            f"<td class=\"num\">{100 * record['original_drop_rate']:.1f}%</td>"
            f"<td class=\"num\">{100 * record['replay_stable_share']:.1f}%</td>"
            f"<td class=\"num\">{record['rows_changed_by_ties']}</td></tr>"
        )
    parts += [
        "</tbody></table>",
        f"<p class=\"note\">Switching the watermark from one global line to one per partition "
        f"costs {lines['drops_the_boolean_causes']} records of the original run's drops and "
        f"lifts the stable share to {100 * lines['stable_share_under_four_lines']:.1f}%. The "
        f"slowest partition runs {lines['partition_deficit_ms'] // 1000} seconds behind the "
        f"one driving the watermark, against an allowed lateness of "
        f"{lines['allowed_lateness_ms'] // 1000} seconds.</p>",
        "</body></html>",
    ]
    return "\n".join(parts)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build() + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
