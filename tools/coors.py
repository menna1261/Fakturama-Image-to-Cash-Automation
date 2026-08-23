"""
Measure screen coordinates with the mouse.

Built for pinning down the hand-tuned constants in test.py
(CLICK_OFFSET_X, FIRST_ROW_OFFSET_Y, ROW_HEIGHT, VISIBLE_ROWS), which are
offsets from a dialog's top-left corner — so every reading is reported
both as an absolute screen point and relative to the target window,
which is what you actually need.

Two modes:

  Click mode (default) — every left-click is recorded, with its offset
  inside the window and the delta from the previous click. This is the
  one to use for the grid: click row 1, then row 2, and the reported
  delta-y IS the row height. Clicking the rows is harmless; it just
  selects them.

      uv run coors.py

  Hover mode — a live readout of wherever the cursor is, no clicking.
  Useful for finding an edge or a control you'd rather not click.
  Note the console needs to stay visible; move the mouse without
  clicking and read the line as it updates.

      uv run coors.py --hover

Either mode: Ctrl+C to stop and print a summary.

By default measurements are relative to the "Select the address" dialog
if it's open, falling back to whatever top-level window is under the
cursor. Pass --title to pin to a different window, or --title "" to
always use the window under the cursor.
"""

import argparse
import sys
import time

import win32api
import win32con
import win32gui

DEFAULT_TITLE = "Select the address"
POLL_SECONDS = 0.02
HOVER_REFRESH_SECONDS = 0.1


def find_window_by_title(title: str) -> int | None:
    """The first visible top-level window with exactly this title, if any."""
    found = []

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return found[0] if found else None


def top_level_window_at(point: tuple[int, int]) -> int:
    """
    The top-level window under `point`.

    WindowFromPoint returns the innermost child control, which is rarely
    what you want to measure against — GA_ROOT walks up to the real
    window whose top-left corner the offsets are relative to.
    """
    hwnd = win32gui.WindowFromPoint(point)
    return win32gui.GetAncestor(hwnd, win32con.GA_ROOT)


def describe_point(
    point: tuple[int, int],
    hwnd: int | None,
    previous: tuple[int, int] | None = None,
) -> str:
    """One human-readable line for a measured point."""
    x, y = point
    parts = [f"screen=({x:>5}, {y:>5})"]

    if hwnd:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        title = win32gui.GetWindowText(hwnd) or "<untitled>"
        rel_x, rel_y = x - left, y - top
        inside = left <= x <= right and top <= y <= bottom
        parts.append(f"{title!r} rel=({rel_x:>4}, {rel_y:>4}){'' if inside else ' [OUTSIDE]'}")

    if previous is not None:
        dx, dy = x - previous[0], y - previous[1]
        parts.append(f"delta=({dx:+}, {dy:+})")

    return "  |  ".join(parts)


def resolve_target(title: str, point: tuple[int, int]) -> int | None:
    """
    The window to measure against: the pinned title if it's on screen,
    otherwise whatever top-level window the cursor is over.
    """
    if title:
        hwnd = find_window_by_title(title)
        if hwnd:
            return hwnd
    return top_level_window_at(point)


def watch_clicks(title: str, points: list[tuple[int, int]]) -> None:
    """
    Record a point per left-click until interrupted.

    `points` is passed in and appended to rather than returned, because
    the only way out of this loop is Ctrl+C — a return value would never
    reach the caller, and the summary would come out empty.
    """
    print("Click anywhere to record a point. Ctrl+C when done.\n")
    was_down = False

    while True:
        # High bit set means the button is down right now; edge-detect so
        # one press records one point rather than one per poll.
        is_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
        if is_down and not was_down:
            point = win32gui.GetCursorPos()
            hwnd = resolve_target(title, point)
            previous = points[-1] if points else None
            print(f"  [{len(points) + 1:>2}] {describe_point(point, hwnd, previous)}")
            points.append(point)
        was_down = is_down
        time.sleep(POLL_SECONDS)


def watch_hover(title: str) -> None:
    print("Move the mouse to read coordinates (no clicking). Ctrl+C when done.\n")
    last_point = None

    while True:
        point = win32gui.GetCursorPos()
        if point != last_point:
            hwnd = resolve_target(title, point)
            # \r keeps this on one self-updating line instead of
            # scrolling thousands of near-identical readings past.
            print(f"  {describe_point(point, hwnd):<90}", end="\r", flush=True)
            last_point = point
        time.sleep(HOVER_REFRESH_SECONDS)


def print_summary(points: list[tuple[int, int]]) -> None:
    if not points:
        print("\nNo points recorded.")
        return

    print(f"\n\nRecorded {len(points)} point(s).")
    if len(points) < 2:
        return

    gaps = [b[1] - a[1] for a, b in zip(points, points[1:])]
    vertical = [g for g in gaps if g]
    print(f"  Vertical gaps between consecutive clicks: {gaps}")
    if vertical:
        print(
            f"  -> if those were consecutive grid rows, ROW_HEIGHT is "
            f"{min(vertical)}..{max(vertical)}"
        )
    print(
        "\nMapping to test.py: the FIRST row's rel=(x, y) gives CLICK_OFFSET_X and "
        "FIRST_ROW_OFFSET_Y; the gap between consecutive rows gives ROW_HEIGHT; the "
        "number of rows before the grid stops scrolling gives VISIBLE_ROWS."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure screen coordinates with the mouse.")
    parser.add_argument(
        "--hover",
        action="store_true",
        help="Read coordinates by hovering instead of clicking",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE,
        help=(
            "Window title to measure offsets against (default: "
            f"{DEFAULT_TITLE!r}). Pass an empty string to always use the "
            "window under the cursor."
        ),
    )
    args = parser.parse_args()

    if args.title:
        if find_window_by_title(args.title):
            print(f"Measuring relative to {args.title!r}.")
        else:
            print(
                f"NOTE: no window titled {args.title!r} is open — falling back to "
                f"whatever window is under the cursor."
            )
    else:
        print("Measuring relative to whatever window is under the cursor.")

    points: list[tuple[int, int]] = []
    try:
        if args.hover:
            watch_hover(args.title)
        else:
            watch_clicks(args.title, points)
    except KeyboardInterrupt:
        pass
    finally:
        print_summary(points)


if __name__ == "__main__":
    sys.exit(main())
