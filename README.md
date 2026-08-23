# Fakturama Image-to-Cash Automation

Turns a photograph of a purchase order into a saved Order, Debtor and linked
Invoice inside **Fakturama 2.x** — a Java/SWT Eclipse RCP invoicing application
on Windows.

Google Gemini reads the image into structured data; Microsoft UI Automation
(via `pywinauto`) drives the desktop UI. The extraction is the straightforward
half. Most of the engineering is in making the automation deterministic against
an application whose accessibility layer is incomplete.

## Demo :
https://drive.google.com/file/d/1stgUjiq4RTdu3CWMv9-f99yX_o9TfLUa/view?usp=sharing



## Requirements

| | |
|---|---|
| OS | Windows 10/11 — the automation is Win32/UIA, it does not run elsewhere |
| Python | 3.12 |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Fakturama | 2.x, installed (default `C:\Program Files\Fakturama2\Fakturama.exe`) |
| API key | A Google Gemini API key |

Dependencies (`pyproject.toml`): `google-genai`, `pydantic`, `python-dotenv`,
`pywinauto`, `psutil`.

## Setup

```bash
git clone <this repo>
cd Fakturama-Image-to-Cash-Automation
uv sync                      # creates .venv and installs everything
```

Create a `.env` file in the repo root (see `.env.example`):

```
GEMINI_API_KEY=your-key-here
```

Then start Fakturama once by hand and let it finish its first-run setup — it
asks where to keep its data directory and runs database migrations. The bot
expects an installed, initialised Fakturama; it won't get through the setup
wizard for you.

## Running it

```bash
uv run entry_point.py --image sample_order.jpg
```

That launches Fakturama, extracts the order from the image, and drives the flow
end to end. A sample image and its cached extraction are in the repo, so this
works out of the box.

### Options

| Flag | What it does |
|---|---|
| `--image PATH` | **Required.** The order image (png/jpg) |
| `--path PATH` | Path to `Fakturama.exe` (default: the standard install location) |
| `--attach` | Drive an already-running Fakturama instead of launching one |
| `--no-kill-existing` | Skip the startup cleanup. By default a launch first closes any leftover Fakturama, so the run starts from a clean main window |
| `--force-extract` | Ignore the cached extraction and call Gemini again |
| `--test-payment-method "Credit Card"` | Override the extracted payment method — useful for forcing the "must create it" branch on demand |
| `--fill-line-items` | Also set each line's quantity, price, VAT and discount (off by default — see [Known gaps](#known-gaps)) |
| `--dump-ui` | Print every screen's controls as it opens. For adding automation to a screen that hasn't been inspected yet |
| `--log-file [PATH]` | Tee everything the run prints to a UTF-8 file (default `last_run.log`) |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Automation or extraction failure |
| `2` | **Stopped for manual review** — an ambiguous match or a case the spec reserves for a human |

`2` is not a crash. It means the run found something it must not decide on its
own — two contacts matching the same company, two payment methods with the same
name — and halted rather than guessing. The message says exactly what it found.

### What a run does

1. Extract the order from the image (cached to `<image>.extraction.json`).
2. Close any leftover Fakturama, launch a fresh one, wait for it to be
   responsive, maximize it.
3. Open a New Order and fill its header — date, customer reference, Net price
   mode, With VAT.
