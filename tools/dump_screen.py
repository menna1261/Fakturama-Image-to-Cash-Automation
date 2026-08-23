"""
Standalone screen dumper: attach to a running Fakturama and print one
editor tab's labels and fields, without re-running the whole flow.

`entry_point.py --dump-ui` can only dump a screen the flow actually reaches, and
re-running to the point of failure costs a full launch, extraction,
debtor resolution and VAT pass every time a single FieldSpec needs
checking. When a run stops for manual review it leaves the screen it
failed on open — so attach to that instead and look at it directly.

Assumes Fakturama is already running with the screen of interest open
(same assumption as test.py).

Usage:
    uv run python dump_screen.py                  # list the open tabs
    uv run python dump_screen.py "New product"    # dump that tab
    uv run python dump_screen.py "New product" --edits
    uv run python dump_screen.py --text "price"   # find by visible text
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pywinauto import Application  # noqa: E402

from utils.automation.editors import list_open_tabs  # noqa: E402
from utils.automation.processes import find_pids  # noqa: E402
from utils.automation.resolver import (  # noqa: E402
    dump_labels_and_fields,
    find_controls_by_text,
)
from utils.automation.windows import find_hwnd, wait_until_responsive  # noqa: E402
from utils.run_log import tee_stdout  # noqa: E402

FAKTURAMA_IMAGE = "Fakturama.exe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tab",
        nargs="?",
        help=(
            "Substring of the editor tab to dump (e.g. 'New product'). "
            "Omit to just list the open tabs."
        ),
    )
    parser.add_argument(
        "--edits",
        action="store_true",
        help=(
            "Also list every Edit descendant on its own, with its title. Use "
            "when a BY_TITLE spec failed and you need to see which Edits "
            "actually carry a title and which are anonymous."
        ),
    )
    parser.add_argument(
        "--out",
        nargs="?",
        const="screen_dump.log",
        default=None,
        help=(
            "Also write the dump to a UTF-8 file instead of only the "
            "terminal. Defaults to screen_dump.log when given no value."
        ),
    )
    parser.add_argument(
        "--text",
        default=None,
        help=(
            "Search the whole main window for controls whose title contains "
            "this, of any control_type. Use when you know the visible label "
            "but not what it's rendered as."
        ),
    )
    return parser.parse_args()


def connect_to_main_window():
    """Attach to the running Fakturama, Win32 handle first — same order as
    workflow/context.py, for the same reason (a UIA wait on a busy SWT
    window can hang past its own timeout)."""
    pids = find_pids(FAKTURAMA_IMAGE)
    if not pids:
        raise SystemExit(
            f"{FAKTURAMA_IMAGE} is not running. Start it (and open the screen "
            f"you want to look at) first."
        )
    pid = pids[0]
    hwnd = find_hwnd(pid=pid, timeout=30)
    wait_until_responsive(hwnd)
    app = Application(backend="uia").connect(handle=hwnd, timeout=10)
    main_win = app.window(handle=hwnd)
    main_win.wait("ready", timeout=30)
    print(f"Attached to PID {pid}, window {hwnd}: {main_win.window_text()!r}")
    return main_win


def find_tab(main_win, needle: str):
    """
    Resolve the tab whose title contains `needle`, matched case-insensitively.

    Substring rather than exact, because Eclipse decorates a dirty editor's
    title with a leading '*' ("*New product") and renames it outright after
    a save.
    """
    wanted = needle.casefold()
    for tab in main_win.descendants(control_type="Tab"):
        if wanted in tab.window_text().casefold():
            print(f"Dumping tab {tab.window_text()!r}")
            return tab
    raise SystemExit(
        f"No open tab matching {needle!r}. Open tabs: {list_open_tabs(main_win)}"
    )


def main() -> int:
    args = parse_args()
    with tee_stdout(args.out) as log_path:
        if log_path:
            print(f"Writing this dump to: {log_path}")
        return _dump(args)


def _dump(args: argparse.Namespace) -> int:
    main_win = connect_to_main_window()

    if args.text:
        find_controls_by_text(main_win, args.text)

    if not args.tab:
        if not args.text:
            list_open_tabs(main_win)
        return 0

    scope = find_tab(main_win, args.tab)
    dump_labels_and_fields(scope)

    if args.edits:
        print("-" * 80)
        print("EDIT CONTROLS (title, then rectangle):")
        for edit in scope.descendants(control_type="Edit"):
            print(f"  Edit title={edit.window_text()!r:34s} rect={edit.rectangle()}")
        print("-" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
