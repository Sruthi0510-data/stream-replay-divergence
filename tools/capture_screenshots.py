"""Screenshot the real HTML report with Chromium, framed by headings rather than by pixels.

Three rules here, each from a failure worth not repeating.

Frame by heading. The tool scrolls to a named h2, reads its document offset, and clips to the
next named heading. A hard coded crop drifts silently the first time a paragraph gets longer
and then the README shows half a table. A missing heading fails the run, because it means the
report changed shape and the shot would be meaningless.

Pass full_page even when a clip is given. Without it Chromium clamps the clip to the viewport
and every capture comes out exactly one viewport tall with the bottom of the section missing.

Read back the computed style of the verdict badge and fail when it is invisible. A badge class
and a table cell class colliding at equal specificity renders a headline green on green: the
Python is correct, the HTML is correct, the tests pass, and the number the page exists for
cannot be read. A screenshot tool is the only thing in the pipeline that can see pixels, so it
is the right place for the check.

The report is built if missing rather than photographed from disk, because a screenshot of a
stale report is worse than no screenshot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "report.html"
OUT = ROOT / "docs" / "img"

SHOTS = [
    ("report-summary", "summary", "bound"),
    ("report-bound", "bound", "sessions"),
    ("report-settings", "settings", None),
]



REQUIRE_BROWSER = os.environ.get("REQUIRE_BROWSER") == "1"
"""Whether a missing browser is a failure or a skip.

Locally it is a skip: a contributor reading the repository should be able to run
everything else without installing a browser engine, and a stack trace from a
tool that only draws pictures teaches them the build is fragile.

In continuous integration it is a failure, because there the browser is
installed on purpose and a silent skip would mean the rendering checks quietly
stopped running. The workflow sets REQUIRE_BROWSER=1, so the same code is
forgiving on a laptop and strict where it matters.

An earlier version caught only ModuleNotFoundError, which is raised when the
playwright package is absent and not when the package is present and its browser
binaries are not. That is the common case on a fresh machine and in a CI job
whose browser install step failed, and it ended the run with a stack trace from
inside the library rather than a sentence anyone could act on.
"""


def launch_chromium(playwright):
    """Launch Chromium, or raise BrowserUnavailable with a message worth reading."""
    try:
        return playwright.chromium.launch()
    except Exception as exc:  # noqa: BLE001 - the library raises its own error type
        raise BrowserUnavailable(
            "chromium is not installed for playwright; run "
            "'python -m playwright install --with-deps chromium'"
        ) from exc


class BrowserUnavailable(RuntimeError):
    """Raised when playwright is importable and its browser binaries are not."""

def ensure_report() -> None:
    if REPORT.exists():
        return
    print("report missing, building it")
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "build_report.py")],
        cwd=ROOT,
        check=False,
    )


def main() -> int:
    try:
        return _main()
    except BrowserUnavailable as exc:
        if REQUIRE_BROWSER:
            print(f"{exc}")
            return 1
        print(f"{exc}; skipping")
        return 0


def _main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        if REQUIRE_BROWSER:
            print("playwright is required here and is not installed")
            return 1
        print("playwright is not installed; skipping screenshot capture")
        return 0

    ensure_report()
    if not REPORT.exists():
        print(f"no report at {REPORT}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    problems: list[str] = []

    with sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        page = browser.new_page(viewport={"width": 960, "height": 900}, device_scale_factor=2)
        page.goto(REPORT.as_uri())
        page.wait_for_timeout(250)

        badge = page.evaluate(
            """() => {
              const el = document.querySelector('.verdict');
              if (!el) return null;
              const s = getComputedStyle(el);
              return {colour: s.color, background: s.backgroundColor,
                      text: el.textContent.trim(),
                      visible: el.offsetWidth > 0 && el.offsetHeight > 0};
            }"""
        )
        if badge is None:
            problems.append("no verdict badge found in the report")
        elif not badge["visible"]:
            problems.append("the verdict badge has zero size")
        elif badge["colour"] == badge["background"]:
            problems.append(
                f"the verdict badge is illegible: colour {badge['colour']} on "
                f"{badge['background']}"
            )

        for name, start, end in SHOTS:
            box = page.evaluate(
                """([start, end]) => {
                  const a = document.getElementById(start);
                  if (!a) return null;
                  const top = a.getBoundingClientRect().top + window.scrollY - 24;
                  const b = end ? document.getElementById(end) : null;
                  const bottom = b
                    ? b.getBoundingClientRect().top + window.scrollY - 24
                    : document.body.scrollHeight;
                  return {top, height: bottom - top};
                }""",
                [start, end],
            )
            if box is None:
                problems.append(f"heading {start!r} is gone, so the {name} shot is meaningless")
                continue
            path = OUT / f"{name}.png"
            page.screenshot(
                path=str(path),
                full_page=True,
                clip={"x": 0, "y": box["top"], "width": 960, "height": box["height"]},
            )
            manifest.append(
                {
                    "image": f"docs/img/{name}.png",
                    "source": "docs/report.html",
                    "framed_from": start,
                    "framed_to": end,
                    "height_px": round(box["height"]),
                }
            )
        browser.close()

    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "shots": manifest,
                "badge": badge,
                "note": (
                    "captured from the report the tool generates, framed by heading id, with "
                    "the verdict badge contrast read back from the rendered page"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    for entry in manifest:
        print(f"  {entry['image']}  framed {entry['framed_from']} to {entry['framed_to']}")
    if badge:
        print(f"  badge: {badge['text']!r} {badge['colour']} on {badge['background']}")
    if problems:
        print("screenshot capture failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"captured {len(manifest)} screenshot(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
