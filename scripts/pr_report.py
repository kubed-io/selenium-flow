"""Markdown for the sticky PR comments the profile and benchmark jobs post.

    python scripts/pr_report.py profile dist/profile [ARTIFACT_URL]
    python scripts/pr_report.py bench dist/bench.json bench-baseline/benchmark-data.json

A pull request cannot show a flamegraph inline: GitHub takes no image uploads
through its API and will not render an SVG from the repository in a comment. So
the comment carries what the flamegraph says in numbers — the responsiveness
timings and where the server spent its CPU — and links the SVG artifact (§F4.19).
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

# Every frame in py-spy's flamegraph (inferno) is a titled rect carrying its
# exact sample offset and width, fg:x and fg:w. py-spy draws the root at the
# top, so a frame's callees sit one row (16px) below it.
FRAME = re.compile(
    r'<title>(.*?) \([\d,]+ samples?, [\d.]+%\)</title><rect[^>]*?y="(\d+)"'
    r'[^>]*?fg:x="(\d+)" fg:w="(\d+)"'
)
PATH = re.compile(r"\((?:\S*?/(?:site-packages|python3\.\d+|kubed)/)")
TOP = 12
PACKAGE = "selenium_flow/"
# Under every sample, so their totals say nothing.
ENTRY_POINTS = ("main (selenium_flow/main.py", "run (selenium_flow/server.py")
# py-spy's own grouping rows, not functions.
SYNTHETIC = re.compile(r"(thread|process) \(\d+\)")


def hot_frames(svg: str) -> tuple[int, list[tuple[str, int, int]]]:
    """Total samples, and every function as (name, self, total).

    Self is the samples a function was on top of the stack for — its own work,
    not its callees' — which is what finds a hot spot: the entry points are in
    every sample and say nothing. Ranked by self, from any package, because a
    hot spot inside a library we call is still ours to avoid (§F4.19).
    """
    rects = [
        (html.unescape(label), int(y), int(x), int(w))
        for label, y, x, w in FRAME.findall(svg)
    ]
    by_row: dict[int, list[tuple[int, int]]] = {}
    for _, y, x, w in rects:
        by_row.setdefault(y, []).append((x, w))
    total = max((x + w for _, _, x, w in rects), default=0)
    own: dict[str, list[int]] = {}
    for label, y, x, w in rects:
        if label == "all" or SYNTHETIC.match(label):
            continue
        callees = sum(cw for cx, cw in by_row.get(y + 16, []) if x <= cx < x + w)
        name = PATH.sub("(", label)
        mine = own.setdefault(name, [0, 0])
        mine[0] += w - callees
        mine[1] += w
    rows = [(name, self_, all_) for name, (self_, all_) in own.items()]
    return total, rows


def profile(directory: Path, artifact_url: str = "") -> str:
    parts = ["## 🔥 Integration profile\n"]
    timings = directory / "responsiveness.md"
    if timings.is_file():
        parts.append(timings.read_text())
    svg = directory / "server.svg"
    if svg.is_file():
        total, frames = hot_frames(svg.read_text())
        hottest = sorted(frames, key=lambda f: -f[1])[:TOP]
        ours = sorted(
            (f for f in frames if _ours(f[0])), key=lambda f: -f[2]
        )[:TOP]
        parts.append(
            f"\n### Where the server spent its CPU\n\n{total} samples at 100Hz "
            "(py-spy, whole run, MCP and the admin page at once). Self is a "
            "function's own work, total includes what it called.\n\n"
            "| Function | Self | Total |\n|---|---|---|\n"
            + _table(hottest, total)
            + "\n**In our code**, by total — what each function and its callees "
            "cost, leaving out startup and the entry points every sample is under."
            "\n\n| Function | Self | Total |\n|---|---|---|\n"
            + _table(ours, total)
        )
        link = f"[the flamegraph]({artifact_url})" if artifact_url else "the flamegraph"
        parts.append(f"\nOpen {link} (`integration-profile`, `server.svg`).\n")
    return "\n".join(parts)


def _ours(name: str) -> bool:
    return (
        f"({PACKAGE}" in name
        and not name.startswith("<module>")
        and not name.startswith(ENTRY_POINTS)
    )


def _table(frames: list[tuple[str, int, int]], total: int) -> str:
    return "".join(
        f"| `{name}` | {own / total:.0%} | {every / total:.0%} |\n"
        for name, own, every in frames
    )


def bench(current: Path, baseline: Path) -> str:
    """This run's benchmarks beside main's latest, fastest first."""
    now = {
        b["fullname"]: b["stats"]["ops"]
        for b in json.loads(current.read_text())["benchmarks"]
    }
    before: dict[str, float] = {}
    if baseline.is_file():
        entries = json.loads(baseline.read_text()).get("entries", {})
        runs = next(iter(entries.values()), [])
        if runs:
            before = {b["name"]: b["value"] for b in runs[-1]["benches"]}
    rows = []
    for name, ops in sorted(now.items(), key=lambda item: -item[1]):
        was = before.get(name)
        change = f"{ops / was:.2f}x" if was else "new"
        rows.append(
            f"| `{name.split('::')[-1]}` | {_time(ops)} | "
            f"{_time(was) if was else '—'} | {change} |\n"
        )
    note = (
        "" if before else "\nNo baseline from main yet; the next main run makes one.\n"
    )
    return (
        "## ⏱️ Benchmarks\n\nMean time per call; the change is speed against "
        "main, so above 1x is faster. Shared runners swing 10-20%.\n\n"
        "| Benchmark | This PR | Main | Change |\n|---|---|---|---|\n"
        + "".join(rows)
        + note
    )


def _time(ops: float) -> str:
    seconds = 1 / ops
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.2f} ms"
    return f"{seconds * 1e6:.0f} µs"


def main(argv: list[str]) -> int:
    if argv[:1] == ["profile"] and len(argv) in (2, 3):
        print(profile(Path(argv[1]), argv[2] if len(argv) == 3 else ""))
        return 0
    if argv[:1] == ["bench"] and len(argv) == 3:
        print(bench(Path(argv[1]), Path(argv[2])))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
