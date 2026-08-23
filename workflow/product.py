"""
Step 5 (design doc 3.x): resolve every line item's Product.

Runs the same select-or-create branch as debtor.py, once per extracted
item and in source order: search the Order's own product selector for
the exact SKU, and when it isn't there, make sure the item's VAT rate
exists, create the Product against it, save, and prove the save worked by
re-running the same search.

Completing the item lines themselves (3.13-3.16) is a separate problem
with a separate blocker — see complete_line_items() at the bottom.
"""

from utils.automation.clipboard_grid import select_row, walk_rows
from utils.automation.editors import (
    Editor,
    close_current_tab,
    open_editor,
    save_current_editor,
)
from utils.automation.field_specs import (
    MAIN_WINDOW_FIELDS,
    NEW_ORDER_FIELDS,
    NEW_PRODUCT_FIELDS,
    PRODUCTS_LIST_FIELDS,
    SELECT_ADDRESS_DIALOG_FIELDS,
    SELECT_PRODUCT_GRID,
)
from utils.automation.resolver import fill_fields, find_field, print_fill_summary, search
from utils.automation.windows import find_hwnd, find_hwnds, is_window, wait_until_responsive
from utils.extraction.schema import LineItem
from workflow.context import Context, ManualReviewRequired
# Steps 3.13-3.16 live in their own module: the Items table is a NatTable
# with no UIA cells at all, so it needs an entirely different mechanism
# (keyboard navigation plus clipboard read-back) from every
# FieldSpec-driven screen here.
from workflow.line_items import complete_line_items
from workflow.open_order import ORDER_EDITOR
from workflow.outcomes import Outcome
from workflow.vat import create_vat, format_percent, try_select_vat, vat_name

PRODUCTS_LIST_EDITOR = "products"
PRODUCT_EDITOR = "product"
_PRODUCTS_LIST_TAB_RE = ".*Products.*"
_PRODUCT_TAB_RE = ".*New [Pp]roduct.*"
_PRODUCT_DIALOG_TITLE = "Select a product"

# Design doc 3.10: fixed for every product this bot creates, whatever the
# image says — they aren't read off the order.
COST_PRICE = "0.00"
STOCK = "0.00"


def resolve_products(ctx: Context) -> list[Outcome]:
    """
    Entry point. Returns one Outcome per extracted item, in source order.

    Every item is selected or created FIRST, and the lines are completed
    afterwards, rather than finishing each line before moving to the next
    one. Both orders leave the same Order behind — the rows are added in
    source order either way — but this one gets all the master-data work
    done before complete_line_items() can stop the run, so a stop there
    doesn't leave half the Products uncreated as well.
    """
    items = ctx.extraction.items
    if not items:
        raise ManualReviewRequired(
            "The extraction produced no line items, so there is nothing to put "
            "on the Order. Re-run with --force-extract, or check the image."
        )

    outcomes = []
    for position, item in enumerate(items, start=1):
        print("=" * 80)
        print(f"Item {position}/{len(items)}: SKU {item.sku!r} ({item.description!r})")
        outcomes.append(_resolve_one(ctx, item))

    if ctx.fill_line_items:
        complete_line_items(ctx)
    else:
        print("=" * 80)
        print(
            "Line-item values NOT set (quantity, unit price, VAT, discount are at "
            "Fakturama's defaults).\nThat step drives the Order's NatTable by "
            "coordinate and is off by default; pass --fill-line-items to run it."
        )
    return outcomes


def _resolve_one(ctx: Context, item: LineItem) -> Outcome:
    """Design doc 3.2-3.12 for a single item row."""
    if _select_matching_product(ctx, item):
        return Outcome.FOUND

    _create_product(ctx, item)

    # 3.12: the re-search is the proof that the save persisted. Same
    # reasoning as the Debtor's 2.12 — if the Product we just wrote can't
    # be selected from the Order, it isn't on the Order, and the run must
    # not carry on as though it were.
    if not _select_matching_product(ctx, item):
        raise ManualReviewRequired(
            f"The Product {item.sku!r} we just saved did not come back on "
            f"re-search, so it can't be added to the Order — either the save "
            f"didn't persist, or the saved Item Number differs from the "
            f"extracted SKU. The rows the grid actually returned are logged "
            f"above."
        )
    return Outcome.CREATED


