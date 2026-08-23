"""
Plain-Win32 window lookups, deliberately NOT going through UIA.

UIA property queries (window text, control tree, etc.) can block for a
long time — sometimes past their own stated timeout — while this
Eclipse/SWT app's UI thread is still busy (booting, or a dialog still
settling right after it opens). The Win32 APIs used here
(EnumWindows/GetWindowThreadProcessId/IsWindowVisible/IsHungAppWindow)
don't go through UIA and don't have that problem, so we use them purely
to *find the handle* and confirm it's responsive, then hand that exact
handle to pywinauto.
"""

import ctypes
import logging
import time
from collections.abc import Collection

import win32con
import win32gui
import win32process

logger = logging.getLogger("automation.windows")


def find_hwnds(*, pid: int | None = None, title: str | None = None) -> list[int]:
    """
    One pass of EnumWindows: every visible top-level window matching the
    given filters, in enumeration order. The single place this process
    enumerates windows; find_hwnd() polls it.
    """
    found: list[int] = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if pid is not None:
            _, owner_pid = win32process.GetWindowThreadProcessId(hwnd)
            if owner_pid != pid:
                return True
        text = win32gui.GetWindowText(hwnd)
        if title is None:
            if not text:
                return True
        elif text != title:
            return True
        found.append(hwnd)
        return True

    win32gui.EnumWindows(callback, None)
    return found


def find_hwnd(
    *,
    pid: int | None = None,
    title: str | None = None,
    timeout: float = 60.0,
    poll: float = 0.5,
    exclude: Collection[int] = (),
) -> int:
    """
    Poll for a visible top-level window matching the given filters and
    return its handle.

    - `pid` given: only windows owned by that process match.
    - `title` given: the window text must equal it exactly (used for
      dialogs like "Select the address").
    - `title` omitted: any non-empty window text matches (used to find an
      app's main window, whose title varies by version/workspace).

    `timeout=0` makes a single pass and raises immediately if nothing
    matches, for callers that just want to know whether a window is on
    screen right now.

    `exclude` skips handles the caller already knew about. A dialog
    reopened by the same click sequence has the same title as the one
    before it, so a caller that snapshots the matching handles before
    clicking and passes them here waits for a genuinely NEW window
    instead of latching onto the previous one on its way out.
    """
    deadline = time.monotonic() + timeout
    attempts = 0
    excluded = set(exclude)

    while True:
        found = [hwnd for hwnd in find_hwnds(pid=pid, title=title) if hwnd not in excluded]
        if found:
            return found[0]

        if time.monotonic() >= deadline:
            break

        attempts += 1
        if attempts % 10 == 0:
            logger.info("...still waiting for %s", _describe(pid, title))
        time.sleep(poll)

    raise TimeoutError(f"No {_describe(pid, title)} found within {timeout}s")


def _describe(pid: int | None, title: str | None) -> str:
    if title is not None:
        owner = f" owned by PID {pid}" if pid is not None else ""
        return f"visible window titled {title!r}{owner}"
    return f"visible window for PID {pid}"


def is_window(hwnd: int) -> bool:
    """
    Whether `hwnd` still refers to a live window.

    Cheap enough to call before any coordinate work, and the difference
    between a clear message and a bare pywintypes "Invalid window handle"
    from whichever GetWindowRect happened to run first.
    """
    return bool(win32gui.IsWindow(hwnd))


def is_maximized(hwnd: int) -> bool:
    """
    Whether a window is currently maximized.

    Via ctypes rather than win32gui, which doesn't expose IsZoomed at
    all -- the same route wait_until_responsive() takes to
    IsHungAppWindow.
    """
    return bool(ctypes.windll.user32.IsZoomed(hwnd))


def maximize(hwnd: int, timeout: float = 5.0) -> bool:
    """
    Maximize a window, and confirm it actually took. Returns True if the
    window ended up maximized.

    Through Win32 rather than UIA's window pattern, for the same reason
    window discovery is: it doesn't depend on the app's UI thread being
    responsive, and it can't hang.

    This is not cosmetic. Anything driven by computed coordinates -- the
    clipboard walk over the address grid, above all -- depends on the
    window being in a known, stable state, and on it not changing size
    part-way through a run. Starting maximized every time also means the
    editors have room to lay out their fields the same way each run,
    which keeps the tree shape the FieldSpecs were confirmed against.
    """
    if is_maximized(hwnd):
        logger.info("Window %s is already maximized", hwnd)
        return True

    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_maximized(hwnd):
            return True
        time.sleep(0.1)

    # Not fatal: the run can still work in a restored window, it just
    # can't rely on the geometry being what was measured.
    logger.info("Window %s did not report as maximized within %ss", hwnd, timeout)
    return False


def wait_until_responsive(hwnd: int, timeout: float = 90.0) -> None:
    """
    Poll IsHungAppWindow until the window stops reporting itself as hung.

    A window can become win32-visible (and get picked up by find_hwnd)
    well before Eclipse/SWT has finished its startup work (OSGi bundles,
    embedded DB, migrations), and any UIA call made before that point
    will block until the app's message pump catches up. Waiting here
    avoids handing an unresponsive window to pywinauto.
    """
    deadline = time.monotonic() + timeout
    attempts = 0
    while time.monotonic() < deadline:
        if not ctypes.windll.user32.IsHungAppWindow(hwnd):
            return
        attempts += 1
        if attempts % 5 == 0:
            logger.info("...window %s not responsive yet (still starting up), waiting", hwnd)
        time.sleep(1)
    raise TimeoutError(f"Window {hwnd} did not become responsive within {timeout}s")
