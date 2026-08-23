# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop-automation prototype that turns a single order image into a fully saved Order + Debtor + linked Invoice inside **Fakturama** (a Java/SWT Eclipse RCP invoicing app), using Gemini for image extraction and Microsoft UI Automation (via `pywinauto`) for driving the UI. See `README.md` for setup and how to run it, `TODOs.md` for the step-by-step implementation checklist (what's done vs. pending), and `ARCHITECTURE.md` for a full module-by-module walkthrough of the main flow — read `ARCHITECTURE.md` before making non-trivial changes to `entry_point.py` or `workflow/`.

## Commands

Dependency management is via `uv` (see `pyproject.toml` / `uv.lock`).

```bash
uv sync                                    # install/sync dependencies into .venv
```

Requires a `.env` file at the repo root with `GEMINI_API_KEY=...` (see `.env.example`).

Run the main automation flow (launches Fakturama unless `--attach` is given, extracts order data from the image, and drives the New Order → Debtor resolution/creation flow):

```bash
uv run entry_point.py --image path/to/order.jpg [--attach] [--path "C:\...\Fakturama.exe"] [--force-extract] [--test-payment-method "Credit Card"] [--dump-ui]
```

- `--attach` — attach to an already-running Fakturama instead of launching a new one.
- `--no-kill-existing` — skip the startup cleanup. By default, launching first kills any leftover Fakturama (via `psutil`) so the run starts from a clean main window rather than someone's half-open editor. `--attach` never kills anything.
- `--force-extract` — bypass the cached `<image>.extraction.json` and call Gemini again.
- `--test-payment-method` — override the extracted Payment Method, useful for forcing the "not found, must create" branch on demand.
- `--dump-ui` — dump each screen's labels/fields as it opens (for adding a `FieldSpec` to a screen that hasn't been inspected yet).
- `--log-file [PATH]` — tee everything the run prints (screen dumps, the resolver's lookup failures, fill summaries) to a UTF-8 file, defaulting to `last_run.log`. Prefer this over a shell redirect: PowerShell's `>` writes UTF-16 with a BOM, which is why the original `run_log.txt` has to be decoded before it can be searched.

Dump one screen without re-running the whole flow — attach to a Fakturama that's already open on the screen of interest (a run that stopped for manual review leaves it open):

```bash
uv run python dump_screen.py                              # list the open editor tabs
uv run python dump_screen.py "New product" --edits --out  # dump one tab to screen_dump.log
uv run python dump_screen.py --text "price"               # find controls by visible text
```

Exit codes: `0` success, `1` automation/extraction failure, `2` stopped for manual review.

Run extraction alone (prints the extracted JSON, no UI automation):

```bash
uv run python -m utils.extraction.run_extraction path/to/order.jpg
```

(Run via `-m` from the repo root, not as a direct script path — `utils/extraction/run_extraction.py` uses an absolute `from utils.extraction... import` that only resolves when the repo root is on `sys.path`.)

There is no test suite or linter configured in this repo.

## Architecture

Four layers, deliberately kept independent of each other:

- **`utils/extraction/`** — turns an order image into a validated `OrderExtraction` object (`schema.py` defines the Pydantic models; `gemini_extract.py` calls Gemini with structured-output mode and caches the result to `<image>.extraction.json` next to the image, since re-extracting on every UI-automation debug run is slow/wasteful/non-deterministic). Knows nothing about Fakturama.
- **`utils/automation/`** — a generic UI-automation engine that knows nothing about Fakturama's business flow. `locators.py` defines `FieldSpec`/`Strategy` (the vocabulary for describing how to find a field: `BY_TITLE`, `SIBLING_OF_LABEL`, or the unimplemented `VISION_FALLBACK`). `field_specs.py` holds the actual filled-in `FieldSpec` data for every screen inspected so far — pure data, no logic, with each entry commented on *how* it was confirmed against the live app; it stays **central**, one dict per screen, rather than being split across the step modules. `resolver.py` resolves and drives individual controls (`find_field()`, `type_into()`, `set_field_value()`, `select_combo_option()`, `fill_fields()`, `search()`, plus diagnostic helpers); `grids.py` reads/selects search-result rows; `editors.py` opens/switches/saves/closes Eclipse editor tabs; `windows.py` finds window handles via plain Win32; `processes.py` finds and shuts down processes by executable name.
- **`workflow/`** — the business flow, one module per numbered step (`open_order.py`, `debtor.py`, `payment_method.py`), each exposing a single entry function taking the shared `Context` (`context.py`) and handling its own internal branching. Steps that hit a design-doc "a human decides this" case raise `ManualReviewRequired` rather than returning a status string.
- **`entry_point.py`** — the orchestrator: argument parsing, extraction, connect, then the steps in order. `run()` is a flat sequence; any branching belongs inside a step module. Adding a step = one new file in `workflow/` + one line here.

Every import is absolute from the repo root (`from utils.automation.resolver import ...`, `from workflow.context import ...`). `entry_point.py` sits at the root, so running it already puts the root on `sys.path`; it re-inserts it explicitly anyway so the imports resolve however the script is invoked (absolute path from another directory, a wrapper, an IDE run config).

### Hard-won lessons baked into the code (don't rediscover these)

- **Never use `auto_id` as a lookup key.** Confirmed empirically to change across app restarts. Every `FieldSpec` is built only from `title`, `control_type`, and structural position.
- **UIA calls can hang past their own stated timeout** while this Eclipse/SWT app is still busy (booting, or a dialog still settling right after it opens). The fix used throughout: find the window/dialog's handle via plain Win32 (`EnumWindows`/`GetWindowThreadProcessId`) first — not UIA — confirm it's actually responsive (`IsHungAppWindow`), and only then hand the handle to `pywinauto`'s `Application(backend="uia").connect(handle=...)`. See `find_hwnd`/`wait_until_responsive` in `utils/automation/windows.py`.
- **`.set_edit_text()` (UIA's `ValuePattern.SetValue`) does not reliably persist values into this app's data model**, even for plain top-level `Edit` controls — it can look filled during the session and simply not be there after Save. `type_into()` in `resolver.py` — the single primitive behind `set_field_value()`, `fill_fields()` and `search()` — always drives real synthetic keystrokes (click, select-all, type, Tab) instead.
- **Eclipse renames a tab's title after Save** (e.g. `"*New Debtor"` → the saved contact's name), which breaks any later `title_re`-based re-search for that tab. Editor panes and their tab headers are resolved to concrete wrapper objects **once**, at open time, and that same reference is reused for the rest of the run (see `Editor`/`open_editor` in `utils/automation/editors.py`, which captures a tab's content pane *and* its clickable header at open time and stashes them in `ctx.editors[name]`, plus the resolver's `.descendants()`-based lookup, which works on both a lazy `WindowSpecification` and an already-resolved wrapper).
- **Some SWT ComboBox dropdowns render their popup as a separate element not nested under the combo itself**, which breaks pywinauto's built-in `.select()` item search even when the item is visibly present. `select_combo_option()` falls back to manually expanding and searching the whole top-level window for a matching `ListItem`.
- **The "Select the address" dialog's results grid is not exposed via UIA at all** — a custom-painted SWT Table with zero row-level accessibility (confirmed via full tree dumps with a real, visibly-rendered matching row present). Only the search box and OK/Cancel buttons on that dialog are UIA-visible. It's read instead by clipboard walk (`clipboard_grid.py`): land on each row with a click plus the Down key, `Ctrl+C`, read the clipboard, compare. That means coordinates, so `SELECT_ADDRESS_GRID` in `field_specs.py` holds the measured row geometry — re-measure it with `coors.py` (click row 1, then row 2; the reported delta-y is the row height) if the theme, DPI or font ever changes. Two safeguards keep bad geometry from becoming bad data: a computed point outside the dialog is never clicked, and the chosen row is read back and compared before OK. `grids.py` remains the UIA path for grids that *do* expose rows, like terms-of-payment.
- **`Ctrl+C` on an EMPTY grid throws a modal "Internal Error" dialog** (`java.lang.NullPointerException: Cannot read the array length because "this.copiedCells" is null` — the copy handler assumes a selection exists). An empty grid is a completely ordinary outcome (the record doesn't exist yet, so the flow goes on to create it), so `read_selected_row()` dismisses the popup with Enter and reports "no row" rather than letting a modal block every step that follows. Any new clipboard-driven screen needs the same treatment.
- **Create new master records from the relevant list view's green `+`, never from the left navigator's "New X" link.** The navigator link resolves cleanly by title and clicking it opens nothing at all — no editor, no error, no new tab. The reason is in the shipped e4 model (`Application.e4xmi` inside `com.sebulli.fakturama.rcp_2.2.0.jar`): each list toolbar's `+` (`com.sebulli.fakturama.listview.<type>.add`) passes `org.fakturama.rcp.forcenew=true` to the open-editor command, and the navigator's New-group action doesn't — so the navigator action targets the current selection and no-ops when there is none. Confirmed on Products; terms of payment and VATs already used the list route and work. Those `+` tool items carry no label, so their `FieldSpec` title is the **tooltip** (`Create a new product`, `Create a new tax rate`, `Create a new term of payment`).
- **Fakturama's own resource bundle is a legitimate grounding source for label text**, and much cheaper than a guess-and-rerun cycle. `OSGI-INF/l10n/bundle.properties` inside the `rcp` jar holds every English UI string, and each editor class's constant pool names the message keys it actually references — so reading `VatEditor`'s constants and resolving them gives `editor.vat.header = "New TAX Rate"` (not the "New VAT" a guess produces) for its tab title. The same method reproduces the two tab titles already verified against the running app (`New Debtor` via `command.new.debtor.name`, `New Term of Payment` via `main.menu.new.payment`). It settles *text* only — a control's type and position still need a live `--dump-ui`.
- **Every value typed through `send_keys`/`type_keys` must be escaped first** — `type_into()` runs it through `escape_for_type_keys()`. pywinauto reads `+ ^ %` as the Shift/Ctrl/Alt modifiers, `~` as Enter, `( )` as grouping and `{ }` as key-code delimiters, so real extracted data gets silently mangled: `"VAT 19%"` typed as `VAT 19` plus a bare Alt press (creating the rate under the wrong name, so the Product's VAT dropdown then offered `VAT 19` while the run searched for `VAT 19%`), and the sample order's phone `"+49 30 5550 1420"` typed as `$(`… because `+` is Shift. Verify any change here against `pywinauto.keyboard.parse_keys` — escaped text must yield exactly one keystroke per character and no modifier presses. Corollary: a percent-formatted numeric field wants a bare `"0"` / `"19"`, since the field renders its own `%` and the old code was only ever sending the bare number anyway.
- **The "Select a product" dialog can close itself, having already done the job.** Fakturama's `DOCUMENT_IMMEDIATELY_OVERTAKE_ITEMNUMBER_FROM_PRODUCTS_DIALOG` preference ("immediately take over a clearly found item number") is `true` in this install (`~/.fakturama2/.metadata/.plugins/org.eclipse.core.runtime/.settings/com.sebulli.fakturama.rcp.prefs`), so a search that unambiguously identifies one product is accepted the moment it's typed — the product lands on the Order and the dialog is destroyed before there's any row to walk or OK to click. `_select_matching_product()` checks `is_window()` after the search and reports that as a successful selection. Getting this wrong is expensive in both directions: a raw crash on the dead handle, or — worse — reading "dialog gone" as "no match" and creating a duplicate of a product that's already on the Order. Note this fires only on the app's own narrower "clearly found" test; partial or multi-row hits still leave the dialog open.
- **No Fakturama grid can be read through UIA — the list views included.** `grids.py`'s `find_matching_rows()` returns `[]` for the VATs list even with a matching row plainly on screen (`No Table control found ... treating as zero results`), because these are all Nebula NatTables. This is far more dangerous than it looks: callers spell "the record doesn't exist" as "zero rows", so a read that always fails doesn't surface as an error — it silently authorises creating a duplicate. It produced two identical `VAT 19%` rates in one run, one per line item. **Ask the combo that will consume the record instead** (`try_select_vat`, `try_select_payment_method`), which is UIA-visible and is also what the design doc means by using the selectors as the existence checks.
- **`select_combo_option()` must never return without having selected something.** It used to catch `.select()`'s failure, log "clicking the item instead", and then return — never clicking, never raising. `fill_fields()` reads a clean return as success, so the log said `OK: 'vat' set to 'VAT 19%'` while the Product saved with Fakturama's default Tax-free rate. If a combo value doesn't stick, check this function before suspecting the widget.
- **The Order's Items table is a Nebula NatTable and has no UIA cells at all** (`DocumentEditor` → `DocumentItemListTable.getNatTable()`). No `FieldSpec` can address a cell in it, however it's written, so per-line values (design doc 3.13-3.16) can't be set the way every other field in this bot is. `list_open_tabs()` output around it is also misleading: the Order editor has *inner* tabs (`Invoice address`, `Delivery address`) that are `Tab` controls too, so they show up alongside real editor tabs.
- Saving/closing editors is done via `Ctrl+S`/`Ctrl+W` sent to the main window (`save_current_editor`/`close_current_tab`), not by hunting for toolbar buttons — simpler and doesn't depend on an unverified button title/icon lookup.

## Adding automation for a new screen or field

This is the workflow every existing `FieldSpec` in `field_specs.py` was built with. Follow it in order — every shortcut skipped here in earlier work caused a wrong guess that had to be redone.

1. **Get to the real screen first, then inspect — never guess a control's shape.** Run `entry_point.py --dump-ui` (with `--attach` if Fakturama's already open) up to the point where the new screen/dialog is visible, then dump it — `--dump-ui` calls `dump_labels_and_fields()` on each screen as it opens; for anything more specific, add a temporary call from the step module:
   - `dump_labels_and_fields(scope)` — prints every `Text`/`Edit`/`ComboBox`/`Pane`/`Button` descendant with its title and rectangle. Pass a wider `control_types` tuple (e.g. add `"Image"`, `"TabItem"`, `"Table"`) if the control you want isn't one of the defaults.
   - `find_controls_by_text(scope, "some visible label")` — when you know the label text but not its `control_type` (icon buttons often turn out to be `Image`, not `Button`).
   - `list_open_tabs(scope)` (in `utils/automation/editors.py`) — when a `title_re` guess for a newly-opened tab doesn't match; Eclipse renames tabs after Save, and titles aren't always what you'd assume. `open_editor()` already prints this for you when it fails.
   - `scope.print_control_identifiers(depth=N)` for a full raw dump when the above aren't enough.
2. **Pick a `Strategy` based on what the dump actually shows**, not what seems likely:
   - Control has its own non-empty `title` → `BY_TITLE`.
   - Control's `title` is empty but a `Text` label sits in the same parent, earlier in tree order → `SIBLING_OF_LABEL` (this walks the label's own parent's children and takes the first match of the target `control_type` *after* the label — necessary because a parent can hold several label+field pairs side by side).
   - Control has no title and no nearby label (icon-only, or the label belongs to a different field) → `VISION_FALLBACK` with a precise `vision_description`, or, if the field needs to actually work today, a bespoke one-off in the step module (see the composite Pane-splitting code in `workflow/debtor.py`'s `_fill_main_address` for a precedent).
3. **Add the `FieldSpec` to the relevant dict in `field_specs.py`**, with a comment stating *how* it was confirmed (which dump, what it showed) — every existing entry does this so a future read never has to re-derive whether a spec is a guess or a verified fact.
4. **Write the fill/click logic in the relevant `workflow/` module** (a new one if it's a new step — then add one call to `run()` in `entry_point.py`). Use `fill_fields(scope, SPECS, values)` for anything multi-field: it resolves each spec, picks combo-vs-text off the spec, and returns a `{field_name: bool}` dict without raising, so one bad field doesn't abort the rest. Drop to `find_field()` + `type_into()`/`select_combo_option()`/`.click_input()` only for one-offs like composite panes. A situation the design doc reserves for a human → `raise ManualReviewRequired(...)`, never a status string.
5. **Run it for real and read the resolver's own failure output before changing anything.** `find_field()` is self-diagnosing on failure: a `SIBLING_OF_LABEL` miss logs the actual nearby siblings, and a `select_combo_option()` miss logs the real error plus (via its manual-expand fallback) the visible `ListItem` texts it found. Fix based on that output, not a second guess.
6. **If a value looks set during the session but isn't saved/found afterward**, suspect the `.set_edit_text()` persistence issue (see above) before anything else — route the field through `set_field_value()`/`type_into()` rather than a raw UIA value-set.
7. **If a screen/dialog you just opened isn't responding to UIA calls (hangs past a stated timeout)**, don't add a longer timeout — use the Win32-handle-first pattern (`find_hwnd(pid=..., title=...)` + `wait_until_responsive`) instead, same as the existing dialogs.
8. **If a lookup fails only on a screen you switched away from and back to**, the tab isn't active — call `ctx.editor("name").switch_to()` first. Controls in a non-active Eclipse tab don't report as visible to UIA, and switching also resets an editor's own sub-tab back to its first page.
