"""
Opening, switching between, saving and closing Eclipse editor tabs.

Every Fakturama screen this bot drives (New Order, New Debtor, terms of
payment, ...) opens as its own top-level editor tab, and they all need
the same three things done to them — hence one place for it.

Two hard-won rules are baked in here:

- **Resolve each tab to a CONCRETE wrapper exactly once, at open time.**
  Eclipse renames a tab's title after Save (e.g. "*New Debtor" -> the
  saved contact's name), which breaks any later title_re-based
  re-search. A resolved wrapper keeps tracking the same underlying
  native widget regardless of later title changes, since the widget
  itself isn't recreated, just relabeled.
- **A tab's content pane and its tab header are two different controls**
  (control_type "Tab" vs "TabItem"). Both are captured up front:
  the pane is the scope for field lookups, the header is what you click
  to bring the editor back to the foreground.
"""

from dataclasses import dataclass

from pywinauto.timings import TimeoutError as PWATimeoutError


class EditorNotOpenedError(Exception):
    """Raised when a click that should have opened an editor tab didn't."""


@dataclass
class Editor:
    """One open editor tab: its content pane plus its clickable header."""

    name: str
    pane: object
    header: object | None = None

    def switch_to(self) -> None:
        """
        Bring this editor to the foreground.

        Merely holding a reference to an editor doesn't make it the
        active tab, and controls inside a non-active tab stop reporting
        as "visible enabled" via UIA — so lookups against them time out
        until the tab is actually clicked back into focus.
        """
        if self.header is None:
            raise EditorNotOpenedError(
                f"Editor {self.name!r} has no resolved tab header to click"
            )
        self.header.click_input()


def open_editor(main_win, title_re: str, name: str, timeout: float = 15) -> Editor:
    """
    Wait for an editor tab matching `title_re` to appear and return it as
    an Editor with both its pane and header resolved.

    Raises EditorNotOpenedError if the pane never appears — i.e. whatever
    was clicked did not open the expected editor.
    """
    try:
        pane = main_win.child_window(title_re=title_re, control_type="Tab").wait(
            "visible", timeout=timeout
        )
    except PWATimeoutError as e:
        raise EditorNotOpenedError(
            f"No editor tab matching {title_re!r} appeared within {timeout}s. "
            f"Open tabs right now: {list_open_tabs(main_win)}"
        ) from e

    try:
        header = main_win.child_window(title_re=title_re, control_type="TabItem").wait(
            "visible", timeout=timeout
        )
    except PWATimeoutError:
        # The pane is what field lookups need, so a missing header is
        # only fatal later, if something actually tries to switch back
        # to this editor.
        print(f"WARNING: opened {name!r} but could not resolve its tab header — switch_to() will fail.")
        header = None

    print(f"SUCCESS: {name!r} editor is open.")
    return Editor(name=name, pane=pane, header=header)


def save_current_editor(main_win) -> None:
    """
    Save the active editor via Ctrl+S rather than hunting for the toolbar
    Save button/icon — avoids relying on an unverified control lookup
    (title/icon can vary) for something the app's standard keyboard
    shortcut handles directly. The caller is responsible for making the
    intended editor active first (Editor.switch_to()).
    """
    main_win.type_keys("^s", pause=0.05)
    print("OK: sent Ctrl+S.")


def close_current_tab(main_win) -> None:
    """Close the active editor tab via Ctrl+W. Same reasoning as save_current_editor()."""
    main_win.type_keys("^w", pause=0.05)
    print("OK: sent Ctrl+W.")


def list_open_tabs(scope) -> list[str]:
    """
    Print and return the titles of every open Tab control under `scope` —
    useful when a title_re guess for a newly-opened editor tab doesn't
    match, so we can see the real title instead of guessing again.
    """
    titles = [t.window_text() for t in scope.descendants(control_type="Tab")]
    print(f"Open tabs: {titles}")
    return titles
