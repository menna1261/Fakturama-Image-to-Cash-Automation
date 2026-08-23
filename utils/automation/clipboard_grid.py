"""
Reading a results grid whose rows aren't exposed via UIA at all, by
driving it like a person would: land on a row, Ctrl+C, read the
clipboard, repeat.

Needed because the "Select the address" dialog's grid is a
custom-painted SWT Table with zero row-level accessibility — confirmed
via a full print_control_identifiers dump taken with a real, visibly
rendered, exactly-matching row on screen. grids.py can see nothing
there, so without this a Debtor that genuinely exists is
indistinguishable from one that doesn't.

Everything here is coordinate arithmetic against the dialog's own
top-left corner, so the numbers in GridGeometry have to be measured
against the real dialog (use `coors.py`) rather than guessed. Two
independent safeguards keep a wrong number from turning into wrong data:
every computed point is checked to be inside the dialog before it's
clicked, and the row this module finally selects is read back and
compared before the caller is told it worked.

Note this takes over the clipboard for the duration — the run already
owns the mouse and keyboard, so that's in keeping, but anything the user
had copied is gone afterwards. It also has to cope with the app throwing
a modal error dialog when asked to copy from an empty grid; see
_ERROR_DIALOG_TITLE below.
"""

import logging
import time
from dataclasses import dataclass

import win32clipboard
import win32con
import win32gui
import win32process
from pywinauto import mouse
from pywinauto.keyboard import send_keys

from utils.automation.windows import find_hwnds, is_window

logger = logging.getLogger("automation.clipboard_grid")

# Let the grid repaint after a click/keypress before reading it.
_SETTLE_SECONDS = 0.15

# Ctrl+C on a grid with NO rows makes this Fakturama build throw
# "java.lang.NullPointerException: Cannot read the array length because
# 'this.copiedCells' is null" into a modal error dialog — the copy
# handler assumes a selection exists. An empty grid is a perfectly
# ordinary outcome (the searched-for Debtor doesn't exist yet, so we go
# on to create it), so the popup has to be dismissed and read as "no
# rows" rather than allowed to block every step that follows.
_ERROR_DIALOG_TITLE = "Internal Error"
_ERROR_DISMISS_TIMEOUT = 5.0


@dataclass(frozen=True)
class GridGeometry:
    """
    Where a specific dialog's grid rows sit, relative to its window's
    top-left corner. Measure these with `coors.py` (click row 1, then
    row 2: the reported delta-y is `row_height`).
    """

    click_offset_x: int  # into the first column, clear of the row edges
    first_row_offset_y: int  # centre of row 1, just below the column headers
    row_height: int
    visible_rows: int  # rows drawn at once; past these the grid scrolls
    max_rows: int = 50  # safety cap so a stuck walk can't run forever


def clear_clipboard() -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
    finally:
        win32clipboard.CloseClipboard()


def get_clipboard_text() -> str:
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return ""
    finally:
        win32clipboard.CloseClipboard()


def _pid_of(hwnd: int) -> int:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return pid


def dismiss_error_dialog(pid: int) -> bool:
    """
    Dismiss the app's "Internal Error" popup if it's on screen, pressing
    Enter to take its default OK button. Returns True if one was there.

    Scoped to `pid` so this can only ever close a dialog belonging to the
    app we're driving, never some unrelated window that happens to share
    the title.
    """
    hwnds = find_hwnds(pid=pid, title=_ERROR_DIALOG_TITLE)
    if not hwnds:
        return False

    print(f"{_ERROR_DIALOG_TITLE!r} dialog appeared — dismissing it.")
    deadline = time.monotonic() + _ERROR_DISMISS_TIMEOUT
    while time.monotonic() < deadline:
        hwnds = find_hwnds(pid=pid, title=_ERROR_DIALOG_TITLE)
        if not hwnds:
            return True
        try:
            win32gui.SetForegroundWindow(hwnds[0])
        except Exception as e:
            # Windows refuses foreground changes in some states; the
            # dialog is modal and usually already focused, so Enter is
            # still worth sending.
            logger.info("Could not foreground the error dialog (%s) — sending Enter anyway", e)
        time.sleep(_SETTLE_SECONDS)
        send_keys("{ENTER}")
        time.sleep(_SETTLE_SECONDS)

    raise RuntimeError(
        f"The {_ERROR_DIALOG_TITLE!r} dialog would not close within "
        f"{_ERROR_DISMISS_TIMEOUT}s — it is modal, so nothing further can run "
        f"until it is closed by hand."
    )


