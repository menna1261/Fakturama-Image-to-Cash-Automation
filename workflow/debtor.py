"""
Step 4 (design doc 2.x): resolve the Order's Debtor.

Search the existing contacts for an exact match; select it if there's
exactly one, otherwise create the contact from the extracted data, save
it, and prove the save worked by re-running the same search.
"""

import time

from utils.automation.editors import close_current_tab, open_editor, save_current_editor
from utils.automation.clipboard_grid import row_text_matches, select_row, walk_rows
from utils.automation.field_specs import (
    MAIN_WINDOW_FIELDS,
    NEW_DEBTOR_FIELDS,
    NEW_ORDER_FIELDS,
    SELECT_ADDRESS_DIALOG_FIELDS,
    SELECT_ADDRESS_GRID,
)
from utils.automation.resolver import (
    FieldNotFoundError,
    fill_fields,
    find_controls_by_text,
    find_field,
    print_fill_summary,
    search,
    type_into,
)
from utils.automation.windows import find_hwnd, find_hwnds, wait_until_responsive
from utils.extraction.schema import Debtor
from workflow.context import Context, ManualReviewRequired
from workflow.open_order import ORDER_EDITOR
from workflow.outcomes import Outcome
from workflow.payment_method import ensure_term_of_payment_exists, try_select_payment_method

DEBTOR_EDITOR = "debtor"
_DEBTOR_TAB_RE = ".*New Debtor.*"
_ADDRESS_DIALOG_TITLE = "Select the address"

# A newly created payment method only shows up in the Debtor's Payment
# combo after the Debtor itself has been saved; give the combo a moment
# to refresh before reading it back.
_COMBO_REFRESH_SECONDS = 2
# Let the Order editor repaint after a Debtor is selected into it.
_ADDRESS_POPULATE_SECONDS = 1


def resolve_debtor(ctx: Context) -> Outcome:
    """
    Entry point. Returns FOUND if an existing contact matched, CREATED if
    we had to make one. Raises ManualReviewRequired on ambiguity, or if a
    contact we just saved can't be found again.
    """
    if _select_matching_debtor(ctx):
        return Outcome.FOUND

    _create_debtor(ctx)
    _verify_saved_debtor(ctx)
    return Outcome.CREATED


def expected_debtor_fields(debtor: Debtor) -> dict[str, str]:
    """The fields that must all match for a result row to count as this Debtor (2.3)."""
    return {
        "company": debtor.company,
        "first_name": debtor.contact_first_name,
        "last_name": debtor.contact_last_name,
        "zip": debtor.billing_address.zip,
        "city": debtor.billing_address.city,
    }


def _open_address_dialog(ctx: Context):
    """
    Step 2.1: click the upper existing-contact icon beside Addresses (not
    the green + icon) and wait for "Select the address" to open.

    Returns both the pywinauto window (for the search box and OK/Cancel,
    which ARE UIA-visible) and the raw handle (for the clipboard walk
    over the grid, which isn't).
    """
    icon = find_field(ctx.editor(ORDER_EDITOR).pane, NEW_ORDER_FIELDS["select_debtor_icon"])

    # Snapshot the dialogs of this name that already exist, so the lookup
    # below waits for a NEW one. This dialog is opened twice on the
    # creation path (2.2, then 2.12's re-search) and both instances share
    # a title, so a plain lookup can return the previous one before it's
    # destroyed — see the same guard in product.py, where that produced an
    # "Invalid window handle" crash mid-walk.
    existing = find_hwnds(pid=ctx.pid, title=_ADDRESS_DIALOG_TITLE)
    print("Clicking select_debtor_icon...")
    icon.click_input()

    # Win32 handle first, then UIA — a UIA wait on a freshly-opened
    # window can hang past its own timeout while the window is settling.
    dialog_hwnd = find_hwnd(
        pid=ctx.pid, title=_ADDRESS_DIALOG_TITLE, timeout=15, exclude=existing
    )
    wait_until_responsive(dialog_hwnd, timeout=15)
    print(f"SUCCESS: {_ADDRESS_DIALOG_TITLE!r} dialog opened.")
    return ctx.app.window(handle=dialog_hwnd), dialog_hwnd


