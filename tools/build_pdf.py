"""Lay out a repository document as a PDF: cmark-gfm for the markdown, Chromium for the page.

Rendered with the library GitHub uses, so a document that looks right in the repository looks
right in the PDF. Print rules are deliberately plain: black text, a 10pt body, no decorative
colour, and table borders that survive a monochrome printer. A cover page is added only when
asked for, because the copy committed in the repository should not open with one.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10pt; line-height: 1.45;
       color: #000000; }
h1 { font-size: 17pt; margin: 0 0 10pt; }
h2 { font-size: 13pt; margin: 18pt 0 6pt; border-bottom: 0.6pt solid #000000;
     padding-bottom: 3pt; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 14pt 0 4pt; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code, pre { font-family: 'DejaVu Sans Mono', Consolas, monospace; font-size: 8.6pt; }
pre { background: #f4f4f4; border: 0.5pt solid #999999; padding: 6pt 8pt;
      white-space: pre-wrap; page-break-inside: avoid; }
blockquote { margin: 8pt 0 8pt 12pt; padding-left: 10pt; border-left: 1.5pt solid #000000;
             font-style: italic; }
table { border-collapse: collapse; width: 100%; font-size: 8.8pt; margin: 8pt 0;
        page-break-inside: avoid; }
th, td { border: 0.5pt solid #000000; padding: 3.5pt 5pt; text-align: left;
         vertical-align: top; }
th { font-weight: 700; }
a { color: #000000; text-decoration: none; }
.cover { page-break-after: always; padding-top: 55mm; }
.cover h1 { font-size: 24pt; border: 0; margin-bottom: 6pt; }
.cover .sub { font-size: 11pt; margin-bottom: 28pt; }
.cover table { width: 78%; font-size: 9.5pt; }
.cover .foot { margin-top: 30pt; font-size: 9pt; }
"""


def cover_html(title: str, subtitle: str, rows: list[tuple[str, str]], footer: str) -> str:
    body = "".join(f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in rows)
    return (
        f'<div class="cover"><h1>{title}</h1><div class="sub">{subtitle}</div>'
        f"<table><tbody>{body}</tbody></table>"
        f'<div class="foot">{footer}</div></div>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="render a markdown document as a PDF")
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default=None, help="add a cover page with this title")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--metrics", type=Path, default=ROOT / "docs" / "metrics.json")
    args = parser.parse_args()

    import cmarkgfm
    from cmarkgfm.cmark import Options
    from playwright.sync_api import sync_playwright

    html_body = cmarkgfm.markdown_to_html_with_extensions(
        args.source.read_text(encoding="utf-8"),
        options=Options.CMARK_OPT_UNSAFE,
        extensions=["table", "autolink", "strikethrough", "tagfilter"],
    )

    cover = ""
    if args.title:
        import json

        metrics = json.loads(args.metrics.read_text())["metrics"] if args.metrics.exists() else {}
        rows = [
            ("records in the shipped log", f"{metrics.get('records'):,}"),
            (
                "output rows that survive a replay",
                f"{metrics.get('replay_stable_pct')} percent",
            ),
            (
                "sessions before and after",
                f"{metrics.get('original_sessions')} becomes {metrics.get('replay_sessions')}",
            ),
            (
                "rows the computable bound misses",
                str(metrics.get("original_rows_outside_the_bound")),
            ),
            ("suite", f"{metrics.get('tests_total')} tests, "
                      f"{metrics.get('coverage_line_pct')} percent line and branch"),
        ]
        stamp = dt.date.today().isoformat()
        cover = cover_html(
            args.title,
            args.subtitle,
            rows,
            f"Laxmi Sruthi Susarla. Figures re-measured {stamp} by tools/collect_metrics.py "
            f"and checked against the prose by tools/check_numbers.py.",
        )

    page_html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<style>{PRINT_CSS}</style></head><body>{cover}{html_body}</body></html>"
    )
    scratch = args.out.with_suffix(".scratch.html")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(page_html, encoding="utf-8")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(scratch.as_uri())
            page.wait_for_timeout(300)
            page.pdf(
                path=str(args.out),
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"},
            )
            browser.close()
    finally:
        scratch.unlink(missing_ok=True)

    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {args.out} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