def _open_product_dialog(ctx: Context):
    """
    Step 3.2: click the upper product-selection icon beside the Items
    table (not the green + icon) and wait for "Select a product".

    Returns the pywinauto window and the raw handle for the same reason
    _open_address_dialog() does: the search box and OK/Cancel are
    UIA-visible, the results grid isn't.
    """
    icon = find_field(ctx.editor(ORDER_EDITOR).pane, NEW_ORDER_FIELDS["select_product_icon"])

    # Note which dialogs of this name already exist BEFORE clicking. This
    # dialog gets opened several times per run (once to look for the SKU,
    # again after creating the Product), and every instance carries the
    # same title — so a plain title lookup can hand back the previous
    # one, on its way out but not yet destroyed. Everything downstream
    # then drives a dead window: the search box swallows the query and
    # the first GetWindowRect fails with "Invalid window handle".
    existing = find_hwnds(pid=ctx.pid, title=_PRODUCT_DIALOG_TITLE)
    print("Clicking select_product_icon...")
    icon.click_input()

    # Win32 handle first, then UIA — a UIA wait on a freshly-opened
    # window can hang past its own timeout while the window is settling.
    dialog_hwnd = find_hwnd(
        pid=ctx.pid, title=_PRODUCT_DIALOG_TITLE, timeout=15, exclude=existing
    )
    wait_until_responsive(dialog_hwnd, timeout=15)
    print(f"SUCCESS: {_PRODUCT_DIALOG_TITLE!r} dialog opened.")
    return ctx.app.window(handle=dialog_hwnd), dialog_hwnd


def _select_matching_product(ctx: Context, item: LineItem) -> bool:
    """
    Steps 3.2-3.3: search the product selector by SKU and select the
    single exact match. Returns True when a Product was selected onto the
    Order, False when the dialog was cancelled with no match.

    The grid is read by clipboard walk for the same reason the address
    one is — this dialog is the same JFace AbstractSelectionDialog and
    exposes no rows to UIA. The whole grid is read before anything is
    picked, so "two products carry this SKU" stays distinguishable from
    "one does", which is what lets 3.3's conflict case be detected at all.
    """
    dialog, dialog_hwnd = _open_product_dialog(ctx)

    search(dialog, item.sku)

    if not is_window(dialog_hwnd):
        # Fakturama closed the dialog itself, having already put the
        # product on the Order. Its "immediately take over a clearly
        # found item number" preference does this whenever the search
        # text unambiguously identifies one product — confirmed as
        # DOCUMENT_IMMEDIATELY_OVERTAKE_ITEMNUMBER_FROM_PRODUCTS_DIALOG=true
        # in this install's com.sebulli.fakturama.rcp.prefs. That is
        # exactly 3.3's "one exact Product appears, select it and click
        # OK", carried out by the app instead of by us, so there is
        # nothing left to walk, match or confirm — and treating a missing
        # dialog as "no match" would create a duplicate of a product that
        # is already on the Order.
        #
        # Only the app's own judgement of "clearly found" is trusted
        # here, which is narrower than ours: it fires on an unambiguous
        # item-number hit, never on a partial or multi-row one. Those
        # still leave the dialog open and go through the walk below.
        print(
            f"Fakturama took {item.sku!r} over directly from the search box and "
            f"closed the dialog (its 'immediately take over a clearly found item "
            f"number' preference) — the product is on the Order."
        )
        return True

    print("Reading the results grid (clipboard walk — its rows aren't UIA-visible):")
    rows = walk_rows(dialog_hwnd, SELECT_PRODUCT_GRID)
    print(f"Read {len(rows)} row(s).")

    matches = [(i, text) for i, text in enumerate(rows) if _row_has_exact_sku(text, item.sku)]

    if len(matches) > 1:
        _click_dialog_button(dialog, "cancel_button")
        raise ManualReviewRequired(
            f"{len(matches)} products carry the Item No. {item.sku!r} exactly "
            f"(rows {[i + 1 for i, _ in matches]}) — ambiguous, not proceeding "
            f"automatically."
        )

    if not matches:
        if rows:
            print(f"No row carries the exact SKU {item.sku!r} — clicking Cancel.")
        else:
            print(
                f"The results grid is empty — no product matches {item.sku!r} "
                f"yet. Clicking Cancel and creating one."
            )
        _click_dialog_button(dialog, "cancel_button")
        return False

    row_index, row_text = matches[0]
    print(f"Exactly one exact match (row {row_index + 1}) — selecting it.")
    if not select_row(dialog_hwnd, SELECT_PRODUCT_GRID, row_index, row_text):
        _click_dialog_button(dialog, "cancel_button")
        raise ManualReviewRequired(
            f"Found the Product {item.sku!r} at row {row_index + 1} but could not "
            f"confirm it was selected, so OK was not clicked — see the geometry "
            f"mismatch logged above."
        )

    _click_dialog_button(dialog, "ok_button")
    return True


