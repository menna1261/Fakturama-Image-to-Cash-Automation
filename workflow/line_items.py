"""
Step 5's line-completion half (design doc 3.13-3.16): Qty., U.Price, VAT
and Discount on each item line of the open Order.

The Items table is a Nebula NatTable (DocumentEditor holds a
DocumentItemListTable whose getNatTable() returns one), which paints
every row, column and cell onto a single SWT Canvas. Nothing inside it
exists in the UIA tree, so no FieldSpec can address a cell and the
approach used for every other screen in this bot simply does not apply.

What it is driven with instead is the keyboard, and read with the
clipboard — the same two mechanisms clipboard_grid.py uses on the
selection dialogs, which are the same kind of widget.

The one thing deliberately NOT done here is measuring cell coordinates.
The selection dialogs could get away with a measured GridGeometry because
they are fixed-size; this table lives in a resizable editor and draws a
preference-dependent subset of its sixteen possible columns
(DocumentItemListDescriptor), so any hard-coded column x or row y would
be a guess that silently writes a price into the wrong field. Exactly one
click is used, purely to give the table keyboard focus; everything after
that is Ctrl+Home / arrow keys, and every value written is read back.
"""

import re
import time

from pywinauto import mouse
from pywinauto.keyboard import send_keys

from utils.automation.clipboard_grid import (
    clear_clipboard,
    dismiss_error_dialog,
    get_clipboard_text,
)
from utils.automation.resolver import escape_for_type_keys
from utils.extraction.schema import LineItem
from workflow.context import Context, ManualReviewRequired
from workflow.open_order import ORDER_EDITOR
from workflow.vat import format_percent, vat_name

# Let the table repaint after a keystroke before reading it back.
_SETTLE = 0.15
# A cell editor commits asynchronously, and committing a quantity or a
# discount makes Fakturama recalculate the line's price before the row
# reads back with the new value. 0.35s was not enough: a write that had
# in fact landed read back as unchanged, and the pointless retry that
# followed is what knocked the selection off the row.
_COMMIT_SETTLE = 0.9
# No Fakturama document item row has more columns than this
# (DocumentItemListDescriptor defines 16), so a scan that passes it is
# looping rather than reading.
_MAX_COLUMNS = 20
# Pause between synthetic keystrokes inside a cell editor.
_KEY_PAUSE = 0.02
# How long to wait for NatTable to instantiate a cell editor, and how far
# to step when probing for one.
_EDITOR_TIMEOUT = 4.0
_PROBE_STEP_X = 24
_PROBE_STEP_Y = 6
# A cell editor is one row tall. Anything taller is a different control
# that merely overlaps the click point — the first probe matched one and
# reported a "row" 152px high inside a table only 140px tall.
_MAX_CELL_HEIGHT = 40

# Reference data: the fixed left-to-right order of
# DocumentItemListDescriptor, read out of the shipped rcp jar. Which of
# these are actually drawn depends on document preferences, but the ones
# that ARE drawn always keep this order — which is what makes the
# adjacency facts _map_columns() relies on true for any drawn subset.
_COLUMN_ORDER = [
    "POSITION", "OPTIONAL", "QUANTITY", "QUNIT", "WEIGHT", "ITEMNUMBER",
    "PICTURE", "VESTINGDATESTART", "VESTINGDATEEND", "NAME", "DESCRIPTION",
    "VAT", "SALESEQUALIZATIONTAX", "UNITPRICE", "DISCOUNT", "TOTALPRICE",
]


def complete_line_items(ctx: Context) -> None:
    """
    Design doc 3.13-3.17. Every product is already on the Order at
    Fakturama's default quantity; this sets what the image actually says.
    """
    items = ctx.extraction.items
    table = _find_items_table(ctx)
    _focus_table(table)

    # Calibrate the row geometry once from a real cell editor's rectangle,
    # then address every row from it — NatTable rows are uniform.
    rect = table.rectangle()
    row_top, row_height = _find_row_band(ctx, rect)

    for index, item in enumerate(items):
        print("=" * 80)
        print(f"Line {index + 1}/{len(items)}: {item.sku!r}")
        _complete_one(ctx, index, item, rect, row_top, row_height)