def _select_matching_debtor(ctx: Context) -> bool:
    """
    Steps 2.2-2.3: search the address selector by Company and select the
    single exact match, if there is one. Returns True when a Debtor was
    selected, False when the dialog was cancelled with no match.

    Searching by Company (not contact name) is deliberate: this search
    box appears to filter on Company/Alias only, so a name query finds
    nothing even for a contact that definitely exists.

    The grid itself is read by clipboard walk, not UIA: its rows have no
    UIA representation at all on this Fakturama build (see
    clipboard_grid.py). The whole grid is read before anything is
    chosen, so "two contacts match" is distinguishable from "one does" —
    which the design doc requires, and which stopping at the first match
    would not give.
    """
    debtor = ctx.debtor
    expected = expected_debtor_fields(debtor)
    dialog, dialog_hwnd = _open_address_dialog(ctx)
    print(f"Expected extracted debtor fields to match against: {expected}")

    search(dialog, debtor.company)

    print("Reading the results grid (clipboard walk — its rows aren't UIA-visible):")
    rows = walk_rows(dialog_hwnd, SELECT_ADDRESS_GRID)
    print(f"Read {len(rows)} row(s).")

    matches = [(i, text) for i, text in enumerate(rows) if row_text_matches(text, expected)]

    if len(matches) > 1:
        _click_dialog_button(dialog, "cancel_button")
        raise ManualReviewRequired(
            f"{len(matches)} contacts match {debtor.company!r} exactly (rows "
            f"{[i + 1 for i, _ in matches]}) — ambiguous, not proceeding automatically."
        )

    if not matches:
        if rows:
            print("No exact match among the rows read — clicking Cancel.")
        else:
            print(
                f"The results grid is empty — no contact matches {debtor.company!r} "
                f"yet. Clicking Cancel and creating one."
            )
        _click_dialog_button(dialog, "cancel_button")
        return False

    row_index, row_text = matches[0]
    print(f"Exactly one exact match (row {row_index + 1}) — selecting it.")
    if not select_row(dialog_hwnd, SELECT_ADDRESS_GRID, row_index, row_text):
        _click_dialog_button(dialog, "cancel_button")
        raise ManualReviewRequired(
            f"Found the Debtor {debtor.company!r} at row {row_index + 1} but could not "
            f"confirm it was selected, so OK was not clicked — see the geometry "
            f"mismatch logged above."
        )

    _click_dialog_button(dialog, "ok_button")
    return True


def _click_dialog_button(dialog, spec_key: str) -> None:
    """OK/Cancel are UIA-visible on this dialog even though its rows aren't."""
    find_field(dialog, SELECT_ADDRESS_DIALOG_FIELDS[spec_key]).click_input()


def _create_debtor(ctx: Context) -> None:
    """
    Steps 2.5-2.11: open New Contact, fill it, assign its payment method,
    then save and close it and return to the Order.
    """
    # "New Contact" lives in the main window's left navigator panel, not
    # inside the New Order editor tab.
    link = find_field(ctx.main_win, MAIN_WINDOW_FIELDS["new_contact_link"])
    print("Clicking New Contact...")
    link.click_input()

    editor = open_editor(ctx.main_win, _DEBTOR_TAB_RE, DEBTOR_EDITOR)
    ctx.editors[DEBTOR_EDITOR] = editor
    ctx.maybe_dump(editor.pane)

    print_fill_summary(_fill_main_address(editor.pane, ctx.debtor))

    _click_misc_tab(editor.pane)
    print_fill_summary(_fill_misc(editor.pane, ctx.debtor))

    _assign_payment_method(ctx)

    editor.switch_to()
    save_current_editor(ctx.main_win)
    close_current_tab(ctx.main_win)
    ctx.editors.pop(DEBTOR_EDITOR, None)
    ctx.editor(ORDER_EDITOR).switch_to()