def read_selected_row(hwnd: int) -> str:
    """
    Copy whatever row the grid currently has selected and read it back.

    Returns "" both when nothing was copied and when the copy raised the
    app's NullPointerException popup — which is what an empty grid does
    (see _ERROR_DIALOG_TITLE above). Both mean the same thing to every
    caller here: there is no row to read.
    """
    clear_clipboard()
    send_keys("^c")
    time.sleep(_SETTLE_SECONDS)

    if dismiss_error_dialog(_pid_of(hwnd)):
        print("  (the copy failed because the grid has no rows)")
        return ""

    return get_clipboard_text()


def require_window(hwnd: int, what: str) -> None:
    """
    Fail loudly if `hwnd` is no longer a live window.

    Every coordinate here is derived from GetWindowRect, which raises a
    bare pywintypes "Invalid window handle" the moment the dialog is gone
    — a traceback that says nothing about which dialog or why. Worse, the
    obvious alternative of treating a dead handle as "no rows" would be
    actively harmful: "no rows" is how this codebase spells "the record
    doesn't exist", so a vanished dialog would silently become a decision
    to create a duplicate.
    """
    if not is_window(hwnd):
        raise RuntimeError(
            f"The {what} window (handle {hwnd}) no longer exists, so its grid "
            f"can't be read. It was open a moment ago — something closed it "
            f"mid-read (a stray keystroke reaching it, or the app dismissing "
            f"it). Not treating this as 'no matching rows': that would create "
            f"a duplicate record."
        )


def row_click_point(hwnd: int, geometry: GridGeometry, row_index: int) -> tuple[int, int]:
    """
    Screen coordinate of row `row_index` (0-based, in walk order).

    The multiplication is clamped at `visible_rows`. The grid only draws
    that many lines, so once the walk goes past the last one the grid
    scrolls instead of growing: the selected row is then sitting on the
    BOTTOM visible line, not somewhere further down the window. So every
    row past the fold is clicked at the last line's position — which is
    where the walk has actually parked it.
    """
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    visible_index = min(row_index, geometry.visible_rows - 1)
    x = left + geometry.click_offset_x
    y = top + geometry.first_row_offset_y + visible_index * geometry.row_height
    return x, y


def click_row(hwnd: int, geometry: GridGeometry, row_index: int) -> bool:
    """
    Click row `row_index`. Returns False without clicking if the computed
    point falls outside the dialog — a moved or resized dialog would
    otherwise send the click straight through to whatever is behind it,
    which in this app means clicking something in the Order editor.
    Refusing to click is recoverable; clicking the wrong thing isn't.
    """
    _, top, _, bottom = win32gui.GetWindowRect(hwnd)
    x, y = row_click_point(hwnd, geometry, row_index)

    if not top < y < bottom:
        logger.info(
            "Row %s: computed click point (%s, %s) is outside the dialog "
            "(top %s, bottom %s) — not clicking.",
            row_index + 1, x, y, top, bottom,
        )
        return False

    mouse.click(coords=(x, y))
    time.sleep(_SETTLE_SECONDS)
    return True