def _find_items_table(ctx: Context):
    """
    Locate the NatTable's canvas in the Order editor.

    The canvas itself has no title, so it's found by position relative to
    the "Items" label, whose text is the app's own string
    (editor.document.items). Both come from resolved UIA rectangles — no
    constant is hard-coded, so this survives a resized window, which is
    the whole reason cell coordinates are avoided elsewhere in this file.

    Confirmed against a real dump of the New Order editor, where the
    label sits at (386,374)-(422,394) and the table area is the pane at
    (432,376)-(1884,965) nested inside (430,374)-(1894,967).
    """
    pane = ctx.editor(ORDER_EDITOR).pane

    labels = [c for c in pane.descendants(control_type="Text") if c.window_text() == "Items"]
    if not labels:
        raise ManualReviewRequired(
            "Could not find the 'Items' label on the Order editor, so the item "
            "table's position can't be established."
        )
    label_rect = labels[0].rectangle()

    # The table is the big pane to the RIGHT of the label and starting at
    # roughly its height. Several nested panes match; take the innermost
    # (smallest area), so a click lands inside the table proper rather
    # than on a border or scroll area wrapping it.
    candidates = []
    for candidate in pane.descendants(control_type="Pane"):
        rect = candidate.rectangle()
        if rect.left < label_rect.right:
            continue
        if abs(rect.top - label_rect.top) > 20:
            continue
        if rect.width() < 200 or rect.height() < 100:
            continue
        candidates.append((rect.width() * rect.height(), candidate, rect))

    if not candidates:
        raise ManualReviewRequired(
            f"No pane matching the Items table was found to the right of the "
            f"'Items' label at {label_rect}. The Order editor's layout differs "
            f"from the one this was built against — re-dump it with "
            f"`dump_screen.py \"New Order\"`."
        )

    _, table, rect = min(candidates, key=lambda c: c[0])
    print(f"Items table canvas at {rect}.")
    return table


def _focus_table(table) -> None:
    """
    Give the table keyboard focus with the single click this file allows
    itself, then let the keyboard do the rest.

    The click goes near the top-left of the table body. The y offset
    clears NatTable's column header (~20px) and lands in one of the first
    rows; which row doesn't matter, because Ctrl+Home immediately moves
    to the first cell of the first row. Clicking the HEADER would matter
    a great deal — that sorts the table and reorders the very rows this
    step then addresses by index — which is why the offset is generous.
    """
    rect = table.rectangle()
    x, y = rect.left + 40, rect.top + 45
    print(f"Clicking the items table at ({x}, {y}) to give it keyboard focus...")
    mouse.click(coords=(x, y))
    time.sleep(_SETTLE)


def _read_cell(ctx: Context) -> str:
    """
    Copy the currently selected cell and read it back.

    Returns "" when nothing was copied, including when the copy raised
    the app's "Internal Error" popup — the same NullPointerException an
    empty grid throws elsewhere, dismissed the same way rather than left
    to block every step that follows.
    """
    clear_clipboard()
    send_keys("^c")
    time.sleep(_SETTLE)
    if dismiss_error_dialog(ctx.pid):
        return ""
    return get_clipboard_text().strip()


def _goto_row(ctx: Context, index: int) -> None:
    """Move to the first cell of row `index` (0-based)."""
    send_keys("^{HOME}")
    time.sleep(_SETTLE)
    for _ in range(index):
        send_keys("{DOWN}")
        time.sleep(_SETTLE)


