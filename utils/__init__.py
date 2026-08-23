"""
Reusable machinery, independent of Fakturama's business flow.

- `automation/` — the UI-automation engine: finding and driving controls,
  editor tabs, result grids, windows and processes. Knows nothing about
  what a "Debtor" or a "Payment Method" is.
- `extraction/` — turning an order image into a validated
  OrderExtraction. Knows nothing about Fakturama.

Neither imports the other, and neither imports `workflow/`.
"""