4. Resolve the **Debtor**: search existing contacts; select the single exact
   match, or create the contact (including creating its payment method if
   Fakturama doesn't have it yet), then re-search to prove the save persisted.
5. Resolve each **Product**: search by SKU; select it, or create it — creating
   the required VAT rate first if it's missing.
6. Save the Order, then create and complete the linked **Invoice**.

## Other entry points

```bash
# Extraction alone — prints the JSON, touches no UI
uv run python -m utils.extraction.run_extraction sample_order.jpg

# Inspect a screen that's already open (a run stopped for review leaves one up)
uv run python dump_screen.py                              # list open editor tabs
uv run python dump_screen.py "New product" --edits --out  # dump one tab to a file
uv run python dump_screen.py --text "price"               # find controls by text

# Measure screen coordinates with the mouse — for the one grid that has to be
# driven by coordinate. Click row 1 then row 2; the reported delta is the row height.
uv run coors.py
```

## Project layout

```
entry_point.py       the orchestrator — arg parsing, then the steps in order
utils/
  extraction/        image -> validated data (Gemini + Pydantic). Knows nothing about Fakturama
  automation/        the UI engine: locators, resolver, grids, editors, windows, processes
workflow/            one module per step of the business flow
```

Dependencies point one way only: `entry_point.py` → `workflow/` → `utils/`.
Nothing in `utils/` imports `workflow/`, and the two `utils` packages never
import each other. A UI change touches one data file; an image-format change
touches the schema; a process change touches one file in `workflow/`.

Full walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md).
Step-by-step status: [TODOs.md](TODOs.md).

## Known gaps

Stated plainly rather than left to be discovered:

- **Line-item values are not set by default.** Products land on the Order at
  Fakturama's default quantity and price. The Order's Items table is a Nebula
  NatTable with no UIA cells at all, so those fields have to be driven by
  screen coordinate through a cell editor — the least stable thing in the
  codebase. It's implemented behind `--fill-line-items` but currently fails
  while calibrating row geometry.
- **No automated tests, no linter.**
- **The vision-based locator fallback is an extension point, not an
  implementation.** `Strategy.VISION_FALLBACK` raises `NotImplementedError`.
- **The "Select the address" grid is driven by measured coordinates.** Its rows
  have no UIA representation at all. The geometry is stored as data and guarded
  two ways — a point outside the dialog is never clicked, and the chosen row is
  read back before OK — but a theme, font or DPI change means re-measuring with
  `coors.py`.
- Several verification substeps in [TODOs.md](TODOs.md) are unticked, mostly
  around confirming copied values on the Invoice.

---

## If I had 3 more hours

I'd spend all three finishing the project rather than polishing what already
works — closing out the remaining checklist sections, in the order their
dependencies demand. In priority order, with why each earns its place.


### 1. Complete and save the Order — checklist §6 (~30 min)

Everything above this step exists to make this one correct, and it is the step
that can't be trusted until item 1 lands: with line values left at Fakturama's
defaults, the Order's totals cannot match the source image, so the confirmation
that matters most has nothing to check yet.

`workflow/save_order.py` already confirms the order-level values and the totals
before saving. What's missing is the after-the-fact proof: open **Data >
Documents** and confirm exactly one Order row with the right number, date,
customer reference, **open** state and total. Saving without reading the record
back is the same mistake the Debtor step already learned not to make — this
application will accept input it does not persist.

### 2. Complete and verify the linked Invoice — checklist §8 (~45 min)

`workflow/invoice.py` creates the follow-up Invoice, checks the totals copied
across, and applies the paid/unpaid status. Three things are still open:

- **Set the Invoice's payment method** from the extraction, and stop for manual
  review if it isn't offered — the one branch of §9's manual-review list that
  isn't handled anywhere yet.
- **Confirm what actually copied over** from the Order: customer reference,
  invoice and delivery addresses, order date, VAT mode, the item lines.
- **Verify from the Documents list** that the Invoice row shows the expected
  state and total, and that the source Order still shows its own — then stop,
  creating no Delivery, Correction or Dunning documents.

The reopen-and-re-confirm pass on the saved Invoice is the last item, and the
first thing I'd drop if the three hours ran out.

### 3. derive the address grid's geometry instead of measuring it (~30 min)

The clipboard walk needs to know where row 1 is. That number is currently
measured by hand and stored as data, so it goes stale on any theme or DPI
change.

It doesn't have to be. The dialog's **search box is UIA-visible** even though
its rows aren't — so row 1 can be anchored to the bottom edge of that box's
rectangle, and the row height derived from the difference between two rows read
by keyboard rather than assumed. The measured constants stay as a fallback. That
turns the most brittle part of the system into something self-calibrating.