def _scan_row(ctx: Context, row_index: int | None = None) -> list[str]:
    """
    Read the current row as a list of cells.

    One Ctrl+C does it: this table's copy handler is row-scoped, so a
    single copy returns every cell of the selected row, tab-separated.
    That was established the hard way — an earlier version walked Right
    copying one cell at a time, and every read came back as the same
    complete row, because moving the cell cursor doesn't change what the
    copy handler emits.

    Reading the row in one go is also better than walking it: no
    keystrokes are spent, nothing depends on the cursor ending up
    somewhere particular, and the tab positions are exactly the visible
    column positions, which is what _map_columns() needs.
    """
    raw = _read_cell(ctx)
    if not raw and row_index is not None:
        # An empty copy usually means nothing is selected rather than
        # nothing being there: committing a cell with Enter moves the
        # selection down a row, and past the last row there is nothing to
        # copy — which makes the app throw its NullPointerException popup
        # and hand back "". Re-select the row and ask again before
        # treating it as a real failure.
        print("  (copy came back empty — re-selecting the row and retrying)")
        _goto_row(ctx, row_index)
        raw = _read_cell(ctx)
    if not raw:
        raise ManualReviewRequired(
            "Copying the selected item row returned nothing, even after "
            "re-selecting it. Either the table has no row selected (the focus "
            "click may have missed it) or the Order has no item lines at all."
        )
    cells = [c.strip() for c in raw.split("\t")]
    if len(cells) > _MAX_COLUMNS:
        raise ManualReviewRequired(
            f"The item row copied back {len(cells)} cells, more than any "
            f"Fakturama document item row has ({_MAX_COLUMNS}) — the copy is not "
            f"a single row. Raw text:\n  {raw!r}"
        )
    return cells


def _map_columns(cells: list[str], item: LineItem) -> dict[str, int]:
    """
    Work out which visible column is which, from two anchors whose values
    we know exactly — the SKU (Item No.) and the description (Name) — plus
    the fact that the drawn columns always keep DocumentItemListDescriptor's
    relative order.

    Anchoring on content rather than counting from the left is what makes
    this independent of which optional columns the install happens to
    draw (Opt., Q. Unit, weight, picture and the vesting dates are all
    optional, and each one present would shift a fixed index).
    """
    sku = item.sku.strip().casefold()
    name = item.description.strip().casefold()

    item_idx = next((i for i, c in enumerate(cells) if c.strip().casefold() == sku), None)
    name_idx = next((i for i, c in enumerate(cells) if c.strip().casefold() == name), None)
    if item_idx is None or name_idx is None or name_idx <= item_idx:
        raise ManualReviewRequired(
            f"Could not locate the Item No. and Name columns for {item.sku!r} in "
            f"the row the table actually returned:\n"
            f"  {list(enumerate(cells))}\n"
            f"Expected a cell equal to {item.sku!r} followed later by one equal "
            f"to {item.description!r}. Without both anchors the remaining columns "
            f"can't be identified, and writing a price into a guessed column is "
            f"exactly what this refuses to do."
        )

    # The remaining columns are pinned by ADJACENCY, not by counting
    # forward through _COLUMN_ORDER. Counting forward silently absorbs any
    # optional column this install doesn't draw: with
    # SALESEQUALIZATIONTAX hidden (it is, outside Spain) a forward walk
    # hands its index to UNITPRICE and shifts DISCOUNT and TOTALPRICE one
    # place left — writing the unit price into the VAT cell.
    #
    # Three descriptor facts survive any subset being drawn, because none
    # of the optional columns sits between these pairs:
    #   TOTALPRICE is last, DISCOUNT is immediately before it, and
    #   UNITPRICE is immediately before DISCOUNT.
    # QUANTITY is likewise immediately before ITEMNUMBER whenever the
    # optional QUNIT/WEIGHT columns are hidden — which is checked below
    # rather than assumed.
    total_idx = len(cells) - 1
    discount_idx = total_idx - 1
    unitprice_idx = discount_idx - 1
    quantity_idx = item_idx - 1

    if quantity_idx < 0 or unitprice_idx <= name_idx:
        raise ManualReviewRequired(
            f"The item row for {item.sku!r} doesn't have the column layout this "
            f"step expects (Qty. before Item No.; U.Price, Discount and Price as "
            f"the last three). Row read:\n  {list(enumerate(cells))}"
        )

    # Verify the presumed Qty. column really is one: a freshly added item
    # carries Fakturama's default quantity, so this cell must read as a
    # number. If an optional column is drawn between Qty. and Item No.,
    # this is where that shows up instead of becoming a wrong quantity.
    if _first_number(cells[quantity_idx]) is None:
        raise ManualReviewRequired(
            f"The cell before Item No. ({cells[quantity_idx]!r}) isn't numeric, so "
            f"it isn't the Qty. column — an optional column (Q. Unit, weight, "
            f"Opt.) is probably drawn between them. Row read:\n"
            f"  {list(enumerate(cells))}"
        )

    # VAT sits between Description and U.Price; identify it by content
    # rather than position, since SALESEQUALIZATIONTAX may or may not be
    # drawn in that same gap.
    vat_idx = next(
        (
            i
            for i in range(name_idx + 1, unitprice_idx)
            if "vat" in cells[i].casefold() or "%" in cells[i]
        ),
        None,
    )

    mapping = {
        "QUANTITY": quantity_idx,
        "ITEMNUMBER": item_idx,
        "NAME": name_idx,
        "UNITPRICE": unitprice_idx,
        "DISCOUNT": discount_idx,
        "TOTALPRICE": total_idx,
    }
    if vat_idx is not None:
        mapping["VAT"] = vat_idx
    return mapping