def _fill_main_address(scope, debtor: Debtor) -> dict[str, bool]:
    """
    Steps 2.6-2.7: Company, First/Last Name and the Main address fields.
    Customer ID and Salutation are deliberately skipped (see the
    NEW_DEBTOR_FIELDS comment). Step 2.8 (assigning the Invoice/Delivery
    address role) isn't implemented yet — needs the address-type control
    inspected first.
    """
    results = fill_fields(
        scope,
        NEW_DEBTOR_FIELDS,
        {
            "company": debtor.company,
            "street": debtor.billing_address.street,
            "country": debtor.billing_address.country,
            "email": debtor.email,
            "telephone": debtor.phone,
        },
    )

    # First/Last Name and ZIP/City are each a single Pane wrapping two
    # Edit controls — resolve the Pane, then set its two Edit children by
    # position, same composite-widget pattern as the Date field.
    dual_edits = [
        ("first_last_name_pane", debtor.contact_first_name, debtor.contact_last_name),
        ("zip_city_pane", debtor.billing_address.zip, debtor.billing_address.city),
    ]
    for spec_key, first_value, second_value in dual_edits:
        try:
            pane = find_field(scope, NEW_DEBTOR_FIELDS[spec_key])
            edits = pane.children(control_type="Edit")
            if len(edits) < 2:
                raise FieldNotFoundError(
                    f"{spec_key}: expected 2 Edit children in the pane, found {len(edits)}"
                )
            for edit, value in zip(edits, (first_value, second_value)):
                type_into(edit, value)
            print(f"OK: {spec_key!r} set to ({first_value!r}, {second_value!r})")
            results[spec_key] = True
        except FieldNotFoundError as e:
            print(f"FAILED: {spec_key!r} — {e}")
            results[spec_key] = False

    return results


def _click_misc_tab(scope) -> None:
    print("Clicking Miscellaneous tab...")
    find_field(scope, NEW_DEBTOR_FIELDS["misc_tab"]).click_input()


def _fill_misc(scope, debtor: Debtor) -> dict[str, bool]:
    """Step 2.9: Alias name, Discount = 0%, Net or Gross = Net."""
    return fill_fields(
        scope,
        NEW_DEBTOR_FIELDS,
        {
            "alias_name": debtor.alias,
            # Bare "0", not "0%": Discount is a percent-formatted field
            # that adds its own suffix. This used to read "0%" and still
            # sent "0", because send_keys treats '%' as Alt and dropped
            # it; now that type_into() escapes properly, spelling it "0%"
            # would type a literal '%' into a numeric field for the first
            # time. Keep sending what actually worked.
            "discount": "0",
            "net_or_gross": "Net",
        },
    )


def _assign_payment_method(ctx: Context) -> None:
    """
    Step 2.10: set the Debtor's Payment Method, creating the term of
    payment first if the combo doesn't offer it yet.
    """
    editor = ctx.editor(DEBTOR_EDITOR)
    method = ctx.payment_method

    if try_select_payment_method(editor.pane, method):
        return

    outcome = ensure_term_of_payment_exists(ctx, method)
    print(f"terms of payment outcome: {outcome.name}")

    # Come back to the Debtor before touching its combo again: controls
    # in a non-active tab don't report as visible to UIA, so this must
    # happen on BOTH paths (found-existing as well as just-created).
    editor.switch_to()

    if outcome is Outcome.CREATED:
        # A brand-new payment method only appears in this combo once the
        # Debtor itself has been saved.
        save_current_editor(ctx.main_win)
        time.sleep(_COMBO_REFRESH_SECONDS)

    # Switching tabs resets the Debtor editor's own sub-tab back to
    # Addresses, and Payment lives on Miscellaneous.
    _click_misc_tab(editor.pane)

    if not try_select_payment_method(editor.pane, method):
        raise ManualReviewRequired(
            f"Payment Method {method!r} is still unavailable on the Debtor after "
            f"resolving it in the terms-of-payment registry ({outcome.name})."
        )


def _verify_saved_debtor(ctx: Context) -> None:
    """
    Steps 2.12-2.13: reopen the address selector and confirm the Debtor we
    just saved now resolves as an exact match (proof the save persisted),
    then confirm its address actually populated onto the Order.
    """
    if not _select_matching_debtor(ctx):
        raise ManualReviewRequired(
            "The Debtor we just saved did not come back as an exact match on "
            "re-search, so it can't be attached to the Order — either the save "
            "didn't persist, or the saved fields differ from what was extracted. "
            "The rows the grid actually returned are logged above."
        )

    # 2.13: a lightweight check (the debtor's name now appears somewhere
    # in the editor) rather than pinning down the exact Invoice/Delivery
    # address control's structure, which hasn't been inspected yet.
    time.sleep(_ADDRESS_POPULATE_SECONDS)
    matches = find_controls_by_text(ctx.editor(ORDER_EDITOR).pane, ctx.debtor.company)
    if matches:
        print(f"OK (2.13): Debtor address appears populated on the Order ({len(matches)} match(es)).")
    else:
        print("FAILED (2.13): could not confirm Debtor address populated — name not found on Order.")
