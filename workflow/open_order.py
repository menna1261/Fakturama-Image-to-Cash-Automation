"""
Step 3 (design doc 1.x): open a New Order editor and fill its header.

Leaves the auto-proposed No. untouched, sets Date and Cust.Ref. from the
extraction, and pins the two fixed document modes. The Order editor is
kept open in ctx.editors["order"] for every later step to come back to.
"""

import datetime

from utils.automation.editors import open_editor
from utils.automation.field_specs import NEW_ORDER_FIELDS
from utils.automation.resolver import fill_fields, print_fill_summary
from utils.extraction.schema import OrderExtraction
from workflow.context import Context

# Fixed per the design doc (1.7): these aren't read off the image,
# they're always set this way for every order.
PRICE_MODE = "Net"
VAT_MODE = "With VAT"

ORDER_EDITOR = "order"
_ORDER_TAB_RE = ".*New Order.*"


def format_date_for_fakturama(iso_date: str) -> str:
    """Convert an extracted ISO date (YYYY-MM-DD) into what the Date field expects."""
    return datetime.date.fromisoformat(iso_date).strftime("%m/%d/%Y")


def build_new_order_values(extraction: OrderExtraction) -> dict[str, str]:
    """
    Map extracted order data onto the New Order field-fill values —
    separating "what data do I have" from "how do I type it into the UI".
    """
    return {
        "cust_ref": extraction.external_reference,
        "date": format_date_for_fakturama(extraction.order_date),
        "price_mode": PRICE_MODE,
        "vat_mode": VAT_MODE,
    }


def open_order(ctx: Context) -> None:
    """Click the toolbar's New Order button, then fill the order header."""
    # Ground the button by name + control_type, never by coordinates.
    order_btn = ctx.main_win.child_window(title="Create: New Order", control_type="Button")
    order_btn.wait("visible enabled", timeout=15)
    print("Found 'Order' button, clicking...")
    order_btn.click_input()

    editor = open_editor(ctx.main_win, _ORDER_TAB_RE, ORDER_EDITOR)
    ctx.editors[ORDER_EDITOR] = editor
    ctx.maybe_dump(editor.pane)

    results = fill_fields(editor.pane, NEW_ORDER_FIELDS, build_new_order_values(ctx.extraction))
    print_fill_summary(results)