def _picture_index(columns: dict[str, int]) -> int | None:
    """
    The copied index of the Picture column, if this table draws one.

    Per DocumentItemListDescriptor, Picture is the only column that can
    sit between Item No. and Name — so any gap between those two anchors
    is it.
    """
    gap = columns["NAME"] - columns["ITEMNUMBER"]
    return columns["ITEMNUMBER"] + 1 if gap > 1 else None


def _nav_index(copied_index: int, columns: dict[str, int]) -> int:
    """
    Translate a COPIED column index into the number of Right presses
    needed to reach that cell — which is the same number.

    Both sequences start at Qty. and include Picture: Ctrl+Home lands on
    Qty. (the Pos. column is a row number and is not navigable), and
    Ctrl+C likewise omits Pos. Two independent observations on the live
    table pin this down, and only this mapping satisfies both:
      - 7 Right presses reached Discount (the line total moved 250 -> 225)
      - 1 Right press reached Item No. (it was overwritten with "2")
    Counting Pos. as navigable satisfies the first but not the second,
    and was tried: it wrote the quantity into Item No.

    This is a function rather than a bare pass-through so the reasoning
    has somewhere to live, and so a table that hides Picture has one
    place to be corrected.
    """
    return copied_index


def _editor_controls(scope) -> list:
    """Every control that could be an in-place cell editor."""
    return (scope.descendants(control_type="Edit")
            + scope.descendants(control_type="ComboBox"))


def _covers(control, x: int, y: int) -> bool:
    """Whether a control's rectangle contains the point, with a little slack."""
    try:
        rect = control.rectangle()
    except Exception:
        return False
    return rect.left - 4 <= x <= rect.right + 4 and rect.top - 4 <= y <= rect.bottom + 4


def _ident(control):
    try:
        return tuple(control.element_info.runtime_id)
    except Exception:
        return id(control)


def _is_cell_sized(control, bounds) -> bool:
    """
    Whether a control is plausibly ONE cell's in-place editor.

    Without this, the point-containment test alone matches any large Edit
    that happens to sit behind the table, and the first probe duly
    reported a "row" 152px tall inside a table only 140px high. A cell
    editor is short and lives entirely within the grid.
    """
    try:
        rect = control.rectangle()
    except Exception:
        return False
    return (
        rect.height() <= _MAX_CELL_HEIGHT
        and rect.top >= bounds.top - 4
        and rect.bottom <= bounds.bottom + 4
        and rect.left >= bounds.left - 4
        and rect.right <= bounds.right + 4
    )


