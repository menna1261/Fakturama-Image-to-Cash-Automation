"""
Step 4's payment-method sub-branch (design doc 2.10.x).

Two separable jobs, deliberately kept apart:

- `try_select_payment_method()` assigns a method to whatever editor is
  passed in, via its Payment ComboBox.
- `ensure_term_of_payment_exists()` guarantees the method exists in the
  app's terms-of-payment registry, creating it if not. It touches only
  the terms-of-payment screens and knows nothing about the Debtor
  editor, so debtor.py owns the "select -> ensure -> re-select"
  sequencing (and this module doesn't have to import it back).
"""

from utils.automation.editors import (
    Editor,
    close_current_tab,
    open_editor,
    save_current_editor,
)
from utils.automation.field_specs import (
    MAIN_WINDOW_FIELDS,
    NEW_DEBTOR_FIELDS,
    NEW_PAYMENT_METHOD_FIELDS,
    TERMS_OF_PAYMENT_FIELDS,
)
from utils.automation.grids import find_matching_rows
from utils.automation.resolver import (
    AmbiguousOptionError,
    FieldNotFoundError,
    fill_fields,
    find_field,
    print_fill_summary,
    search,
    select_combo_option,
)
from workflow.context import Context, ManualReviewRequired
from workflow.outcomes import Outcome

# Design doc 2.10.4: the Payment Method name we extract maps onto one of
# Fakturama's fixed payment-code dropdown entries.
PAYMENT_CODE_MAPPING = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}

TERMS_LIST_EDITOR = "terms_of_payment"
NEW_TERM_EDITOR = "new_term_of_payment"
_TERMS_LIST_TAB_RE = ".*terms of payment.*"
_NEW_TERM_TAB_RE = ".*New Term of Payment.*"


def try_select_payment_method(scope, method: str) -> bool:
    """
    Select `method` in the Payment ComboBox on `scope`. Returns False
    (rather than raising) when the method simply isn't among the combo's
    options — that's the expected trigger for the creation branch, not an
    error.

    Two of them offered under the same name is a different matter
    entirely (design doc 2.10.2): the run cannot tell which one the order
    means, and returning False here would send it off to create a third.
    That stops for manual review instead.
    """
    try:
        control = find_field(scope, NEW_DEBTOR_FIELDS["payment_method"])
        select_combo_option(control, method)
        print(f"OK: payment_method set to {method!r}")
        return True
    except AmbiguousOptionError as e:
        raise ManualReviewRequired(
            f"Payment Method {method!r} appears more than once in the Debtor's "
            f"Payment dropdown, so which one the order means can't be decided "
            f"automatically — {e}"
        ) from e
    except FieldNotFoundError as e:
        print(f"Payment Method {method!r} is not available in the combo — {e}")
        return False


def ensure_term_of_payment_exists(ctx: Context, method: str) -> Outcome:
    """
    Design doc 2.10.1-2.10.6. Open the terms-of-payment registry, search
    for `method`, and create it if it isn't there.

    Returns Outcome.FOUND if exactly one already existed, Outcome.CREATED
    if we made and saved a new one. Raises ManualReviewRequired on
    ambiguity or on a creation we couldn't complete.

    Leaves the terms-of-payment list tab open and focused — the caller is
    responsible for switching back to whatever editor it cares about.
    """
    list_editor = _open_terms_of_payment(ctx)
    search(list_editor.pane, method, container_spec=TERMS_OF_PAYMENT_FIELDS["search_pane"])

    matches = find_matching_rows(list_editor.pane, {"name": method})
    if len(matches) > 1:
        raise ManualReviewRequired(
            f"{len(matches)} terms of payment match {method!r} exactly — ambiguous."
        )
    if len(matches) == 1:
        print("Exactly one exact match already exists.")
        return Outcome.FOUND

    print("No exact match — creating it.")
    _create_term_of_payment(ctx, list_editor, method)
    return Outcome.CREATED


def _open_terms_of_payment(ctx: Context) -> Editor:
    link = find_field(ctx.main_win, MAIN_WINDOW_FIELDS["terms_of_payment_link"])
    print("Clicking terms of payment...")
    link.click_input()

    editor = open_editor(ctx.main_win, _TERMS_LIST_TAB_RE, TERMS_LIST_EDITOR)
    ctx.editors[TERMS_LIST_EDITOR] = editor
    return editor


def _create_term_of_payment(ctx: Context, list_editor: Editor, method: str) -> None:
    """
    Design doc 2.10.3-2.10.6: open the create editor, fill it, save and
    close it. Nothing touches the "Text unpaid/deposit/paid" fields or
    "Set as standard" — neither is specced.
    """
    if method not in PAYMENT_CODE_MAPPING:
        raise ManualReviewRequired(
            f"{method!r} has no entry in PAYMENT_CODE_MAPPING "
            f"({list(PAYMENT_CODE_MAPPING)}) — can't determine the payment-code "
            f"dropdown value."
        )

    create_btn = find_field(list_editor.pane, TERMS_OF_PAYMENT_FIELDS["create_button"])
    create_btn.click_input()

    editor = open_editor(ctx.main_win, _NEW_TERM_TAB_RE, NEW_TERM_EDITOR)
    ctx.editors[NEW_TERM_EDITOR] = editor
    ctx.maybe_dump(editor.pane)

    results = fill_fields(
        editor.pane,
        NEW_PAYMENT_METHOD_FIELDS,
        {
            "name": method,
            "description": method,
            "payment_code": PAYMENT_CODE_MAPPING[method],
            "cash_discount": "0",
            "discount_days": "0",
            "net_days": "0",
        },
    )
    print_fill_summary(results)

    if not all(results.values()):
        raise ManualReviewRequired(
            f"Not saving the new term of payment {method!r} — these fields failed "
            f"to fill: {[name for name, ok in results.items() if not ok]}"
        )

    save_current_editor(ctx.main_win)
    close_current_tab(ctx.main_win)
    ctx.editors.pop(NEW_TERM_EDITOR, None)
