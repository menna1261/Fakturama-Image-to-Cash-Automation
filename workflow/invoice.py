"""
Step 7-8 (design doc 5.x): complete and verify the linked Invoice.

The Invoice is created by the Order's follow-up action, so almost
everything on it — Cust.Ref., addresses, item lines, totals, VAT mode —
is copied by Fakturama rather than typed by this run. 5.1 says to leave
the proposed No. and dates alone, so this step confirms what was copied
and then applies the one thing the Invoice carries that the Order does
not: the payment status.
"""

from utils.automation.editors import save_current_editor
from utils.automation.field_specs import DOCUMENT_TOTALS_FIELDS, INVOICE_FIELDS
from utils.automation.resolver import FieldNotFoundError, find_field, set_field_value
from workflow.context import Context, ManualReviewRequired
from workflow.open_order import format_date_for_fakturama
from workflow.save_order import INVOICE_EDITOR, _money

# Design doc 5.3 keys off the extracted status. The image states it in
# words ("PAID"), so compare case-insensitively rather than on an exact
# spelling the extraction happens to produce.
_PAID = "paid"


def complete_invoice(ctx: Context) -> None:
    """Design doc 5.1-5.4."""
    editor = ctx.editor(INVOICE_EDITOR)
    editor.switch_to()

    _confirm_copied_totals(ctx, editor)
    _apply_payment_status(ctx, editor)

    print("Saving the Invoice (5.4)...")
    save_current_editor(ctx.main_win)


def _confirm_copied_totals(ctx: Context, editor) -> None:
    """
    Design doc 5.1: confirm the totals really were copied from the Order.

    Checking the totals rather than every copied field is deliberate:
    they're the arithmetic consequence of the item lines, the addresses
    and the VAT mode, so a total that matches the source means the copy
    brought the lines across intact. A field-by-field comparison would be
    longer and prove less.
    """
    extraction = ctx.extraction
    expected = {
        "total_net": extraction.net_total,
        "vat_total": extraction.vat_total,
        "total": extraction.gross_total,
    }

    problems = []
    for field, wanted in expected.items():
        try:
            control = find_field(editor.pane, DOCUMENT_TOTALS_FIELDS[field])
        except FieldNotFoundError as e:
            problems.append(f"{field}: not found on the Invoice — {e}")
            continue
        shown = control.window_text()
        actual = _money(shown)
        if actual is None or abs(actual - wanted) > 0.005:
            problems.append(f"{field}: Order/image says {wanted:.2f}, Invoice shows {shown!r}")
        else:
            print(f"OK (5.1): Invoice {field} reads {shown!r} = {wanted:.2f}.")

    if problems:
        raise ManualReviewRequired(
            "The linked Invoice's totals don't match the Order it was created "
            "from:\n  " + "\n  ".join(problems) + "\n"
            "The follow-up action is supposed to copy the lines across intact, "
            "so a mismatch means the Invoice is not a faithful copy — it must "
            "not be saved and marked paid in this state."
        )


def _apply_payment_status(ctx: Context, editor) -> None:
    """
    Design doc 5.3. If the image says PAID: tick "paid", set the payment
    date to the extracted date and Value to the full Invoice Total. If it
    doesn't, leave the box clear and invent nothing — the doc is explicit
    that an unpaid invoice gets no date and no value.
    """
    payment = ctx.extraction.payment
    is_paid = (payment.paid_status or "").strip().casefold() == _PAID

    if not is_paid:
        print(
            f"Paid status is {payment.paid_status!r}, not PAID — leaving 'paid' "
            f"unticked and inventing no date or value (5.3)."
        )
        return

    if not payment.payment_date:
        raise ManualReviewRequired(
            "The image says the invoice is PAID but no payment date was "
            "extracted. Design doc 5.3 allows no invented date, and ticking "
            "'paid' without one would leave the Invoice inconsistent."
        )

    checkbox = find_field(editor.pane, INVOICE_FIELDS["paid_checkbox"])
    if checkbox.get_toggle_state() != 1:
        print("Ticking 'paid' (5.3)...")
        checkbox.click_input()

    # Value is the FULL Invoice Total, per 5.3 — not the net, and not the
    # source's gross if the two ever disagree. _confirm_copied_totals()
    # has already established that they don't.
    set_field_value(
        find_field(editor.pane, INVOICE_FIELDS["paid_date"]),
        format_date_for_fakturama(payment.payment_date),
    )
    set_field_value(
        find_field(editor.pane, INVOICE_FIELDS["paid_value"]),
        f"{ctx.extraction.gross_total:.2f}",
    )
    print(
        f"OK (5.3): marked paid on {payment.payment_date} for "
        f"{ctx.extraction.gross_total:.2f}."
    )
