"""
Resolves a FieldSpec against a live pywinauto scope (a window or a
container control) into a ready-to-use control, dispatching on the
spec's Strategy.
"""

import logging
import time

from pywinauto.findwindows import ElementAmbiguousError, ElementNotFoundError
from pywinauto.timings import TimeoutError as PWATimeoutError
from pywinauto.timings import wait_until

from utils.automation.locators import FieldSpec, Strategy

logger = logging.getLogger("automation.resolver")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger.setLevel(logging.INFO)

_LOOKUP_TIMEOUT = 10
_LOOKUP_ERRORS = (ElementNotFoundError, ElementAmbiguousError, PWATimeoutError)

# Pause between synthetic keystrokes. Typing into a live-filtered search
# box gets the slower of the two, so the filter's keystroke listener
# keeps up with us.
_KEY_PAUSE = 0.02
_SEARCH_KEY_PAUSE = 0.03
# How long to let a live-filtered result grid repopulate after typing.
_SEARCH_SETTLE_SECONDS = 1.0


class FieldNotFoundError(Exception):
    """Raised when a FieldSpec could not be resolved to a live control."""


class AmbiguousOptionError(Exception):
    """
    Raised when a dropdown offers the wanted option more than once.

    Deliberately NOT a subclass of FieldNotFoundError. Callers treat "the
    option isn't there" as a routine outcome and go create the missing
    record; treating "there are two of them" the same way would create a
    third. This has to reach the top of the run instead.
    """


def find_field(scope, spec: FieldSpec):
    """
    Resolve `spec` against `scope` (a pywinauto window/control) and
    return the matched, ready (visible+enabled) control.

    Raises FieldNotFoundError on any failure, with the field's name and
    the strategy that failed folded into the message — callers should
    never need to interpret a raw pywinauto exception.
    """
    start = time.monotonic()
    logger.info("Resolving field %r via %s...", spec.name, spec.strategy.name)

    try:
        if spec.strategy is Strategy.BY_TITLE:
            control = _find_by_title(scope, spec)
        elif spec.strategy is Strategy.SIBLING_OF_LABEL:
            control = _find_sibling_of_label(scope, spec)
        elif spec.strategy is Strategy.VISION_FALLBACK:
            control = _find_via_vision_fallback(scope, spec)
        else:
            raise FieldNotFoundError(
                f"Field {spec.name!r}: unknown strategy {spec.strategy!r}"
            )
    except FieldNotFoundError:
        raise
    except _LOOKUP_ERRORS as e:
        elapsed = time.monotonic() - start
        logger.info(
            "Field %r via %s FAILED after %.2fs: %s",
            spec.name, spec.strategy.name, elapsed, e,
        )
        raise FieldNotFoundError(
            f"Field {spec.name!r}: {spec.strategy.name} lookup failed — {e}"
        ) from e

    elapsed = time.monotonic() - start
    logger.info("Field %r via %s resolved in %.2fs", spec.name, spec.strategy.name, elapsed)
    return control


def _find_by_title(scope, spec: FieldSpec):
    if spec.title is None:
        raise FieldNotFoundError(f"Field {spec.name!r}: BY_TITLE requires `title`")

    # Use .descendants() + manual title filtering rather than
    # .child_window(): the latter only exists on a lazy
    # WindowSpecification, but `scope` here can also be an
    # already-resolved wrapper (e.g. an editor tab kept alive across an
    # Eclipse tab-title rename after save) — .descendants() works on
    # both, since WindowSpecification proxies it to its resolved wrapper.
    if spec.control_type:
        candidates = scope.descendants(control_type=spec.control_type)
    else:
        candidates = scope.descendants()
    matches = [c for c in candidates if c.window_text() == spec.title]

    if not matches:
        raise FieldNotFoundError(
            f"Field {spec.name!r}: no {spec.control_type or 'control'} "
            f"titled {spec.title!r} found"
        )
    if len(matches) > 1:
        raise FieldNotFoundError(
            f"Field {spec.name!r}: {len(matches)} ambiguous matches for "
            f"{spec.control_type or 'control'} titled {spec.title!r}"
        )

    control = matches[0]
    wait_until(
        _LOOKUP_TIMEOUT, 0.5,
        lambda: control.is_visible() and control.is_enabled(),
    )
    return control


