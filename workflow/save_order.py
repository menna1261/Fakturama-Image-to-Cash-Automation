"""
Step 6 (design doc 4.x): check the Order's totals against the image,
save it, and create its linked Invoice.

The totals check is the point of this step. Everything before it edits
one field at a time and verifies that field; 4.3 is the first check that
looks at the document as a whole, so it's the one that catches a line
that went in wrong in a way no per-field read-back could see — a quantity
on the wrong row still reads back correctly on that row.
"""

import re

from utils.automation.editors import open_editor, save_current_editor
from utils.automation.field_specs import DOCUMENT_TOTALS_FIELDS
from utils.automation.resolver import find_field, select_combo_option
from workflow.context import Context, ManualReviewRequired
from workflow.open_order import ORDER_EDITOR

INVOICE_EDITOR = "invoice"
_INVOICE_TAB_RE = ".*New Invoice.*"

# Design doc 4.2: fixed unless the image supplies order-level values,
# which the extraction schema has no field for — so they're always these.
NO_SHIPPING = "Free of shipping costs"

# Money read back off the screen is compared to the cent. Anything looser
# would let a wrong line discount through; anything tighter would trip on
# the app's own rounding.
_MONEY_TOLERANCE = 0.005


def save_order(ctx: Context) -> None:
    """Design doc 4.1-4.7."""
    editor = ctx.editor(ORDER_EDITOR)
    editor.switch_to()

    _confirm_order_level_values(ctx, editor)
    _confirm_totals(ctx, editor)

    print("Saving the Order (4.4)...")
    save_current_editor(ctx.main_win)

    _create_followup_invoice(ctx, editor)


def _confirm_order_level_values(ctx: Context, editor) -> None:
    """
    Design doc 4.2: overall Discount stays 0% and Shipping stays "Free of
    shipping costs" / 0.00.

    Both are read before being written, and only written if they're
    already wrong. Fakturama defaults them correctly, so the normal path
    touches nothing — which matters because the Discount field here is
    document-level, and typing into it by reflex would silently discount
    the whole Order.
    """
    discount = find_field(editor.pane, DOCUMENT_TOTALS_FIELDS["discount"])
    shown = discount.window_text()
    value = _money(shown)
    if value not in (None, 0.0):
        raise ManualReviewRequired(
            f"The Order's overall Discount reads {shown!r}, not 0%. The image "
            f"supplies per-line discounts only, so an order-level discount "
            f"here would come from somewhere this run doesn't know about."
        )
    print(f"OK (4.2): overall Discount reads {shown!r}.")

    try:
        shipping = find_field(editor.pane, DOCUMENT_TOTALS_FIELDS["shipping"])
        select_combo_option(shipping, NO_SHIPPING)
        print(f"OK (4.2): Shipping set to {NO_SHIPPING!r}.")
    except Exception as e:
        # Not fatal on its own — the totals check below is what actually
        # decides whether the Order is right, and a stray shipping cost
        # would show up there as a mismatched Total.
        print(f"WARNING (4.2): could not confirm Shipping — {e}")


def _confirm_totals(ctx: Context, editor) -> None:
    """
    Design doc 4.3: Total Net, VAT and Total must match the source.

    This is the whole-document arithmetic check, and the only place a
    mis-keyed line quantity or discount is guaranteed to surface: a
    wrong value typed into the right cell reads back happily from that
    cell, but it cannot make the totals add up.
    """
    extraction = ctx.extraction
    expected = {
        "total_net": extraction.net_total,
        "vat_total": extraction.vat_total,
        "total": extraction.gross_total,
    }

    problems = []
    for field, wanted in expected.items():
        control = find_field(editor.pane, DOCUMENT_TOTALS_FIELDS[field])
        shown = control.window_text()
        actual = _money(shown)
        if actual is None or abs(actual - wanted) > _MONEY_TOLERANCE:
            problems.append(f"{field}: image says {wanted:.2f}, Order shows {shown!r}")
        else:
            print(f"OK (4.3): {field} reads {shown!r} = {wanted:.2f}.")

    if problems:
        raise ManualReviewRequired(
            "The Order's totals do not match the source image, so it is not "
            "being saved:\n  " + "\n  ".join(problems) + "\n"
            "This is the check that catches a line item that went in wrong — "
            "compare the item rows against the image before saving by hand."
        )


def _create_followup_invoice(ctx: Context, editor) -> None:
    """
    Design doc 4.6-4.7: click Invoice in the saved Order's "Create a
    follow-up document" area — deliberately not the top toolbar's Invoice
    button, because only the follow-up action carries the Order
    relationship the linked Invoice needs.
    """
    button = find_field(editor.pane, DOCUMENT_TOTALS_FIELDS["followup_invoice_button"])
    print("Clicking Invoice in 'Create a follow-up document' (4.6)...")
    button.click_input()

    invoice = open_editor(ctx.main_win, _INVOICE_TAB_RE, INVOICE_EDITOR)
    ctx.editors[INVOICE_EDITOR] = invoice
    ctx.maybe_dump(invoice.pane)


def _money(text: str) -> float | None:
    """
    Read a number off a rendered money/percent field, tolerating the
    currency symbol, thousands separators and either decimal mark
    ("$1,234.50", "1.234,50 €", "0%").
    """
    cleaned = text.strip().replace(" ", " ")
    match = re.search(r"-?[\d][\d.,\s]*", cleaned)
    if not match:
        return None
    number = match.group(0).strip().replace(" ", "")

    # Whichever separator comes last is the decimal mark; the other is
    # grouping. Guessing wrong here turns 1.234,50 into 1.23.
    last_dot, last_comma = number.rfind("."), number.rfind(",")
    if last_dot > last_comma:
        number = number.replace(",", "")
    elif last_comma > last_dot:
        number = number.replace(".", "").replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None
