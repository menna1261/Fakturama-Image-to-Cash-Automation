"""
Extract structured order data from a single order image using Gemini.

Uses Gemini's structured-output mode (response_schema=OrderExtraction)
so the model is forced to return JSON matching our schema exactly —
no manual JSON parsing/repair needed on our end.
"""

import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from utils.extraction.schema import OrderExtraction

MODEL = "gemini-3.6-flash"

EXTRACTION_PROMPT = """\
You are extracting structured data from a single scanned/photographed sales order image.

Extract exactly what is shown on the image — do not invent, guess, or fill in
plausible-looking values for anything not actually visible. If a field is not
present on the image, use an empty string (for text) or 0 (for numbers), and
for payment_date use null unless a payment date is explicitly shown.

Normalize as you extract:
- All dates -> ISO format YYYY-MM-DD.
- All monetary/numeric values -> plain numbers (no currency symbols, no thousands separators).
- Trim leading/trailing whitespace from all text fields.
- Keep ZIP/postal codes as strings (leading zeros matter).

If billing and delivery addresses are identical or only one address is shown,
use the same address for both billing_address and delivery_address.

Extract every line item shown, in the order they appear on the image.
"""


def _load_api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Put it in a .env file at the repo "
            "root (GEMINI_API_KEY=...) or set it as an environment variable."
        )
    return api_key


def extract_order_data(image_path: str | Path) -> OrderExtraction:
    """
    Send the order image to Gemini and return a validated OrderExtraction.

    Raises RuntimeError if GEMINI_API_KEY is missing, or ValueError if the
    model's response doesn't validate against the schema (pydantic error
    propagates as-is so the caller sees exactly what didn't match).
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"Order image not found: {image_path}")

    api_key = _load_api_key()
    client = genai.Client(api_key=api_key)

    mime_type, _ = mimetypes.guess_type(image_path.name)
    if mime_type is None:
        raise ValueError(f"Could not determine image MIME type for: {image_path.name}")

    image_part = types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type)

    response = client.models.generate_content(
        model=MODEL,
        contents=[image_part, EXTRACTION_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=OrderExtraction,
        ),
    )

    if response.parsed is None:
        raise ValueError(f"Gemini response did not match OrderExtraction schema: {response.text}")

    return response.parsed


def _cache_path_for(image_path: Path) -> Path:
    return image_path.with_suffix(image_path.suffix + ".extraction.json")


def extract_order_data_cached(image_path: str | Path, force: bool = False) -> OrderExtraction:
    """
    Like extract_order_data(), but caches the result to
    "<image_path>.extraction.json" next to the image and reuses it on
    later calls instead of hitting the Gemini API again.

    We call this repeatedly while iterating on the UI-automation side
    (re-running the script to debug field-filling logic) — the image
    itself doesn't change between those runs, so re-extracting every
    time is wasted latency/cost and, since LLM output isn't perfectly
    deterministic, a needless source of run-to-run variance while
    debugging something unrelated to extraction.

    Pass force=True to bypass the cache and re-extract (e.g. after
    changing the prompt/schema, or if the cached result looks wrong).
    """
    image_path = Path(image_path)
    cache_path = _cache_path_for(image_path)

    if cache_path.exists() and not force:
        print(f"Using cached extraction: {cache_path}")
        return OrderExtraction.model_validate_json(cache_path.read_text(encoding="utf-8"))

    result = extract_order_data(image_path)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"Cached extraction to: {cache_path}")
    return result
