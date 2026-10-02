"""Render the README the way GitHub does and assert every image actually loads.

Two failures motivate this file. The first is that a committed SVG can be valid XML, open
correctly when navigated to directly, and still fail as an <img src> with naturalWidth 0 and
nothing in any console to say why. Only a browser can see that, so only a browser can check
it. The second is that python-markdown disagrees with GitHub about markdown inside HTML
blocks, so a README verified with it can still render wrongly; cmarkgfm is the library GitHub
itself uses.

The check that caught a real mistake during the build is the harness note below: an <img>
whose src is a file:// URL does not load when the page was created with set_content, because
that page has an about:blank origin with no access to local files. The image was fine and the
test was wrong. Writing a real HTML file next to the images and navigating to it fixes the
test rather than the diagram, which is why the scratch page is written to disk here.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"



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

def render_readme(source: str) -> str:
    """Render markdown with the library GitHub uses, or fall back with a clear warning."""
    try:
        import cmarkgfm
        from cmarkgfm.cmark import Options
    except ModuleNotFoundError:
        print("cmarkgfm is not installed; falling back to a raw HTML wrapper")
        return source
    return cmarkgfm.markdown_to_html_with_extensions(
        source,
        options=Options.CMARK_OPT_UNSAFE,
        extensions=["table", "autolink", "strikethrough", "tagfilter"],
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
        print("playwright is not installed; skipping the rendering check")
        return 0

    html = render_readme(README.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", dir=ROOT, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(f"<!doctype html><meta charset='utf-8'><body>{html}</body>")
        scratch = Path(handle.name)

    problems: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright)
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.goto(scratch.as_uri())
            page.wait_for_timeout(400)
            images = page.evaluate(
                """() => Array.from(document.images).map(img => ({
                    src: img.getAttribute('src'),
                    width: img.naturalWidth,
                    height: img.naturalHeight,
                }))"""
            )
            browser.close()
    finally:
        scratch.unlink(missing_ok=True)

    if not images:
        problems.append("the README contains no images, so the diagrams are not embedded")
    for image in images:
        source = image["src"] or ""
        if source.startswith("http"):
            continue
        if not image["width"] or not image["height"]:
            problems.append(f"{source} loaded with naturalWidth {image['width']}")

    for image in images:
        print(f"  {image['src']}  {image['width']}x{image['height']}")
    if problems:
        print("rendering check failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"rendering check passed: {len(images)} image(s) load with non-zero intrinsic size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