def _find_sibling_of_label(scope, spec: FieldSpec):
    if spec.label_text is None:
        raise FieldNotFoundError(
            f"Field {spec.name!r}: SIBLING_OF_LABEL requires `label_text`"
        )
    # .descendants(), not .child_window(): `scope` can be an
    # already-resolved wrapper (not just a lazy WindowSpecification),
    # which has no .child_window(). See _find_by_title() for the same
    # reasoning.
    label_matches = [
        c for c in scope.descendants(control_type="Text")
        if c.window_text() == spec.label_text
    ]
    if not label_matches:
        raise FieldNotFoundError(
            f"Field {spec.name!r}: no Text labeled {spec.label_text!r} found"
        )
    label = label_matches[0]
    wait_until(_LOOKUP_TIMEOUT, 0.5, lambda: label.is_visible())

    # label.parent() returns an already-resolved UIAWrapper, not a lazy
    # WindowSpecification, so it has no .child_window() — use .children()
    # to enumerate directly.
    parent = label.parent()
    siblings = parent.children()

    # A parent pane can hold several label+field pairs (e.g. "No." and
    # "Date" side by side) — naively taking the first control_type match
    # anywhere in the parent picks up the WRONG field (e.g. filled "No."
    # instead of "Date"). Instead, find our label's own position in the
    # sibling list and take the first match that comes AFTER it — this
    # matches how form layouts lay out children in visual/tab order
    # (label, then its own field, then the next label, ...).
    label_runtime_id = label.element_info.runtime_id
    label_index = next(
        (i for i, c in enumerate(siblings) if c.element_info.runtime_id == label_runtime_id),
        None,
    )
    if label_index is None:
        raise FieldNotFoundError(
            f"Field {spec.name!r}: label {spec.label_text!r} not found among its own parent's children"
        )

    control = next(
        (c for c in siblings[label_index + 1:] if c.element_info.control_type == spec.control_type),
        None,
    )
    if control is None:
        # Self-diagnosing failure: log what's actually sitting near the
        # label instead of just "not found", so a wrong assumption about
        # control_type (e.g. a native date picker isn't a plain "Edit")
        # is visible immediately instead of requiring a fresh tree dump.
        nearby = [
            f"{c.element_info.control_type}(title={c.window_text()!r})"
            for c in siblings[max(0, label_index - 1):label_index + 4]
        ]
        logger.info("Siblings near label %r: %s", spec.label_text, nearby)
        raise FieldNotFoundError(
            f"Field {spec.name!r}: no {spec.control_type} found after label "
            f"{spec.label_text!r} in tree order. Nearby siblings: {nearby}"
        )

    wait_until(
        _LOOKUP_TIMEOUT, 0.5,
        lambda: control.is_visible() and control.is_enabled(),
    )
    return control


def _find_via_vision_fallback(scope, spec: FieldSpec):
    raise NotImplementedError(
        f"Vision fallback not yet implemented — needed for: {spec.vision_description}"
    )


# Characters pywinauto's send_keys()/type_keys() reads as SYNTAX rather
# than text: '+', '^' and '%' are the Shift/Ctrl/Alt modifiers, '~' is an
# alias for Enter, '(' and ')' group keystrokes under a modifier, and
# '{}' delimit key codes. Any of them appearing in real extracted data is
# silently swallowed or turned into a keypress. Escaping is by wrapping
# in braces, which is pywinauto's own documented mechanism:
# send_keys('{^}a{%}') types the literal text "^a%".
_TYPE_KEYS_RESERVED = frozenset("^+%~(){}")


def escape_for_type_keys(text: str) -> str:
    """
    Make `text` type as itself.

    This is not a theoretical concern — it corrupted real data from the
    sample order. "VAT 19%" typed as "VAT 19" followed by a bare Alt
    press, so the tax rate was created under the wrong name and the
    Product editor's dropdown then offered "VAT 19" while the run looked
    for "VAT 19%". The extracted phone number "+49 30 5550 1420" is worse:
    a leading '+' is Shift, so it typed Shift+4, Shift+9 — "$(" — into
    the Debtor. Anything carrying a '+', '%' or bracket (emails, company
    names, percentages) is affected.
    """
    return "".join(f"{{{ch}}}" if ch in _TYPE_KEYS_RESERVED else ch for ch in text)


