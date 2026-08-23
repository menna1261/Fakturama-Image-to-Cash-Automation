"""
The shared run context, plus connecting to Fakturama to build one.

Context is what every step function takes instead of a growing tuple of
positional arguments — the previous flow had already reached
`finish_debtor(app, main_win, debtor_header, order_editor, order_header,
pid, debtor)`, and steps 5-8 (products, VAT, invoice) would each have
added more.

The `editors` dict is where the resolve-once-reuse-forever rule lives:
each opened editor tab is stashed under a name so a later step can
switch back to it without a title_re re-search, which Eclipse's
post-save tab renaming would break. See automation/editors.py.
"""

import subprocess
import time
from dataclasses import dataclass, field

from pywinauto import Application

from utils.automation.editors import Editor
from utils.automation.processes import close_processes, find_pids
from utils.automation.resolver import dump_labels_and_fields
from utils.automation.windows import find_hwnd, maximize, wait_until_responsive
from utils.extraction.schema import Debtor, OrderExtraction


class ManualReviewRequired(Exception):
    """
    Raised by any step that hits a situation the design doc says a human
    must resolve — ambiguous matches, a record still missing after we
    created it, a payment method that can't be assigned.

    Central to the flow's error handling: entry_point.py catches this once and
    exits with a distinct code, so no individual branch can forget to
    check for it and let the run report false success.
    """


@dataclass
class Context:
    app: Application
    hwnd: int
    pid: int
    main_win: object
    extraction: OrderExtraction
    payment_method_override: str | None = None
    dump_ui: bool = False
    # Off by default: setting per-line quantity/price/discount means
    # driving the Order's NatTable by coordinate, opening a cell editor
    # per cell. It is the least stable thing the bot does, and every
    # other step works without it. Opt in with --fill-line-items.
    fill_line_items: bool = False
    editors: dict[str, Editor] = field(default_factory=dict)

    @property
    def debtor(self) -> Debtor:
        return self.extraction.debtor

    @property
    def payment_method(self) -> str:
        """
        The payment method to drive the UI with — the --test-payment-method
        override if given, otherwise whatever was extracted from the image.
        """
        return self.payment_method_override or self.extraction.payment.method

    def editor(self, name: str) -> Editor:
        try:
            return self.editors[name]
        except KeyError:
            raise ManualReviewRequired(
                f"Expected the {name!r} editor to be open by now, but it isn't."
            ) from None

    def maybe_dump(self, scope) -> None:
        """Dump a screen's labels/fields, but only under --dump-ui."""
        if self.dump_ui:
            dump_labels_and_fields(scope)


FAKTURAMA_IMAGE = "Fakturama.exe"

# After closing an old instance, give Windows a moment to actually
# release the workspace/database lock files before the new one starts —
# otherwise the fresh launch can fail on a lock its predecessor only
# just let go of.
_RESTART_SETTLE_SECONDS = 2

"""
Fakturama process
       │
       │ PID = 17816
       ↓
Windows process
       │
       │ owns
       ↓
Main window
       │
       │ HWND = 123456
       ↓
pywinauto connects to this window

"""

def find_fakturama_pid() -> int:
    pids = find_pids(FAKTURAMA_IMAGE)
    if not pids:
        raise RuntimeError(
            f"{FAKTURAMA_IMAGE} is not running. Start it first, or omit --attach "
            f"to have this script launch it."
        )
    if len(pids) > 1:
        print(
            f"WARNING: {len(pids)} {FAKTURAMA_IMAGE} processes are running {pids} — "
            f"attaching to the first. Drop --attach to close them all and start clean."
        )
    return pids[0]


def connect(
    extraction: OrderExtraction,
    *,
    exe_path: str | None,
    attach: bool,
    kill_existing: bool = True,
    payment_method_override: str | None = None,
    dump_ui: bool = False,
    fill_line_items: bool = False,
) -> Context:
    """
    Launch or attach to Fakturama and return a ready-to-use Context.

    When launching, any Fakturama left over from an earlier run is closed
    first (unless kill_existing=False): a leftover instance may be
    sitting on a half-filled editor or a modal dialog, and every step
    here assumes it starts from a clean main window. `attach` never
    closes anything — the whole point of attaching is to drive the
    instance that's already open.

    Connects UIA directly to the main window's known handle — no
    title/class search, no desktop enumeration. All subsequent lookups
    are scoped to this window's own subtree, which is far smaller and
    faster than the desktop.
    """
    if attach:
        pid = find_fakturama_pid()
        print(f"Found {FAKTURAMA_IMAGE} (PID {pid}). Locating its main window...")
    else:
        if kill_existing and close_processes(FAKTURAMA_IMAGE):
            time.sleep(_RESTART_SETTLE_SECONDS)
        print(f"Starting: {exe_path}")
        # Popen returns immediately; it doesn't wait for the process to
        # finish booting.
        pid = subprocess.Popen([exe_path]).pid
        print(f"Started {FAKTURAMA_IMAGE} (PID {pid}). Waiting for its window...")

    hwnd = find_hwnd(pid=pid, timeout=60)
    print(f"Found window handle {hwnd}.")

    print("Waiting for window to become responsive...")
    wait_until_responsive(hwnd)
    print("Window is responsive.")

    # Maximize before any UIA lookup, so every screen is inspected and
    # driven at the same size the FieldSpecs and the address grid's
    # measured geometry were confirmed against.
    if maximize(hwnd):
        print("Window maximized.")
    else:
        print("WARNING: could not maximize the window; coordinate-driven steps "
              "(the address grid) may not match their measured geometry.")

    app = Application(backend="uia").connect(handle=hwnd, timeout=10)
    main_win = app.window(handle=hwnd)
    main_win.wait("ready", timeout=30)
    main_win.set_focus()
    print(f"Main window: title={main_win.window_text()!r}")

    return Context(
        app=app,
        hwnd=hwnd,
        pid=pid,
        main_win=main_win,
        extraction=extraction,
        payment_method_override=payment_method_override,
        dump_ui=dump_ui,
        fill_line_items=fill_line_items,
    )