def walk_rows(hwnd: int, geometry: GridGeometry) -> list[str]:
    """
    Read every row of the grid, top to bottom, and return their raw
    clipboard texts in order.

    Each row is landed on two ways: the Down key moves the grid's
    selection (and scrolls it once past the last visible line), and a
    real click makes the dialog treat that row as picked. Row 1 needs no
    Down — the click alone lands on it.

    The order within one step is read-then-click, not click-then-read.
    The walk ends when the clipboard comes back empty or repeats the
    previous row — at the last row the Down key doesn't move — and
    reading first means that end is recognised before a click is spent
    on it. Clicking first meant every completed walk ended with one
    click on empty space below the last row.

    An empty grid returns [] on the first read — the copy raises the
    app's own NullPointerException popup, which read_selected_row()
    dismisses and reports as "no row". That's a normal result, not a
    failure: it means the record being searched for doesn't exist yet.
    """
    require_window(hwnd, "results grid's")
    rows: list[str] = []

    for row_index in range(geometry.max_rows):
        if row_index == 0:
            # The dialog opens with focus in the search box and nothing
            # selected, so the first row has to be clicked before there
            # is anything for Ctrl+C to copy.
            if not click_row(hwnd, geometry, 0):
                break
        else:
            send_keys("{DOWN}")
            time.sleep(_SETTLE_SECONDS)

        # Read BEFORE clicking, so the end of the grid is recognised
        # without clicking past it. At the last row the Down key doesn't
        # move, and the click that used to come first landed on empty
        # space below the rows every single time the walk finished.
        text = read_selected_row(hwnd)
        if not text:
            logger.info("Row %s: clipboard empty — no more rows.", row_index + 1)
            break
        if rows and text == rows[-1]:
            logger.info("Row %s: same content as the previous row — end of grid.", row_index + 1)
            break

        # A real, previously unseen row: now make the dialog treat it as
        # chosen, which moving the selection with the keyboard alone does
        # not do.
        if row_index > 0 and not click_row(hwnd, geometry, row_index):
            break

        print(f"  Row {row_index + 1}: {text!r}")
        rows.append(text)
    else:
        print(
            f"WARNING: hit the {geometry.max_rows}-row cap without reaching the end "
            f"of the grid — later rows were not read."
        )

    return rows


def select_row(hwnd: int, geometry: GridGeometry, row_index: int, expected_text: str) -> bool:
    """
    Re-select the row at `row_index` and confirm it really is the one
    whose text is `expected_text`. Returns False if it can't be, in which
    case the caller must NOT proceed as though the row were selected.

    A re-selection is needed because reading the grid walks all the way
    to the bottom, which leaves the selection on the last row (and, on a
    grid taller than the fold, leaves it scrolled). Home returns to the
    first row, then Down steps back to the wanted one.

    The read-back is the point of this function. Every coordinate here
    comes from measured constants that could drift with a theme, DPI or
    font change; clicking OK on an unverified row would attach the wrong
    Debtor to the Order, which is exactly the kind of silently-wrong
    result worth failing loudly instead.
    """
    # The walk leaves the selection sitting on the last row it read, so
    # when the match IS that row — the usual outcome once the search box
    # has filtered the grid down — it's already selected and the whole
    # Home/Down/click dance is a no-op worth skipping.
    if read_selected_row(hwnd).strip() == expected_text.strip():
        print(f"Row {row_index + 1} is already selected (the walk ended on it).")
        return True

    send_keys("{HOME}")
    time.sleep(_SETTLE_SECONDS)
    for _ in range(row_index):
        send_keys("{DOWN}")
        time.sleep(_SETTLE_SECONDS)

    if not click_row(hwnd, geometry, row_index):
        print(f"FAILED: could not click row {row_index + 1} to select it.")
        return False

    actual = read_selected_row(hwnd)
    if actual.strip() != expected_text.strip():
        print(
            f"FAILED: after selecting row {row_index + 1} the grid reports\n"
            f"  {actual!r}\n"\
            f"but the matched row was\n  {expected_text!r}\n"
            f"— the grid geometry {geometry} no longer matches the real dialog. "
            f"Re-measure it with coors.py."
        )
        return False

    print(f"Row {row_index + 1} selected and confirmed.")
    return True


def row_text_matches(text: str, expected: dict[str, str]) -> bool:
    """
    True when every non-empty value in `expected` appears somewhere in a
    row's copied text.

    Substring matching, not per-cell equality: the clipboard hands back
    one tab-joined blob for the whole row, with no way to tell which cell
    a value came from. Blank expected values are skipped rather than
    failing the match — the extraction legitimately leaves optional
    fields empty. Logs field by field, so a near-miss (case, spacing, a
    column the row doesn't actually carry) is visible immediately.
    """
    normalized = text.strip().casefold()

    all_match = True
    for field_name, value in expected.items():
        if not value:
            print(f"    [SKIP] {field_name}: no expected value")
            continue
        found = value.strip().casefold() in normalized
        print(f"    [{'OK' if found else 'MISS'}] {field_name}: expected {value!r}")
        if not found:
            all_match = False

    return all_match