def _row_has_exact_sku(text: str, sku: str) -> bool:
    """
    True when one of the row's cells IS the SKU — not merely contains it.

    row_text_matches() (used for the Debtor) asks whether each expected
    value appears anywhere in the row's tab-joined blob, which is right
    for a Debtor: several fields have to agree at once, so a stray
    substring hit on one of them can't carry the match on its own. A
    Product is matched on the SKU alone, so a substring test would accept
    "CHR-ERG-01" against a row for "CHR-ERG-011" and put the wrong
    product on the Order. The clipboard hands rows back tab-separated, so
    per-cell equality is available here — use it.
    """
    cells = [cell.strip() for cell in text.split("\t")]
    matched = any(cell.casefold() == sku.strip().casefold() for cell in cells)
    print(f"    [{'OK' if matched else 'MISS'}] item number: expected {sku!r} in {cells}")
    return matched


def _click_dialog_button(dialog, spec_key: str) -> None:
    """
    OK/Cancel are UIA-visible on this dialog even though its rows aren't.
    The specs come from SELECT_ADDRESS_DIALOG_FIELDS because both
    selection dialogs are the same AbstractSelectionDialog — see the
    comment on that dict.
    """
    find_field(dialog, SELECT_ADDRESS_DIALOG_FIELDS[spec_key]).click_input()


def _create_product(ctx: Context, item: LineItem) -> None:
    """
    Steps 3.4-3.11: open New product, make sure the item's VAT rate is
    available on it, fill it, save and close it, and return to the Order.
    """
    editor = _open_new_product_editor(ctx)
    editor = _select_vat(ctx, editor, item)

    results = fill_fields(editor.pane, NEW_PRODUCT_FIELDS, build_product_values(item))
    print_fill_summary(results)

    if not all(results.values()):
        raise ManualReviewRequired(
            f"Not saving the new Product {item.sku!r} — these fields failed to "
            f"fill: {[field for field, ok in results.items() if not ok]}. Saving "
            f"it half-filled would put a wrong price or a missing tax rate onto "
            f"the Order, which the totals check (4.3) would then have to catch."
        )

    editor.switch_to()
    save_current_editor(ctx.main_win)
    close_current_tab(ctx.main_win)
    ctx.editors.pop(PRODUCT_EDITOR, None)
    ctx.editor(ORDER_EDITOR).switch_to()


