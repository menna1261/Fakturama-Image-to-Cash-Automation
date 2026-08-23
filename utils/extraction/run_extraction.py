"""
CLI: extract structured data from an order image and print the result.

Usage:
    uv run python -m utils.extraction.run_extraction path/to/order_image.png

Run via `-m` from the repo root, not as a direct script path: the
absolute `from utils.extraction... import` below only resolves when the
repo root is on sys.path.
"""

import argparse
import json
import sys

from utils.extraction.gemini_extract import extract_order_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path", help="Path to the order image (png/jpg)")
    args = parser.parse_args()

    try:
        result = extract_order_data(args.image_path)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()