def type_into(control, value: str, *, tab: bool = True, pause: float = _KEY_PAUSE) -> None:
    """
    Click `control`, select-all + delete whatever's in it, type `value`
    as real synthetic keystrokes, and optionally Tab out to commit.

    The single primitive behind every "put text in a box" in this
    codebase — plain fields, composite date pickers, the two-Edit
    First/Last Name and ZIP/City panes, and search boxes (which pass
    tab=False, since tabbing out of a live-filter box isn't wanted).

    Never uses .set_edit_text(). See set_field_value() for why.
    """
    control.click_input()
    # The select-all and the Tab below are deliberately NOT escaped —
    # there the modifier syntax is the point. Only `value` is user data.
    control.type_keys("^a{DELETE}", pause=pause)
    control.type_keys(escape_for_type_keys(value), with_spaces=True, pause=pause)
    if tab:
        control.type_keys("{TAB}", pause=pause)


def set_field_value(control, value: str) -> None:
    """
    Set `value` into `control` using real synthetic keystrokes (click,
    select-all, type, Tab) — never .set_edit_text().

    .set_edit_text() uses UIA's ValuePattern.SetValue, which overwrites
    the control's text property directly without firing the key/focus
    events this app's widgets listen for to actually commit a value into
    their data model. This was first found on the Date field (a
    composite date-picker: the typed text was visible only until the
    widget's own validation ran on focus-out, then silently reverted to
    its real internal value) and assumed to be specific to that
    composite-widget case — but it turned out to affect plain top-level
    Edit controls too: the Debtor's Company field looked filled during
    the session, then simply wasn't there after Save (confirmed via the
    2.12 re-verification step finding no exact match without it).
    Keystrokes mimic an actual user and let each widget's own commit
    logic run, regardless of whether the target is a bare Edit or a
    composite container wrapping one.
    """
    # Composite containers (e.g. the Date picker) have no Edit of their
    # own — drill into the first Edit descendant. A plain Edit is its
    # own descendant-free target, so this just uses `control` itself.
    edit_descendants = control.descendants(control_type="Edit")
    target = edit_descendants[0] if edit_descendants else control
    type_into(target, value)


def fill_fields(scope, specs: dict, values: dict[str, str]) -> dict[str, bool]:
    """
    For each key in `values`, resolve the matching FieldSpec in `specs`
    against `scope` and set it — via select_combo_option() for combos,
    set_field_value() otherwise.

    Returns a {field_name: success} map and never raises: failures are
    recorded, not propagated, so one bad field doesn't abort the rest of
    the form.
    """
    results: dict[str, bool] = {}
    for field_name, value in values.items():
        spec = specs[field_name]
        try:
            control = find_field(scope, spec)
            if spec.is_combo:
                select_combo_option(control, value)
            else:
                set_field_value(control, value)
            print(f"OK: {field_name!r} set to {value!r}")
            results[field_name] = True
        except (FieldNotFoundError, NotImplementedError) as e:
            print(f"FAILED: {field_name!r} — {e}")
            results[field_name] = False
    return results


def print_fill_summary(results: dict[str, bool]) -> None:
    print("=" * 80)
    print("FIELD FILL SUMMARY:")
    for field_name, ok in results.items():
        print(f"  [{'OK' if ok else 'FAILED'}] {field_name}")
    print("=" * 80)


def search(scope, query: str, *, container_spec=None, settle: float = _SEARCH_SETTLE_SECONDS) -> None:
    """
    Type `query` into a screen's search box and wait for its live filter
    to repopulate the results grid.

    `container_spec`, when given, is a FieldSpec for a Pane wrapping the
    search Edit (the terms-of-payment screen nests it one level deeper
    than a direct sibling of its "Search:" label); without it the first
    Edit descendant of `scope` is used (the "Select the address" dialog).

    Explicitly clicks the box rather than assuming it already has
    keyboard focus: that assumption held the FIRST time a dialog opens in
    a run, but not on a second open (confirmed via a read-back showing
    the search box stayed empty after blindly sending keystrokes to the
    dialog). Real keystrokes, not a raw value-set, because the live
    filter listens for key events.
    """
    container = find_field(scope, container_spec) if container_spec is not None else scope
    edits = container.descendants(control_type="Edit")
    if not edits:
        raise FieldNotFoundError(f"No Edit control found to search with in {container}")

    box = edits[0]
    type_into(box, query, tab=False, pause=_SEARCH_KEY_PAUSE)
    print(f"Typed into search box: {query!r} (box now reads: {box.window_text()!r})")
    time.sleep(settle)