def _open_new_product_editor(ctx: Context) -> Editor:
    """
    Design doc 3.7's "New product", reached through the Products list's
    green +, not the left navigator's New-product link. The link resolves
    fine and clicking it opens nothing — only the list toolbars' + carries
    the e4 model's `forcenew=true`. Same route as terms of payment and VATs.
    """
    list_editor = _open_products(ctx)

    create_btn = find_field(list_editor.pane, PRODUCTS_LIST_FIELDS["create_button"])
    print("Clicking the green + on the Products list...")
    create_btn.click_input()

    editor = open_editor(ctx.main_win, _PRODUCT_TAB_RE, PRODUCT_EDITOR)
    ctx.editors[PRODUCT_EDITOR] = editor
    ctx.maybe_dump(editor.pane)
    return editor


def _select_vat(ctx: Context, editor: Editor, item: LineItem) -> Editor:
    """
    Steps 3.4-3.7: get the item's VAT rate selected on the open Product
    editor, creating the rate first if the dropdown doesn't offer it.

    Returns the editor to carry on filling — which is NOT necessarily the
    one passed in. A rate created while the Product editor is open does
    not appear in that editor's dropdown (it reads the VAT list once, when
    it opens), so the editor is closed and reopened rather than refreshed.
    Closing discards nothing: this runs before any field is filled, which
    is the whole reason the VAT check comes first.

    That ordering is also what 3.7 asks for — "click New product only
    after the required VAT exists" — reached from the other side. The
    dropdown is the only readable answer to "does this rate exist?", so
    the editor has to be open to ask it.
    """
    if try_select_vat(editor.pane, item.vat_pct):
        return editor

    print(f"Closing the empty Product editor to create VAT {format_percent(item.vat_pct)}%...")
    close_current_tab(ctx.main_win)
    ctx.editors.pop(PRODUCT_EDITOR, None)

    create_vat(ctx, item.vat_pct)

    editor = _open_new_product_editor(ctx)
    if not try_select_vat(editor.pane, item.vat_pct):
        raise ManualReviewRequired(
            f"The VAT rate {vat_name(item.vat_pct)} is still not on offer in the "
            f"Product editor's dropdown after being created, so {item.sku!r} "
            f"cannot be given the right tax rate. Saving it anyway would put it "
            f"on the Order at Fakturama's default rate and quietly understate "
            f"the totals."
        )
    return editor


def _open_products(ctx: Context) -> Editor:
    """Open (or come back to) the Products list — same shape as _open_vats()."""
    link = find_field(ctx.main_win, MAIN_WINDOW_FIELDS["products_link"])
    print("Clicking Products...")
    link.click_input()

    editor = open_editor(ctx.main_win, _PRODUCTS_LIST_TAB_RE, PRODUCTS_LIST_EDITOR)
    ctx.editors[PRODUCTS_LIST_EDITOR] = editor
    return editor


def gross_price(item: LineItem) -> float:
    """
    Design doc 3.9: the Product master's gross price is the unit NET
    price plus its VAT, to two decimals.

    The line's own discount is deliberately not applied. It belongs to
    this transaction, not to the product — 3.15 puts it on the item line
    instead, and folding it in here would quietly re-price the product
    for every future order.
    """
    return round(item.unit_net_price * (1 + item.vat_pct / 100), 2)


def build_product_values(item: LineItem) -> dict[str, str]:
    """
    Map an extracted line item onto the New product field-fill values —
    same split as build_new_order_values(): what data we have, separately
    from how it gets typed in.

    Deliberately no "vat" entry: _select_vat() owns that field, because
    setting it is entangled with creating the rate and reopening the
    editor. One owner per field — filling it here as well would just
    re-open the dropdown to choose what's already chosen.
    """
    return {
        "item_number": item.sku,
        "name": item.description,
        "description": item.description,
        "price_gross": f"{gross_price(item):.2f}",
        "cost_price": COST_PRICE,
        "stock": STOCK,
    }


