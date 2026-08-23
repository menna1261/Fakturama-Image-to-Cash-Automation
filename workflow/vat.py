"""
Step 5's VAT sub-branch (design doc 3.4-3.6).

Deliberately the same shape as payment_method.py, for the same reason: a
Product can't be created until the VAT rate it needs already exists in
the app's registry, so "make sure this VAT exists" is a separable job
from "create the product". This module touches only the VATs list and
the TAX Rate editor and knows nothing about the Product editor, so
product.py owns the "check -> create -> come back" sequencing.

Note which control answers "does this rate already exist?". Not the VATs
list — that list is a NatTable and exposes no rows to UIA at all, so
reading it always reports zero results and every run would create another
duplicate rate (observed: two "VAT 19%" rows created in a single run, one
per line item). The Product editor's own VAT dropdown is asked instead.
That's the same thing payment_method.py does with the Debtor's Payment
combo, and it follows the design doc's own principle of using the
selector that will consume the record as the existence check.
"""

from utils.automation.editors import (
    Editor,
    close_current_tab,
    open_editor,
    save_current_editor,
)
from utils.automation.field_specs import (
    MAIN_WINDOW_FIELDS,
    NEW_PRODUCT_FIELDS,
    NEW_VAT_FIELDS,
    VATS_LIST_FIELDS,
)
from utils.automation.resolver import (
    AmbiguousOptionError,
    FieldNotFoundError,
    fill_fields,
    find_field,
    print_fill_summary,
    select_combo_option,
)
from workflow.context import Context, ManualReviewRequired

# Design doc 3.5/3.6: the VAT code every rate we reuse or create must
# carry. The string is Fakturama's own dropdown entry
# (einvoice.untdid5305.s in the shipped bundle.properties).
STANDARD_RATE_CODE = "S (Standard rate)"

VATS_LIST_EDITOR = "vats"
NEW_VAT_EDITOR = "new_vat"
_VATS_LIST_TAB_RE = ".*VAT.*"
# The new-rate editor's tab is titled "New TAX Rate", NOT "New VAT":
# VatEditor labels its part with editor.vat.header, which reads
# "New TAX Rate" in bundle.properties. (The same lookup on PaymentEditor
# gives main.menu.new.payment = "New Term of Payment", which is the
# already-verified title payment_method.py matches on — so the rule the
# title was derived from holds on a screen we've actually seen.) Both
# spellings are accepted here anyway, since only one of them can match.
_NEW_VAT_TAB_RE = ".*New (TAX Rate|VAT).*"


def format_percent(pct: float) -> str:
    """
    19.0 -> "19", 7.5 -> "7.5".

    The design doc names a VAT after its percentage ("VAT 19%"), and that
    name is matched against the registry as an exact string — so a
    stringified float's trailing ".0" would mean never finding the rate
    that's already there, and creating "VAT 19.0%" beside it.
    """
    return f"{pct:g}"


def vat_name(pct: float) -> str:
    """The exact Name/Description a VAT rate carries (design doc 3.5-3.6)."""
    return f"VAT {format_percent(pct)}%"


def try_select_vat(scope, pct: float) -> bool:
    """
    Select this line item's VAT rate in the Product editor's VAT combo.
    Returns False (rather than raising) when the rate simply isn't on
    offer — that's the expected trigger for the creation branch, not an
    error.

    Two rates sharing the name is a different matter (design doc 3.5's
    "if any setting conflicts, stop for manual review"): the run can't
    tell which one the order means, and returning False here would send
    it off to create a third.
    """
    name = vat_name(pct)
    try:
        control = find_field(scope, NEW_PRODUCT_FIELDS["vat"])
        select_combo_option(control, name)
        print(f"OK: VAT set to {name!r}")
        return True
    except AmbiguousOptionError as e:
        raise ManualReviewRequired(
            f"The VAT rate {name!r} appears more than once in the Product's VAT "
            f"dropdown, so which one the order means can't be decided "
            f"automatically — {e}"
        ) from e
    except FieldNotFoundError as e:
        print(f"VAT rate {name!r} is not available in the combo — {e}")
        return False


def create_vat(ctx: Context, pct: float) -> None:
    """
    Design doc 3.4-3.6: add the rate this line item needs to the registry.

    Called only once try_select_vat() has established the rate isn't on
    offer, so this creates unconditionally — there is deliberately no
    "search the list first" step, because the list can't be read (see the
    module docstring) and a search that always returns nothing is worse
    than no search: it looks like a check while guaranteeing a duplicate.

    Leaves the VATs list tab open and focused — the caller decides which
    editor to switch back to.
    """
    list_editor = _open_vats(ctx)
    _create_vat(ctx, list_editor, pct)


def _open_vats(ctx: Context) -> Editor:
    link = find_field(ctx.main_win, MAIN_WINDOW_FIELDS["vats_link"])
    print("Clicking VATs...")
    link.click_input()

    editor = open_editor(ctx.main_win, _VATS_LIST_TAB_RE, VATS_LIST_EDITOR)
    ctx.editors[VATS_LIST_EDITOR] = editor
    return editor


def _create_vat(ctx: Context, list_editor: Editor, pct: float) -> None:
    """
    Design doc 3.6: open the create editor from the list's green +, fill
    it, save and close it. Category and the "This TAX Rate" standard-rate
    button are left alone — the doc says to leave the displayed Standard
    VAT unchanged.
    """
    name = vat_name(pct)

    create_btn = find_field(list_editor.pane, VATS_LIST_FIELDS["create_button"])
    print("Clicking the green + on the VATs list...")
    create_btn.click_input()

    editor = open_editor(ctx.main_win, _NEW_VAT_TAB_RE, NEW_VAT_EDITOR)
    ctx.editors[NEW_VAT_EDITOR] = editor
    ctx.maybe_dump(editor.pane)

    results = fill_fields(
        editor.pane,
        NEW_VAT_FIELDS,
        {
            "name": name,
            "description": name,
            "vat_code": STANDARD_RATE_CODE,
            # Bare number, no '%'. Value is a percent-formatted field that
            # renders its own suffix, and until type_into() started
            # escaping send_keys syntax the trailing '%' was silently
            # dropped anyway (it read as Alt) — so "19" is both what this
            # always actually sent and what a person would type. The NAME
            # above keeps its '%', because the design doc specifies the
            # literal name "VAT 19%".
            "value": format_percent(pct),
        },
    )
    print_fill_summary(results)

    if not all(results.values()):
        raise ManualReviewRequired(
            f"Not saving the new VAT rate {name!r} — these fields failed to "
            f"fill: {[field for field, ok in results.items() if not ok]}. "
            f"Saving a half-filled tax rate would leave a record that the "
            f"reuse path (3.5) then refuses to match on the next run."
        )

    save_current_editor(ctx.main_win)
    close_current_tab(ctx.main_win)
    ctx.editors.pop(NEW_VAT_EDITOR, None)