def _open_cell_editor(ctx: Context, x: int, y: int, bounds, timeout: float = _EDITOR_TIMEOUT):
    """
    Double-click (x, y) and return the SWT control NatTable creates for
    that cell, or None if the cell has no editor.

    This is the whole trick for writing to a NatTable. The canvas exposes
    no cell elements, but the moment a cell is activated NatTable
    instantiates a REAL SWT Text or Combo for it, and that control DOES
    appear in the UIA tree — so from here on the cell is an ordinary
    field: focus it, type into it, read the value straight back.

    Blind-typing at a cell cursor could never do that. It gave no way to
    distinguish "the value didn't commit" from "the keystrokes went to
    the wrong column", which is how a quantity came to overwrite an
    Item No., and why a stale row read looked like a failed write.
    """
    before = {_ident(c) for c in _editor_controls(ctx.main_win) if _covers(c, x, y)}

    # Two SEPARATE single clicks, not a double-click. NatTable selects
    # the cell on the first and activates its editor on the second; a
    # real double-click is a different event and does not reliably open
    # the editor.
    mouse.click(coords=(x, y))
    time.sleep(0.15)
    mouse.click(coords=(x, y))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for control in _editor_controls(ctx.main_win):
            if not _covers(control, x, y) or not _is_cell_sized(control, bounds):
                continue
            # Either freshly created for this cell, or the one editor
            # control being reused for a different cell — both are the
            # real in-place editor. Anything that merely overlaps the
            # point has already been excluded by _is_cell_sized().
            if _ident(control) not in before or _covers(control, x, y):
                return control
        time.sleep(0.15)
    return None


def _close_editor() -> None:
    """Abandon an editor that was opened only to be looked at."""
    send_keys("{ESC}")
    time.sleep(_SETTLE)


def _find_row_band(ctx: Context, rect) -> tuple[int, int]:
    """
    Probe downward until a cell editor opens, and return the first data
    row's (top, height).

    Probing rather than assuming a header height: NatTable's header and
    row heights follow the theme and DPI, while the editor's own
    rectangle reports the real geometry exactly. One probe calibrates
    every row, since NatTable rows are uniform.
    """
    # Probe across the width as well as down. Probing only near the left
    # edge finds nothing: the leftmost column is Pos., a row number with
    # no editor at all, so every probe there fails however far down it
    # goes. These fractions spread the attempts over the whole row so at
    # least one lands on an editable column whatever the column widths.
    width = rect.width()
    xs = [rect.left + int(width * f) for f in (0.25, 0.40, 0.55, 0.10, 0.70, 0.85)]

    y = rect.top + 22
    while y < rect.top + 22 + _PROBE_STEP_Y * 10:
        for x in xs:
            editor = _open_cell_editor(ctx, x, y, rect, timeout=0.5)
            if editor is not None:
                band = editor.rectangle()
                _close_editor()
                print(
                    f"  Row geometry: first row top={band.top}, "
                    f"height={band.height()} (found at x={x}, y={y})"
                )
                return band.top, band.height()
        y += _PROBE_STEP_Y

    raise ManualReviewRequired(
        f"No editable cell was found in the items table {rect} after probing "
        f"{len(xs)} columns across its top 10 rows. Either the Order has no "
        f"item lines, or clicking a cell twice no longer opens an in-place "
        f"editor on this Fakturama build."
    )