def dump_labels_and_fields(
    scope,
    control_types=("Text", "Edit", "ComboBox", "Pane", "Button", "Image", "TabItem"),
) -> None:
    """
    Print every descendant of `scope` matching `control_types`, in tree
    order, with its control_type, title, and screen rectangle.

    Diagnostic helper for figuring out real field titles/positions
    (SWT Combo widgets, for instance, don't reliably expose the title we
    might guess) without wading through a full print_control_identifiers
    dump of the whole window.

    "Image" and "TabItem" are in the default list because leaving them out
    actively misleads: the New Order dump showed the "Addresses" and
    "Items" labels with apparently nothing after them, when in fact each
    is followed by the two icons those steps click (the select-existing
    icon and the green +). A dump that silently omits the controls a
    FieldSpec targets is worse than no dump.
    """
    print("-" * 80)
    print(f"LABELS/FIELDS in {scope}:")
    for ctrl in scope.descendants():
        ct = ctrl.element_info.control_type
        if ct not in control_types:
            continue
        title = ctrl.window_text()
        try:
            rect = ctrl.rectangle()
        except Exception:
            rect = None
        print(f"  {ct:10s} title={title!r:30s} rect={rect}")
    print("-" * 80)


def find_controls_by_text(scope, substring: str):
    """
    Search ALL descendants of `scope`, of any control_type, for one
    whose title contains `substring` (case-insensitive). Prints each
    match's control_type/title/rect and returns the list of matches.

    Use this instead of dump_labels_and_fields() when you know the
    visible label (e.g. "New Contact") but not which control_type it's
    rendered as — avoids guessing a type list up front.
    """
    needle = substring.casefold()
    matches = []
    print("-" * 80)
    print(f"CONTROLS matching {substring!r} in {scope}:")
    for ctrl in scope.descendants():
        title = ctrl.window_text()
        if needle in title.casefold():
            matches.append(ctrl)
            try:
                rect = ctrl.rectangle()
            except Exception:
                rect = None
            print(f"  {ctrl.element_info.control_type:10s} title={title!r:30s} rect={rect}")
    print(f"({len(matches)} match(es))")
    print("-" * 80)
    return matches


