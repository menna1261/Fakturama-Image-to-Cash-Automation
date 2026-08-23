"""
Reading and selecting rows in Fakturama's search-result grids.

Shared by every "search for an existing record" screen — the "Select the
address" dialog, the terms-of-payment list, and (once implemented) the
product and VAT lists — since they're all the same SWT Table shape.

NOT every grid can be read this way: some aren't exposed via UIA at all.
Confirmed for the "Select the address" dialog via a full
print_control_identifiers dump — zero representation of any row, even
with a real, visibly-rendered, exact-matching row on screen (a
custom-painted SWT Table that doesn't implement UIA's Table/Grid
accessibility pattern). find_result_rows() can never return a row there,
so that dialog is read by clipboard walk instead; see
clipboard_grid.py.

CAUTION: the list VIEWS are no better. This module's docstring used to
claim the terms-of-payment list as an example of a grid that does expose
its rows; that was never verified, and the equivalent VATs list was then
observed logging "No Table control found ... treating as zero results"
with a matching row plainly on screen. They're all Nebula NatTables.
Since callers spell "record doesn't exist" as "zero rows", a search here
that always returns nothing doesn't read as a failure — it reads as
permission to create a duplicate, which is exactly what happened (two
identical "VAT 19%" rates in one run). Prefer asking the combo that will
consume the record, the way payment_method.py and vat.py now do.

payment_method.py is the one remaining caller, and it is safe only
because the combo already answered the question: it reaches its
find_matching_rows() call solely when the Debtor's Payment dropdown does
NOT offer the method, so "zero rows -> create" is the right answer
regardless of whether the read worked. Treat that call as belt-and-braces
rather than as the check it looks like.
"""

import logging

logger = logging.getLogger("automation.grids")


def find_result_rows(scope) -> list:
    """
    Return the result grid's row controls (DataItems), or [] if there's
    no grid at all — which is itself meaningful: on an empty database a
    search with zero matches never renders a Table control in the tree
    (live-filtered), so "no Table found" is the expected shape of a
    genuine zero-results search, not necessarily a lookup bug.
    """
    tables = scope.descendants(control_type="Table")
    if not tables:
        logger.info("No Table control found under %s — treating as zero results", scope)
        return []
    return tables[0].children(control_type="DataItem")


def row_cell_texts(row) -> list[str]:
    return [c.window_text() for c in row.descendants(control_type="Text")]


def row_matches(cells: list[str], expected: dict[str, str]) -> bool:
    """
    True when every non-empty value in `expected` appears among the row's
    cells (case- and whitespace-insensitive).

    Logs the field-by-field comparison it makes, so a mismatch (case,
    whitespace, wrong column, a field the row genuinely doesn't contain)
    is visible directly instead of only a pass/fail result. Blank
    expected values are skipped rather than failing the match — the
    extraction legitimately leaves optional fields empty.
    """
    normalized_cells = {c.strip().casefold() for c in cells}

    all_match = True
    for field_name, value in expected.items():
        if not value:
            print(f"    [SKIP] {field_name}: no expected value")
            continue
        found = value.strip().casefold() in normalized_cells
        print(
            f"    [{'OK' if found else 'MISS'}] {field_name}: expected {value!r} — "
            f"{'found' if found else 'NOT found'} in row cells {sorted(normalized_cells)}"
        )
        if not found:
            all_match = False

    return all_match


def find_matching_rows(scope, expected: dict[str, str]) -> list:
    """Read every result row under `scope` and return those matching `expected`."""
    rows = find_result_rows(scope)
    print(f"Found {len(rows)} result row(s).")
    matches = []
    for row in rows:
        cells = row_cell_texts(row)
        print(f"  Row: {cells}")
        if row_matches(cells, expected):
            matches.append(row)
    return matches


def select_grid_row(row) -> None:
    """
    Select a result-grid row. Tries UIA's SelectionItemPattern (.select())
    first — the "correct" way to select a list/table row. Falls back to
    clicking directly on the row's first Text cell rather than the row
    container's own bounding box: SWT table rows sometimes have empty
    space in their outer rect that doesn't register as a real click on
    the row, whereas a cell's Text always corresponds to actual rendered
    content.
    """
    try:
        row.select()
        return
    except Exception:
        pass

    cells = row.descendants(control_type="Text")
    target = cells[0] if cells else row
    target.click_input()