def _scan_editors(ctx: Context, rect, row_y: int) -> list[tuple]:
    """
    Walk one row left to right opening each cell's editor, and return
    [(x_centre, value)] in visual order.

    Each editor is opened, read and abandoned with Escape — nothing is
    written. Stepping by the editor's own right edge means column widths
    never have to be known: Fakturama persists per-column widths, so a
    restored layout would otherwise shift every cell.
    """
    found = []
    x = rect.left + 8
    while x < rect.right - 8:
        editor = _open_cell_editor(ctx, x, row_y, rect, timeout=1.0)
        if editor is None:
            x += _PROBE_STEP_X          # a column with no editor (Pos., Picture)
            continue
        band = editor.rectangle()
        value = (editor.window_text() or "").strip()
        _close_editor()
        found.append(((band.left + band.right) // 2, value))
        x = band.right + 6
    return found


def _cell_x_for(ctx: Context, rect, row_y: int, cells: list[str],
                columns: dict[str, int], column: str) -> int:
    """
    The x to double-click for `column` on this row.

    The editable cells, walked left to right, line up with the copied
    cells in the same order minus any column that has no editor. Rather
    than trust that alignment blindly it is anchored: the editor holding
    the SKU must be the Item No. column. If that doesn't hold, the
    alignment is wrong and nothing gets written.
    """
    editors = _scan_editors(ctx, rect, row_y)
    print(f"  Editable cells: {[(x, v[:18]) for x, v in editors]}")

    sku = cells[columns["ITEMNUMBER"]].strip().casefold()
    anchor = next((i for i, (_, v) in enumerate(editors) if v.strip().casefold() == sku), None)
    if anchor is None:
        raise ManualReviewRequired(
            f"None of this row's editable cells holds the Item No. {sku!r}, so "
            f"the editors cannot be lined up with the columns. Editors read: "
            f"{[v for _, v in editors]}"
        )

    # Offset from Item No., counted in copied columns but skipping any
    # copied column that has no editor of its own.
    picture = _picture_index(columns)
    target, item_idx = columns[column], columns["ITEMNUMBER"]
    step = 1 if target > item_idx else -1
    offset = 0
    for i in range(item_idx + step, target + step, step):
        if picture is not None and i == picture:
            continue
        offset += step

    index = anchor + offset
    if not 0 <= index < len(editors):
        raise ManualReviewRequired(
            f"Column {column} maps to editable cell {index}, outside the "
            f"{len(editors)} editors found on the row."
        )
    return editors[index][0]


def _set_cell(ctx: Context, x: int, y: int, bounds, value: str) -> str | None:
    """
    Write one cell through its real editor control, returning what the
    editor reads back BEFORE the edit is committed.

    That read-back is the point: it verifies at the moment of writing,
    instead of re-reading the whole row afterwards and hoping the model
    has caught up — which it repeatedly had not.
    """
    editor = _open_cell_editor(ctx, x, y, bounds)
    if editor is None:
        return None

    editor.type_keys("^a{DELETE}", pause=_KEY_PAUSE)
    editor.type_keys(escape_for_type_keys(value), with_spaces=True, pause=_KEY_PAUSE)
    back = (editor.window_text() or "").strip()

    send_keys("{ENTER}")                # commit the cell edit
    time.sleep(_COMMIT_SETTLE)
    return back


def _complete_one(
    ctx: Context, row_index: int, item: LineItem, rect, row_top: int, row_height: int
) -> None:
    """Design doc 3.13-3.16 for one line."""
    row_y = row_top + row_height * row_index + row_height // 2
    _goto_row(ctx, row_index)
    cells = _scan_row(ctx, row_index)
    print(f"  Row as read: {list(enumerate(cells))}")

    columns = _map_columns(cells, item)
    print(f"  Column map: { {k: v for k, v in sorted(columns.items(), key=lambda kv: kv[1])} }")

    _confirm_vat(item, cells, columns)

    # 3.13-3.15. VAT is deliberately not in this list — see _confirm_vat().
    wanted = {
        "QUANTITY": f"{item.quantity:g}",
        "UNITPRICE": f"{item.unit_net_price:.2f}",
        "DISCOUNT": format_percent(item.discount_pct),
    }
    for column, value in wanted.items():
        if column not in columns:
            raise ManualReviewRequired(
                f"The {column} column isn't drawn on this Order's item table, so "
                f"{item.sku!r} can't be completed. Row read:\n  {list(enumerate(cells))}"
            )
        _write_cell(ctx, row_index, item, columns, column, value, rect, row_y, cells)

    _verify_line(ctx, row_index, item, columns)


def _write_cell(
    ctx: Context, row_index: int, item: LineItem, columns: dict[str, int],
    column: str, value: str, rect, row_y: int, cells: list[str],
) -> None:
    """
    Write one cell, then immediately prove it landed where it was aimed.

    Checking per write rather than only at the end of the line is what
    turns a mis-aimed keystroke from corruption into a stop. A wrong
    Right-press count once overwrote Item No. with a quantity, and
    because nothing looked at the row until all three writes were done,
    the run carried on typing into a row whose identity had already been
    destroyed. The anchor check below catches that on the write that
    causes it.

    The retry is for the opposite failure: a write that lands correctly
    but doesn't take at all. That was seen on the first write of a line —
    Qty stayed at its default while the later writes on the same row all
    worked — which points at the cell editor not being ready rather than
    at the wrong cell, and a second attempt costs one keystroke.
    """
    x = _cell_x_for(ctx, rect, row_y, cells, columns, column)
    print(f"  Setting {column} = {value!r} at x={x}")
    back = _set_cell(ctx, x, row_y, rect, value)

    if back is None:
        raise ManualReviewRequired(
            f"{column} on line {row_index + 1} ({item.sku}) has no editable "
            f"cell — double-clicking it opened no editor control."
        )
    if _first_number(back) is None or abs(_first_number(back) - _first_number(value)) > 0.01:
        raise ManualReviewRequired(
            f"{column} on line {row_index + 1} ({item.sku}) was typed as "
            f"{value!r} but its editor read back {back!r}. The keystrokes did "
            f"not reach the cell intact, so it was not committed as intended."
        )
    print(f"  OK: {column} editor read back {back!r}.")

    # The row-level anchor check stays as a second net: the editor
    # read-back proves what went INTO the cell, not that the cell was the
    # right one. A wrong cell that accepts the value still shows up here
    # as a damaged Item No. or Name.
    _assert_anchors_intact(_scan_row(ctx, row_index), item, columns, column)


def _assert_anchors_intact(
    cells: list[str], item: LineItem, columns: dict[str, int], column: str
) -> None:
    """
    Confirm the row is still the row we think it is.

    Item No. and Name are the two cells this step must never write to;
    they are also what identifies the line. If either has changed, a
    keystroke went somewhere it shouldn't have, and every later write on
    this row would compound it.
    """
    for anchor, expected in (("ITEMNUMBER", item.sku), ("NAME", item.description)):
        index = columns.get(anchor)
        if index is None or index >= len(cells):
            continue
        if cells[index].strip().casefold() != expected.strip().casefold():
            raise ManualReviewRequired(
                f"Writing {column} overwrote the line's {anchor} cell: it should "
                f"read {expected!r} but now reads {cells[index]!r}. A keystroke "
                f"landed in the wrong column, so this row's identity is damaged "
                f"and the Order must not be saved. Row now reads:\n"
                f"  {list(enumerate(cells))}"
            )


def _cell_holds(cells: list[str], columns: dict[str, int], column: str, item: LineItem) -> bool:
    """Whether `column` now reads the value the image specifies."""
    index = columns.get(column)
    if index is None or index >= len(cells):
        return False
    raw = cells[index]
    wanted = {
        "QUANTITY": item.quantity,
        "UNITPRICE": item.unit_net_price,
        "DISCOUNT": item.discount_pct,
    }[column]
    actual = _discount_percent(raw) if column == "DISCOUNT" else _first_number(raw)
    return actual is not None and abs(actual - wanted) <= 0.01


def _confirm_vat(item: LineItem, cells: list[str], columns: dict[str, int]) -> None:
    """
    Design doc 3.14 says "set or confirm" the line's VAT — this confirms.

    The line inherits its rate from the Product master, which step 3.10
    already set to the rate the image specifies, so there is normally
    nothing to change. Confirming is also much the safer half of "set or
    confirm": VAT is a dropdown cell, and driving a NatTable combo editor
    blind (type, hope the right entry is matched, Enter) can commit a
    different rate without any error — the same class of silent wrong
    value that select_combo_option() had to be fixed for. If the
    inherited rate is ever wrong, that means the Product master is wrong,
    which is worth stopping for rather than papering over per line.
    """
    index = columns.get("VAT")
    if index is None:
        print("  (no VAT column drawn on this table — nothing to confirm)")
        return

    shown = cells[index]
    expected_pct = item.vat_pct
    name = vat_name(expected_pct)

    # The VAT cell doesn't copy as a tidy "VAT 19%". Fakturama's copy
    # handler emits the whole Vat entity's toString(), e.g.
    #   VAT taxValue: [0.19] salesEqualizationTax: [null]
    #   description: [VAT 19%] name: [VAT 19%] dateAdded: [...] id: [1] ...
    # so the rate's name is in there, along with several other numbers.
    # Matching the name as a substring is therefore the reliable test —
    # and reading "the first number in the cell" is actively wrong here,
    # since that finds taxValue's 0.19, not 19.
    if name.casefold() in shown.casefold():
        print(f"  OK: line VAT carries {name!r}.")
        return

    # Fall back to the rate itself, which the entity spells as a fraction.
    fraction = re.search(r"taxValue:\s*\[([\d.]+)\]", shown)
    if fraction and abs(float(fraction.group(1)) * 100 - expected_pct) < 0.01:
        print(f"  OK: line VAT taxValue is {fraction.group(1)} = {format_percent(expected_pct)}%.")
        return

    raise ManualReviewRequired(
        f"Line {item.sku!r} carries VAT {shown!r}, but the image says "
        f"{format_percent(expected_pct)}% ({name}). The line inherits its rate "
        f"from the Product master, so the master itself has the wrong rate — "
        f"fix that rather than overriding this one line."
    )


def _verify_line(ctx: Context, row_index: int, item: LineItem, columns: dict[str, int]) -> None:
    """
    Design doc 3.16: read the line back and check it says what the image
    said, including that Price = qty x unit net price x (1 - discount/100).

    Reading back is the whole safety net for this file. Every other screen
    gets a resolver that fails loudly when a control isn't found; here a
    keystroke that lands in the wrong cell produces no error at all, just
    a wrong number on the Order — so the row is re-read and compared
    rather than assumed.
    """
    _goto_row(ctx, row_index)
    cells = _scan_row(ctx, row_index)
    print(f"  Row after filling: {list(enumerate(cells))}")

    expected_total = round(
        item.quantity * item.unit_net_price * (1 - item.discount_pct / 100), 2
    )
    problems = []

    def cell(column: str) -> str:
        index = columns.get(column)
        return cells[index] if index is not None and index < len(cells) else ""

    for column, wanted in (
        ("QUANTITY", item.quantity),
        ("UNITPRICE", item.unit_net_price),
        ("DISCOUNT", item.discount_pct),
        ("TOTALPRICE", expected_total),
    ):
        raw = cell(column)
        actual = _discount_percent(raw) if column == "DISCOUNT" else _first_number(raw)
        if actual is None or abs(actual - wanted) > 0.01:
            problems.append(f"{column}: expected {wanted}, row shows {raw!r}")

    if problems:
        raise ManualReviewRequired(
            f"Line {row_index + 1} ({item.sku}) did not come back with the values "
            f"that were just written:\n  " + "\n  ".join(problems) + "\n"
            f"Full row: {list(enumerate(cells))}\n"
            f"A keystroke landing in the wrong cell is silent, so this is the only "
            f"place it can be caught — the Order must not be saved like this."
        )

    print(
        f"  OK: line {row_index + 1} reads qty {item.quantity:g} @ "
        f"{item.unit_net_price:.2f}, {format_percent(item.discount_pct)}% discount, "
        f"total {expected_total:.2f}."
    )


def _discount_percent(text: str) -> float | None:
    """
    Read a line discount back as a PERCENTAGE.

    The cell does not hold what was typed into it. Typing "10" produces a
    stored value of -0.1: Fakturama keeps a line discount as a negative
    fraction, and the copy handler emits the stored value rather than the
    rendered one. Confirmed on the live table — writing "10" gave a cell
    of "-0.1" and moved the line total from 250 to 225, which is exactly
    10% off.

    So the sign is dropped and a magnitude of 1 or less is read as a
    fraction. The one genuinely ambiguous input is a value of exactly 1,
    which could mean 1% or 100%; it is read as 100%, matching the stored
    form. No order in this flow discounts a line by 1%, and a 100%
    discount would be visible in the totals check either way.
    """
    value = _first_number(text)
    if value is None:
        return None
    value = abs(value)
    return value * 100 if value <= 1 else value


def _first_number(text: str) -> float | None:
    """
    Pull a number out of a rendered cell, tolerating the locale and
    currency decoration Fakturama draws ("2", "250,00 €", "10 %").
    """
    match = re.search(r"-?\d+(?:[.,]\d+)?", text.replace(" ", " "))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))
