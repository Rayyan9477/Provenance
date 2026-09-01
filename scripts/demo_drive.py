"""Drive a real browser through the product walkthrough, once, in one take.

Why this exists
---------------
The segment table in this file fixes eleven segments and the second at
which each one starts. The voice track is recorded separately against those
starts, so the screen has to arrive on time by itself -- a human driving tabs
lands two or three seconds late by segment four and the narration never
recovers. This script holds the schedule instead: it navigates, scrolls,
settles, and then sits still until the segment's deadline, so the frame is
motionless while the voice talks.

Three of the eleven segments have nothing on the web app to show (the write
path argument, the `ops/` evidence, and the raw version endpoint). They are
still shown *in the browser*, because the recording must be one continuous take
-- alt-tabbing to a terminal mid-take is a cut, and a cut is where a reviewer stops
believing the clock. Segments 7 and 8 are generated as terminal-styled HTML from
the real repository files at runtime, so nothing on screen is transcribed by
hand and nothing can drift away from what the repository actually says.

Usage
-----
    python scripts/demo_drive.py --dry-run      # print the plan, check the total
    python scripts/demo_drive.py                # warm up, then the full take
    python scripts/demo_drive.py --segment 5    # one segment, for a re-shoot
    python scripts/demo_drive.py --assets-only  # rebuild the two HTML pages

Progress goes to stderr so it never lands in the recording: the browser window
is what gets captured, this terminal is not.
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# The live targets. These are the deployed services, not a local stack: the
# whole point of the take is that a reviewer can visit the same URLs afterwards.
# ---------------------------------------------------------------------------

WEB = "https://provenance-web-vaq74wztva-uk.a.run.app"
API = "https://provenance-control-plane-vaq74wztva-uk.a.run.app"
CASE = "7c1e894f-57ff-533c-8f9f-543a395e6c46"

#: Generated at runtime, never committed. Two terminal-styled pages plus the
#: two second black hold that opens the video.
ASSETS = _REPO_ROOT / "tmp" / "demo_assets"

#: The transcript's own arithmetic: 3:39 of narration inside a 4:00 cap, with
#: the segment starts adding up to 220 seconds exactly. `--dry-run` asserts the
#: plan below still sums to this, because a segment edited by hand that pushes
#: the total by two seconds desynchronises the voice track from segment 6 on.
TRANSCRIPT_TOTAL_SECONDS = 220.0

#: How long a scroll is given to finish before the still hold starts counting.
#: Smooth scrolling in Chromium is animated, and an animation still running when
#: the narration starts is exactly the wobble this script exists to avoid.
_SCROLL_SETTLE_TIMEOUT = 2.5


# ---------------------------------------------------------------------------
# Reading the repository for the two generated pages
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    """Read a repository file, tolerating the odd non-UTF-8 byte in `ops/`.

    The live-run transcripts were captured from a terminal and carry a few
    box-drawing characters. Replacing an undecodable byte is better than
    aborting a take five seconds before it starts.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def _write_path_claims() -> list[str]:
    """Pull the four write-path claims out of `docs/diagrams/architecture.md`.

    The claims are the left column of the table under "What that diagram
    claims". Reading them rather than retyping them means the slide cannot
    quietly disagree with the document it is quoting. If the table is ever
    restructured we fall back to the argument as the transcript states it,
    because a demo that crashes on a heading rename is worse than one that
    shows four true sentences.
    """
    fallback = [
        "The Kernel is the only canonical writer",
        "The agent layer holds no write capability",
        "Agents cannot write, by grant rather than by prompt",
        "The commit is one transaction",
    ]
    doc = _REPO_ROOT / "docs" / "diagrams" / "architecture.md"
    if not doc.exists():
        return fallback

    claims: list[str] = []
    in_table = False
    for line in _read_text(doc).splitlines():
        if line.startswith("### What that diagram claims"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            if claims:
                break
            continue
        cell = line.split("|")[1].strip()
        if not cell or set(cell) <= {"-", ":"} or cell == "Claim":
            continue
        claims.append(cell.replace("`", "").replace("*", ""))
        if len(claims) == 4:
            break
    return claims or fallback


def _kernel_node_lines() -> list[str]:
    """The Kernel box from the mermaid diagram, one `<br/>` segment per line.

    This is the sentence "THE ONLY CANONICAL WRITER" in its original context --
    a deterministic box with no model call and no network call -- which is the
    half of the argument the lint output cannot show.
    """
    doc = _REPO_ROOT / "docs" / "diagrams" / "architecture.md"
    if not doc.exists():
        return []
    for line in _read_text(doc).splitlines():
        if "THE ONLY CANONICAL WRITER" not in line:
            continue
        body = line.split('["', 1)[-1].rsplit('"]', 1)[0]
        return [part.strip() for part in body.split("<br/>") if part.strip()]
    return []


def _write_path_lint_output() -> str:
    """Run the real lint and capture its stdout.

    Run at prep time, never mid-take: it walks 196 modules and takes a few
    seconds, and a few seconds is a quarter of segment 7. If it cannot run --
    no interpreter path, an import error, a timeout -- we show a short static
    summary instead and say so, rather than killing a take that is otherwise
    ready to record.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-m", "tools.write_path_lint"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(write_path_lint could not be run here: {exc})\n5 rules, 0 violations as of 2026-08-27."
    out = (done.stdout or "").strip()
    if not out:
        return (done.stderr or "").strip() or "(write_path_lint produced no output)"
    return out


def _tail(path: Path, lines: int) -> str:
    """The last `lines` lines of a file, or a visible note if it is missing."""
    if not path.exists():
        return f"({path.relative_to(_REPO_ROOT).as_posix()} is not in this checkout)"
    return "\n".join(_read_text(path).splitlines()[-lines:])


def _summary_block(path: Path, fallback_lines: int) -> str:
    """The SUMMARY block at the end of a live-run transcript.

    `ops/agent-graph-live-run.txt` ends with a banner-delimited SUMMARY that
    carries the numbers segment 8 narrates -- both Gemini tiers, the agent_runs
    count, the PASS/FAIL/CANNOT RUN tally. Slicing from the banner rather than
    a fixed `tail -n` keeps the block whole if the file grows.
    """
    if not path.exists():
        return f"({path.relative_to(_REPO_ROOT).as_posix()} is not in this checkout)"
    rows = _read_text(path).splitlines()
    for index in range(len(rows) - 1, -1, -1):
        if rows[index].strip() == "SUMMARY":
            start = index - 1 if index and set(rows[index - 1].strip()) == {"="} else index
            return "\n".join(rows[start:]).strip("\n")
    return "\n".join(rows[-fallback_lines:])


# ---------------------------------------------------------------------------
# The two generated pages
# ---------------------------------------------------------------------------

_TERMINAL_CSS = """
html, body {{ margin: 0; padding: 0; background: #0b0e12; }}
body {{
  color: #cfd8dc;
  font-family: "Cascadia Mono", "JetBrains Mono", Consolas, "DejaVu Sans Mono", monospace;
  font-size: {font}px;
  line-height: 1.4;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ padding: 34px 56px 26px; }}
h1 {{
  font-size: {title}px; font-weight: 600; color: #8fd6a8;
  margin: 0 0 6px; letter-spacing: 0.01em;
}}
.sub {{ color: #7c8b93; margin: 0 0 26px; font-size: {sub}px; }}
.cmd {{ color: #8fd6a8; margin: 22px 0 8px; }}
.cmd .p {{ color: #5b6b74; }}
.claim {{ color: #e8c46a; margin: 4px 0; }}
.claim .tick {{ color: #8fd6a8; }}
pre {{
  margin: 0; white-space: pre-wrap; word-break: break-word;
  color: #cfd8dc; font: inherit;
}}
.dim {{ color: #7c8b93; }}
"""


def _terminal_page(title: str, subtitle: str, body: str, font: int) -> str:
    """One self-contained dark terminal page, sized to be read at 1080p.

    No external anything: it is opened over `file://` and a missing font or a
    blocked request mid-take would show as a blank frame.
    """
    css = _TERMINAL_CSS.format(font=font, title=font + 6, sub=font - 3)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{css}</style></head>"
        f"<body><div class='wrap'><h1>{html.escape(title)}</h1>"
        f"<p class='sub'>{html.escape(subtitle)}</p>{body}</div></body></html>"
    )


def build_assets() -> dict[str, Path]:
    """Generate the three local pages the take opens over `file://`.

    Rebuilt on every run rather than cached, so the numbers on screen are the
    numbers in the working tree at the moment of recording.
    """
    ASSETS.mkdir(parents=True, exist_ok=True)

    # The two second black hold that opens segment 1. `about:blank` is white,
    # which reads as a broken page rather than as a deliberate beat.
    black = ASSETS / "black.html"
    black.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>.</title>"
        "<style>html,body{margin:0;height:100%;background:#000;}</style>"
        "</head><body></body></html>",
        encoding="utf-8",
    )

    # Segment 7 -- agents propose, the kernel writes.
    kernel = _kernel_node_lines()
    parts: list[str] = []
    if kernel:
        parts.append("<pre class='dim'>" + html.escape("\n".join(kernel)) + "</pre>")
    parts.append("<div style='height:14px'></div>")
    for claim in _write_path_claims():
        parts.append(
            f"<p class='claim'><span class='tick'>&#10003;</span> {html.escape(claim)}</p>"
        )
    parts.append(
        "<p class='cmd'><span class='p'>provenance $</span> python -m tools.write_path_lint</p>"
    )
    parts.append("<pre>" + html.escape(_write_path_lint_output()) + "</pre>")
    write_path = ASSETS / "segment_07_write_path.html"
    write_path.write_text(
        _terminal_page(
            "Agents propose. The Kernel writes.",
            "docs/diagrams/architecture.md -- enforced by grant, checked by lint",
            "".join(parts),
            # 20px is the floor for legibility at 1080p and this page carries
            # the most lines of the three, so it sits exactly on the floor.
            font=20,
        ),
        encoding="utf-8",
    )

    # Segment 8 -- the evidence. Two real transcripts, tailed, nothing retyped.
    evidence_parts = [
        "<p class='cmd'><span class='p'>provenance $</span> tail ops/agent-graph-live-run.txt</p>",
        "<pre>"
        + html.escape(_summary_block(_REPO_ROOT / "ops" / "agent-graph-live-run.txt", 16))
        + "</pre>",
        "<p class='cmd'><span class='p'>provenance $</span> tail -5 ops/ingestion-live-run.txt</p>",
        "<pre>" + html.escape(_tail(_REPO_ROOT / "ops" / "ingestion-live-run.txt", 5)) + "</pre>",
    ]
    evidence = ASSETS / "segment_08_evidence.html"
    evidence.write_text(
        _terminal_page(
            "Measured, not asserted.",
            "ops/ -- live runs against Gemini and against the real cluster",
            "".join(evidence_parts),
            font=20,
        ),
        encoding="utf-8",
    )

    return {"black": black, "write_path": write_path, "evidence": evidence}


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


class Driver:
    """Everything a segment is allowed to do to the browser.

    Deliberately small. A segment navigates, scrolls one thing into the middle
    of the frame, and stops. Anything more elaborate is motion the narration
    has not budgeted for.
    """

    def __init__(self, page, assets: dict[str, Path]) -> None:
        self.page = page
        self.assets = assets

    # -- navigation --------------------------------------------------------

    def goto(self, url: str) -> None:
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            # A short settle after DOM ready: the app hydrates, and hydration
            # shifting the layout under a narration line is the one visible
            # artefact this whole script is about.
            self.page.wait_for_timeout(700)
        except Exception as exc:  # a live take is never abandoned for one bad frame
            _warn(f"navigation to {url} did not complete cleanly: {exc}")

    def ensure(self, url: str) -> None:
        """Navigate only if we are not already there.

        Segments 2 and 4 continue on the page the previous segment opened. In a
        full take that must not reload -- a reload is a visible flash on a beat
        the transcript marks as still. Run in isolation with `--segment`, the
        same call does navigate, because there is nothing on screen yet.
        """
        if self.page.url.rstrip("/") != url.rstrip("/"):
            self.goto(url)

    def file_url(self, key: str) -> str:
        return self.assets[key].resolve().as_uri()

    # -- scrolling ---------------------------------------------------------

    def centre_on(self, text: str) -> None:
        """Smooth-scroll the first match for `text` to the middle of the frame.

        A miss is reported and then ignored: the segment still holds for its
        full duration on whatever is on screen, which is a slightly wrong frame
        rather than a dead take.
        """
        try:
            target = self.page.get_by_text(text, exact=False).first
            target.wait_for(state="attached", timeout=8_000)
            target.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
            self._await_still()
            self._point_at(target)
        except Exception as exc:
            _warn(f"could not centre on {text!r}: {exc}")

    def scroll_top(self) -> None:
        try:
            self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            self._await_still()
        except Exception as exc:
            _warn(f"could not scroll to top: {exc}")

    def _await_still(self) -> None:
        """Block until the scroll animation has stopped moving.

        Polling the offset is cruder than a scrollend listener and works on
        every page here, including the plain-text one Chromium builds for the
        JSON endpoint.
        """
        deadline = time.monotonic() + _SCROLL_SETTLE_TIMEOUT
        previous = None
        stable = 0
        while time.monotonic() < deadline:
            try:
                current = self.page.evaluate("window.scrollY")
            except Exception:
                return
            stable = stable + 1 if current == previous else 0
            if stable >= 2:
                return
            previous = current
            self.page.wait_for_timeout(100)

    def _point_at(self, target) -> None:
        """Rest the real pointer on the thing being talked about.

        The transcript says "cursor rests on" for three segments. A pointer
        sitting on the number is the cheapest way to tell a viewer which of the
        four figures on screen is the one being read aloud.
        """
        try:
            box = target.bounding_box()
            if box:
                self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            pass

    def enlarge_plaintext(self, px: int = 27) -> None:
        """Make Chromium's raw-text rendering readable at 1080p.

        `/v1/version` answers `application/json`, so Chromium renders it as one
        long unstyled line at about 13px, which is illegible on video. The bytes
        on screen are still exactly what the endpoint returned -- only the type
        size and the wrapping change.
        """
        try:
            self.page.add_style_tag(
                content=(
                    "body{background:#0b0e12;margin:0;padding:56px 64px;}"
                    "pre{display:block!important;color:#cfd8dc;"
                    "font-family:'Cascadia Mono',Consolas,monospace;"
                    f"font-size:{px}px;line-height:1.55;white-space:pre-wrap;word-break:break-word;}}"
                    # Chromium's own JSON viewer paints a pale "Pretty-print"
                    # strip above the response. It is browser furniture rather
                    # than anything the API said, and a white band across the
                    # top of an otherwise dark frame is the only thing a viewer
                    # would look at.
                    ".json-formatter-container{display:none!important;}"
                )
            )
        except Exception as exc:
            _warn(f"could not restyle the raw JSON frame: {exc}")


# ---------------------------------------------------------------------------
# The eleven segments, in the transcript's order and at its durations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    number: int
    title: str
    seconds: float
    screen: str
    stage: Callable[[Driver], None]


def _seg01(d: Driver) -> None:
    # Two seconds of black before anything, so the first word of the narration
    # lands on an empty frame rather than half a page.
    d.goto(d.file_url("black"))
    time.sleep(2.0)
    d.goto(f"{WEB}/dashboard")


def _seg02(d: Driver) -> None:
    d.ensure(f"{WEB}/dashboard")
    d.centre_on("USD 2,020.00")


def _seg03(d: Driver) -> None:
    d.goto(f"{WEB}/cases/{CASE}")


def _seg04(d: Driver) -> None:
    # The longest beat, and the one the transcript marks "do not navigate".
    # `ensure` is here only so `--segment 4` has something on screen.
    d.ensure(f"{WEB}/cases/{CASE}")


def _seg05(d: Driver) -> None:
    d.goto(f"{WEB}/cases/{CASE}/proof")
    d.centre_on("model_used")


def _seg06(d: Driver) -> None:
    d.goto(f"{WEB}/watches")
    # The predicate read as a sentence, not the s-expression under it.
    d.centre_on("outstanding_amount is greater than 0")


def _seg07(d: Driver) -> None:
    d.goto(d.file_url("write_path"))


def _seg08(d: Driver) -> None:
    d.goto(d.file_url("evidence"))


def _seg09(d: Driver) -> None:
    d.goto(f"{API}/v1/version")
    d.enlarge_plaintext()


def _seg10(d: Driver) -> None:
    d.goto(f"{WEB}/judge")
    d.centre_on("501 NOT_IMPLEMENTED")


def _seg11(d: Driver) -> None:
    d.goto(f"{WEB}/dashboard")
    d.scroll_top()


def _seg12(d: Driver) -> None:
    """The Cloud Run console. Only reached with --console."""
    d.goto(
        "https://console.cloud.google.com/run" "?project=provenance-agentic-2026&region=us-east4"
    )


#: A twelfth segment, appended only with --console.
#:
#: The rules ask in as many words for "proof of Google Cloud backend
#: deployment", and segment 9 already carries it: the URL bar reads
#: `*.a.run.app`, which is a Cloud Run domain and nothing else, and the JSON in
#: frame says `region: us-east4`, `db_ok: true`, `fixture_mode: false`. That is
#: proof a reviewer can reproduce from their own machine without a token, which is
#: worth more than a photograph of a console.
#:
#: The console is nonetheless the picture people expect, so it is available --
#: and OFF by default for a reason worth stating. It needs an authenticated
#: Google session, and Playwright launches a clean profile with none. Enabled
#: without a signed-in profile it would put a Google sign-in page in the last
#: frames of the video, which is worse than not showing the console at all.
CONSOLE_SEGMENT = Segment(
    number=12,
    title="The Cloud Run console",
    seconds=8.0,
    screen="console.cloud.google.com/run -- needs a signed-in profile",
    stage=_seg12,
)

SEGMENTS: tuple[Segment, ...] = (
    Segment(1, "The asymmetry", 26.5, "black 2s, then /dashboard", _seg01),
    Segment(2, "The record", 19.8, "/dashboard, centred on USD 2,020.00", _seg02),
    Segment(3, "The hero case", 18.5, f"/cases/{CASE[:8]}...", _seg03),
    Segment(4, "The contradiction", 34.5, "the case, held still -- no navigation", _seg04),
    Segment(5, "Why it believes it", 17.2, "/proof, centred on model_used", _seg05),
    Segment(6, "A deadline as an event", 18.5, "/watches, centred on the predicate", _seg06),
    Segment(
        7, "Agents propose, the kernel writes", 20.3, "file:// write-path argument + lint", _seg07
    ),
    Segment(8, "The evidence", 21.6, "file:// ops/ live-run transcripts", _seg08),
    Segment(9, "Google Cloud, on screen", 20.3, f"{API}/v1/version, raw", _seg09),
    Segment(10, "What is not built", 12.7, "/judge, centred on the 501", _seg10),
    Segment(11, "Close", 10.1, "/dashboard", _seg11),
)


# ---------------------------------------------------------------------------
# Progress, to stderr only
# ---------------------------------------------------------------------------


def _warn(message: str) -> None:
    sys.stderr.write(f"\n  !! {message}\n")
    sys.stderr.flush()


def _clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def _announce(segment: Segment, remaining: float) -> None:
    sys.stderr.write(
        f"\n[{_clock(remaining)}] segment {segment.number} -- {segment.title} ({segment.seconds:.1f}s)\n"
    )
    sys.stderr.flush()


def _hold_until(deadline: float, segment: Segment) -> None:
    """Sit still until the segment's deadline, ticking the countdown.

    The tick rewrites one line rather than scrolling, so a fifteen segment run
    does not bury the warnings that matter.
    """
    over = time.monotonic() - deadline
    if over > 0.3:
        _warn(f"segment {segment.number} ran {over:.1f}s over its budget -- the take is drifting")
        return
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        sys.stderr.write(f"\r      still -- {left:5.1f}s to the next segment ")
        sys.stderr.flush()
        time.sleep(min(0.2, left))
    sys.stderr.write("\r" + " " * 52 + "\r")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------


def warm_up(rounds: int = 2) -> None:
    """Hit both services twice before the browser is even open.

    Both Cloud Run services scale to zero. A cold start is about two seconds,
    and two seconds of nothing on the first frame reads as a hang rather than
    as a beat. This is §A.3 of the transcript, done in-process so it cannot be
    forgotten.
    """
    targets = (f"{WEB}/dashboard", f"{API}/v1/version")
    sys.stderr.write("warming both Cloud Run services (they scale to zero)\n")
    for round_index in range(1, rounds + 1):
        for url in targets:
            started = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=45) as response:
                    response.read()
                    status = response.status
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                _warn(f"warm-up {round_index} could not reach {url}: {exc}")
                continue
            elapsed = time.monotonic() - started
            sys.stderr.write(f"  pass {round_index}  {status}  {elapsed:5.2f}s  {url}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def print_plan() -> int:
    total = sum(segment.seconds for segment in SEGMENTS)
    drift = abs(total - TRANSCRIPT_TOTAL_SECONDS)
    within = drift <= 1.0

    print("Demo drive plan -- eleven segments, one continuous take")
    print(f"eleven segments, one fixed schedule   web: {WEB}")
    print()
    print("  #   start     dur     segment                             screen")
    start = 0.0
    for segment in SEGMENTS:
        stamp = f"{int(start) // 60}:{start - 60 * (int(start) // 60):04.1f}"
        print(
            f"  {segment.number:<2}  {stamp:<8}  {segment.seconds:5.1f}s  "
            f"{segment.title:<34}  {segment.screen}"
        )
        start += segment.seconds
    print()
    print(f"  total                {total:6.1f}s  ({_clock(total)})")
    print(
        f"  transcript total     {TRANSCRIPT_TOTAL_SECONDS:6.1f}s  ({_clock(TRANSCRIPT_TOTAL_SECONDS)})"
    )
    print(f"  difference           {drift:6.1f}s  -- within 1s: {'PASS' if within else 'FAIL'}")
    print()
    print("  assets written to    tmp/demo_assets/ at run time (segments 1, 7, 8)")
    print(
        "  warm-up              two rounds against the dashboard and /v1/version, before segment 1"
    )
    return 0 if within else 1


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _chromium_is_installed(playwright) -> bool:
    try:
        return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


def run(
    segment_numbers: list[int],
    keep_open: float,
    *,
    console: bool = False,
    profile: str | None = None,
) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("CANNOT RUN: playwright is not importable. pip install playwright", file=sys.stderr)
        return 2

    assets = build_assets()
    sys.stderr.write(f"assets rebuilt in {ASSETS}\n")

    pool = SEGMENTS + ((CONSOLE_SEGMENT,) if console else ())
    chosen = [segment for segment in pool if segment.number in segment_numbers]
    full_take = len(chosen) == len(pool)

    with sync_playwright() as playwright:
        # Check the binary before launching, so a missing browser is one clear
        # line and an exit code rather than a stack trace.
        if not _chromium_is_installed(playwright):
            print(
                "CANNOT RUN: the Chromium build playwright expects is not installed.\n"
                "  python -m playwright install chromium",
                file=sys.stderr,
            )
            return 2
        try:
            # A persistent context when --profile is given: the console segment
            # needs a signed-in Google session, and a fresh profile has none.
            # Everything else about the take is identical either way.
            if profile:
                context = playwright.chromium.launch_persistent_context(
                    profile,
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--window-size=1920,1080",
                        "--window-position=0,0",
                        "--hide-crash-restore-bubble",
                        "--disable-infobars",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                browser = None
            else:
                browser = playwright.chromium.launch(
                    headless=False,
                    args=[
                        "--window-size=1920,1080",
                        "--window-position=0,0",
                        "--hide-crash-restore-bubble",
                        "--disable-infobars",
                    ],
                    # Without this Chromium shows the "controlled by automated
                    # test software" ribbon, which would be in every frame.
                    ignore_default_args=["--enable-automation"],
                )
                context = None
        except Exception as exc:
            message = str(exc)
            if "executable doesn" in message.lower() or "install" in message.lower():
                print(
                    "CANNOT RUN: chromium is missing. python -m playwright install chromium",
                    file=sys.stderr,
                )
                return 2
            print(f"CANNOT RUN: chromium would not launch: {exc}", file=sys.stderr)
            return 2

        # no_viewport keeps the page the size of the real window, so what the
        # screen recorder captures is what playwright laid out. A forced
        # viewport different from the window size gets scaled on screen.
        #
        # A persistent context arrives already made and already carries a page,
        # so reuse both rather than opening a second window beside it.
        if context is None:
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
        else:
            page = context.pages[0] if context.pages else context.new_page()
        driver = Driver(page, assets)

        started = time.monotonic()
        elapsed_plan = 0.0
        total = sum(segment.seconds for segment in chosen)

        for segment in chosen:
            elapsed_plan += segment.seconds
            _announce(segment, total - (elapsed_plan - segment.seconds))
            # An absolute deadline, not a sleep after the work: navigation and
            # scrolling come out of the segment's own budget, so segment eight
            # still starts at 2:35 even if two pages loaded slowly.
            deadline = started + elapsed_plan if full_take else time.monotonic() + segment.seconds
            segment.stage(driver)
            _hold_until(deadline, segment)

        sys.stderr.write(
            f"\nTAKE COMPLETE -- {time.monotonic() - started:.1f}s. Stop the recording.\n"
        )
        sys.stderr.flush()
        time.sleep(keep_open)
        context.close()
        browser.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive the browser through the 4:00 demo.")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan and the timing check, open nothing"
    )
    parser.add_argument(
        "--segment", type=int, action="append", help="run one segment alone (repeatable)"
    )
    parser.add_argument(
        "--assets-only", action="store_true", help="regenerate tmp/demo_assets/ and stop"
    )
    parser.add_argument(
        "--warmup",
        dest="warmup",
        action="store_true",
        default=True,
        help="warm both Cloud Run services first (the default)",
    )
    parser.add_argument("--no-warmup", dest="warmup", action="store_false", help="skip the warm-up")
    parser.add_argument(
        "--console",
        action="store_true",
        help=(
            "append an eighth-second Cloud Run console beat. Needs --profile "
            "pointing at a browser profile already signed in to the project, or "
            "the video ends on a Google sign-in page"
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "a Chrome/Chromium user-data directory to launch with, so an "
            "existing Google session is available. Only useful with --console"
        ),
    )
    parser.add_argument(
        "--keep-open",
        type=float,
        default=3.0,
        help="seconds to hold the last frame before closing (default 3)",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        return print_plan()

    if args.assets_only:
        for name, path in build_assets().items():
            print(f"{name:<11} {path}")
        return 0

    # The console beat is appended rather than built in, so the default take is
    # exactly the eleven the narration was timed against.
    available = SEGMENTS + ((CONSOLE_SEGMENT,) if args.console else ())
    if args.console and not args.profile:
        print(
            "  [ CANNOT ] --console without --profile launches a clean browser with no\n"
            "             Google session, so the last frames would be a sign-in page.\n"
            "             Pass --profile, or drop --console: segment 9 already shows\n"
            "             the .a.run.app host and region us-east4.",
            file=sys.stderr,
        )
        return 2

    numbers = sorted(set(args.segment)) if args.segment else [s.number for s in available]
    unknown = [n for n in numbers if n not in {s.number for s in available}]
    if unknown:
        print(f"no such segment: {unknown}. Valid: 1..{len(available)}", file=sys.stderr)
        return 2

    if args.warmup:
        warm_up()

    return run(numbers, keep_open=args.keep_open, console=args.console, profile=args.profile)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