def select_combo_option(control, option_text: str) -> None:
    """
    Select `option_text` in a ComboBox control.

    Counting and choosing are two separate jobs here, in that order.
    First open the dropdown and count how many options carry this name;
    only then select one:

      several    -> AmbiguousOptionError, having selected nothing
      one/none   -> .select(), and if that fails, click the item
      neither    -> FieldNotFoundError

    Both halves are searched from the top-level window rather than from
    the combo's own subtree, because some SWT combos render their popup
    as a separate element that isn't nested under the combo — confirmed
    on the New Term of Payment editor's payment-code combo, where
    "Credit card" was visibly in the open dropdown and
    .select("Credit card") still reported "item not found".

    The combo itself is deliberately never clicked — see the comment
    below the docstring.
    """
    # DON'T click the combo first. That was tried — the idea being to
    # give it real focus rather than driving it purely through UIA
    # patterns — and it is redundant: .expand() opens the dropdown
    # through the ExpandCollapse pattern without needing a mouse.
    # Measured against the live app, adding the click cost an extra
    # click per selection and was visibly slower.

    # Open the list and read it BEFORE selecting anything. Selecting
    # first would make a duplicate option undetectable: .select() and a
    # first-match search both just take one and move on, and "there are
    # two entries with this name" is a case the design doc reserves for
    # a human (2.10.2). Counting first is the only way to see it.
    items, matches = _open_and_find_options(control, option_text)

    if len(matches) > 1:
        available = [item.window_text() for item in items]
        _collapse_quietly(control)
        raise AmbiguousOptionError(
            f"The dropdown offers {len(matches)} entries named {option_text!r} — "
            f"ambiguous, so nothing was selected. Full list: {available}"
        )

    # Counting is done; now actually select, and prefer the built-in for
    # it. .select() goes through the selection pattern, which reaches an
    # item regardless of whether the popup has it scrolled into view.
    # Clicking cannot: a long list (Country runs to ~200 entries) draws
    # only a dozen or so rows, and UIA still reports the rest as
    # ListItems whose rectangles are off-screen — so a click lands on
    # whatever is at those coordinates and picks the wrong option. That
    # is a silent wrong value, not a visible failure, so the click is
    # strictly the fallback.
    try:
        control.select(option_text)
        # Returning quietly is NOT proof the value changed. With the
        # popup already open, .select()'s internal select can highlight
        # the item through the selection pattern, and then its own
        # `finally: collapse()` throws that highlight away — no
        # exception raised, combo still on its default. Seen on the New
        # Term of Payment editor's payment-code combo, which reported
        # success while keeping the default. Same species as the
        # .set_edit_text() persistence trap: verify, never trust.
        if _reads_as(control, option_text):
            return
        reason = f".select() returned but the combo reads {_combo_value(control)!r}"
    except Exception as e:
        reason = f".select() failed ({e})"

    logger.info("Combo %r: %s — clicking the item instead", option_text, reason)

    # The fallback the docstring promises, which this function used to
    # only log and never actually perform: it caught .select()'s failure,
    # wrote a line about clicking the item, and then returned None.
    # fill_fields() takes a clean return as success, so a combo that was
    # never set was reported "OK". That is how a Product got saved with
    # Fakturama's default Tax-free rate while the log said
    # "OK: 'vat' set to 'VAT 19%'" — the exact silently-wrong outcome the
    # rest of this module exists to prevent.
    if not matches:
        available = [item.window_text() for item in items]
        _collapse_quietly(control)
        raise FieldNotFoundError(
            f"The dropdown has no entry named {option_text!r}, and .select() "
            f"could not set it either ({reason}). Options actually on offer: "
            f"{available}"
        )

    # .select() collapses the popup in its own `finally` — including on
    # the path where it changed nothing — so the items found earlier are
    # stale. Re-open and re-find before clicking one.
    _, fresh = _open_and_find_options(control, option_text)
    if not fresh:
        _collapse_quietly(control)
        raise FieldNotFoundError(
            f"{option_text!r} was in the dropdown when it was first read but not "
            f"after re-opening it, so it could not be clicked ({reason})."
        )

    fresh[0].click_input()

    # And verify the click too, for the same reason: this function must
    # either set the value or raise. Anything in between gets reported
    # as "OK" by fill_fields() and shows up later as a saved record with
    # the wrong value in it.
    if not _reads_as(control, option_text):
        raise FieldNotFoundError(
            f"Clicked the {option_text!r} entry, but the combo still reads "
            f"{_combo_value(control)!r} — the value did not take."
        )


def _combo_value(control) -> str | None:
    """
    What the combo currently displays, or None if it can't be read.

    selected_text() is the proper question, but it goes through the
    selection pattern and some combos answer it with None; window_text()
    is the cruder fallback.
    """
    for read in (getattr(control, "selected_text", None), getattr(control, "window_text", None)):
        if read is None:
            continue
        try:
            value = read()
        except Exception:
            continue
        if value:
            return value
    return None


def _reads_as(control, option_text: str) -> bool:
    """
    Whether the combo now shows `option_text`.

    A combo whose value can't be read at all counts as a match: the
    alternative is forcing a click that, on a long list, could land on
    the wrong entry — a wrong value is worse than an unverified one.
    The uncertainty is logged rather than hidden.
    """
    value = _combo_value(control)
    if value is None:
        logger.info("Combo: value is not readable — cannot verify %r took", option_text)
        return True
    return value.strip().casefold() == option_text.strip().casefold()


def _open_and_find_options(control, option_text: str) -> tuple[list, list]:
    """
    Open a dropdown and return (every option, the ones named `option_text`).

    The single lookup behind both jobs select_combo_option() has to do:
    counting how many options carry the name, and getting hold of the one
    to click when the selection pattern won't do it. Both need the same
    list, so both come from here.

    Options are gathered from the top-level window rather than from the
    combo's own subtree, because some SWT combos render their popup as a
    separate element that isn't nested under the combo at all.
    """
    try:
        control.expand()
    except Exception as e:
        logger.info("Combo: .expand() failed (%s) — reading the list anyway", e)

    needle = option_text.strip().casefold()
    items = control.top_level_parent().descendants(control_type="ListItem")
    matches = [item for item in items if item.window_text().strip().casefold() == needle]
    return items, matches


def _collapse_quietly(control) -> None:
    """Close a dropdown we're abandoning, so it doesn't cover the next screen."""
    try:
        control.collapse()
    except Exception:
        pass
