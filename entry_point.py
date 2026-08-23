"""
Image-to-cash bot: turn one order image into a saved Order + Debtor
(+ linked Invoice, once steps 5-8 land) inside Fakturama.

This file is the orchestrator and nothing else — argument parsing,
extraction, connecting, then the numbered steps in order. All the actual
screen-driving lives in workflow/, and the generic machinery in utils/.
Keep main() a flat sequence: any branching a step needs belongs inside
that step's module.

Usage:
    uv run entry_point.py --image path/to/order.jpg [--attach]
"""

import argparse
import sys
from pathlib import Path

# entry_point.py sits at the repo root, so running it directly already puts the
# root on sys.path and `utils` / `workflow` resolve. Re-adding it
# explicitly keeps that true no matter how the script is invoked (an
# absolute path from another directory, a wrapper, an IDE run config).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pywinauto.findwindows import ElementNotFoundError  # noqa: E402
from pywinauto.timings import TimeoutError as PWATimeoutError  # noqa: E402

from utils.automation.editors import EditorNotOpenedError  # noqa: E402
from utils.automation.resolver import AmbiguousOptionError, FieldNotFoundError  # noqa: E402
from utils.extraction.gemini_extract import extract_order_data_cached  # noqa: E402
from utils.run_log import tee_stdout  # noqa: E402
from workflow.context import ManualReviewRequired, connect  # noqa: E402
from workflow.debtor import resolve_debtor  # noqa: E402
from workflow.invoice import complete_invoice  # noqa: E402
from workflow.open_order import open_order  # noqa: E402
from workflow.product import resolve_products  # noqa: E402
from workflow.save_order import save_order  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MANUAL_REVIEW = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Path to the order image to extract data from (png/jpg)",
    )
    parser.add_argument(
        "--path",
        default=r"C:\Program Files\Fakturama2\Fakturama.exe",
        help="Path to Fakturama.exe (ignored if --attach is used)",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach to an already-running Fakturama instance instead of launching",
    )
    parser.add_argument(
        "--no-kill-existing",
        action="store_true",
        help=(
            "Don't close leftover Fakturama processes before launching. By "
            "default any already-running instance is asked to close (then "
            "force-killed if it won't), so the run starts from a clean main "
            "window. Ignored with --attach, which never closes anything."
        ),
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Bypass the cached extraction JSON and call Gemini again",
    )
    parser.add_argument(
        "--test-payment-method",
        default=None,
        help=(
            "Override the extracted Payment Method with this value instead — "
            "useful for forcing the creation branch (2.10.1+) on demand, since "
            "the extracted method may already exist."
        ),
    )
    parser.add_argument(
        "--fill-line-items",
        action="store_true",
        help=(
            "Set each line's quantity, unit price, VAT and discount on the Order "
            "(design doc 3.13-3.17). Off by default: it drives the Order's "
            "NatTable by coordinate, opening a cell editor per cell, which is the "
            "least stable part of the flow. Without it the products are still put "
            "on the Order, at Fakturama's default quantities."
        ),
    )
    parser.add_argument(
        "--dump-ui",
        action="store_true",
        help=(
            "Dump each screen's labels/fields as it opens. Use when adding a "
            "FieldSpec for a control that hasn't been inspected yet."
        ),
    )
    parser.add_argument(
        "--log-file",
        nargs="?",
        const="last_run.log",
        default=None,
        help=(
            "Also write everything this run prints — screen dumps, the "
            "resolver's lookup failures, the field-fill summaries — to a "
            "UTF-8 file, so it can be read after the fact instead of scrolled "
            "back through. Defaults to last_run.log when given no value."
        ),
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    """The whole flow. Each step handles its own branching internally."""
    print(f"Extracting order data from: {args.image}")
    extraction = extract_order_data_cached(args.image, force=args.force_extract)
    print(
        f"Extracted: external_reference={extraction.external_reference!r} "
        f"order_date={extraction.order_date!r}"
    )

    ctx = connect(
        extraction,
        exe_path=args.path,
        attach=args.attach,
        kill_existing=not args.no_kill_existing,
        payment_method_override=args.test_payment_method,
        dump_ui=args.dump_ui,
        fill_line_items=args.fill_line_items,
    )

    open_order(ctx)
    print(f"Debtor outcome: {resolve_debtor(ctx).name}")
    print(f"Product outcomes: {[o.name for o in resolve_products(ctx)]}")
    save_order(ctx)
    complete_invoice(ctx)


def main() -> int:
    args = parse_args()
    with tee_stdout(args.log_file) as log_path:
        if log_path:
            print(f"Logging this run to: {log_path}")
        return _run_guarded(args)


def _run_guarded(args: argparse.Namespace) -> int:
    """run(), with every expected failure turned into an exit code."""
    try:
        run(args)
    except (ManualReviewRequired, AmbiguousOptionError) as e:
        # Every "the design doc says a human decides this" case funnels
        # here, so no branch can quietly let a run report false success.
        print("=" * 80)
        print(f"STOPPED FOR MANUAL REVIEW: {e}")
        print("=" * 80)
        return EXIT_MANUAL_REVIEW
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"ERROR: extraction or startup failed: {e}")
        return EXIT_ERROR
    except EditorNotOpenedError as e:
        print(f"ERROR: an expected editor did not open: {e}")
        return EXIT_ERROR
    except ElementNotFoundError as e:
        print(f"ERROR: could not find expected window/control: {e}")
        return EXIT_ERROR
    except (PWATimeoutError, TimeoutError) as e:
        print(f"ERROR: timed out waiting for a control to be ready: {e}")
        return EXIT_ERROR
    except FieldNotFoundError as e:
        print(f"ERROR: {e}")
        return EXIT_ERROR

    print("DONE.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
