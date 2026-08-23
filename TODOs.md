# Fakturama-Image-to-Cash-Automation

# Implementation Checklist :

## 1. Image Extraction
- [x] Load the source order image
- [x] Extract order-level fields: Order Date, External Reference
- [x] Extract debtor fields: Company, Contact/First Name/Last Name, Alias, Billing address, Delivery address, Email, Phone
- [x] Extract payment fields: Payment Method, Paid Status, Payment Date
- [x] Extract line items (repeatable): SKU, Description, Qty, Unit net price, VAT %, Discount, Line total
- [x] Extract order totals: Net Total, VAT Total, Gross Total
- [x] Normalize values (dates → consistent format, currency/numbers → floats, trim whitespace)
- [ ] Basic validation: totals reconcile with line items; flag/log mismatches
- [x] Output extracted data as a structured object (e.g., JSON) before automation starts, for debuggability

## 2. UI Automation - Core Infrastructure
- [x] Launch/attach to Fakturama app window
- [x] Generic "find control by name/type" helper (wraps UIA lookups, with retries/waits for stabilization)
- [x] Generic "click control" / "set text field" / "select dropdown option" helpers
- [x] Wait/retry logic for dialogs and panels to load (no fixed `sleep()` where avoidable)
- [x] Error handling: distinguish "control not found" vs "ambiguous match" vs "success"
- [x] Logging of every action taken (for the annotated screenshots/recording deliverable)

## 3. Open New Order
- [x] Click "Order" in top toolbar, wait for New Order editor
- [x] Leave auto-proposed **No.** unchanged
- [x] Set **Date** to extracted Order Date
- [x] Set **Cust.Ref.** to extracted External Reference
- [x] Set document price mode to **Net**, keep VAT as **With VAT**
- [x] Keep this Order tab/editor open for all subsequent steps

## 4. Debtor Resolution
- [x] Click existing-contact icon beside Addresses (NOT the green + icon)
- [x] Search "Select the address" by extracted Company/customer name
- [x] Exact-match logic: Company + First Name + Name + ZIP + City all match
  - [x] One exact match → select, click OK
  - [x] Conflicting/ambiguous matches → stop for manual review (log + halt gracefully)
  - [x] No match → Cancel, proceed to creation branch
- [ ] On selection: verify populated Invoice/Delivery address match source image
- [ ] **Creation branch:**
  - [x] Click "New Contact", wait for New Debtor editor
  - [x] Leave proposed Customer ID unchanged
  - [x] Enter Company, First Name, Last Name; Salutation = `---` if not supplied
  - [x] Fill Main address: Street, ZIP, City, Country, Email, Telephone
  - [ ] Assign Main address the **Invoice address** role
  - [ ] If billing == delivery, also assign **Delivery address** role (no separate address created)
  - [x] Miscellaneous tab: Alias name, Discount = 0%, Net or Gross = Net
  - [x] Payment tab: select exact Payment Method
    - [ ] If missing → open Data > terms of payment, search exact method
      - [ ] Exact match exists → select, return to Debtor editor
      - [ ] Conflict → stop for manual review
      - [ ] No match → create: Name/Description = method, payment-code mapping (Bank Transfer→Credit transfer, Credit Card→Credit card, SEPA Direct Debit→SEPA direct debit), Cash discount/Discount Days/Net Days = 0, leave Text fields blank, don't set as standard, Save
      - [ ] Return to Debtor editor, select new Payment Method
  - [ ] Save Debtor once
  - [ ] Return to Order, reopen address selector, search again, select newly saved Debtor
  - [ ] Confirm Invoice/Delivery address now populate (confirms save succeeded)

## 5. Product Resolution (repeat per line item, in source order)
- [x] Click upper Product-selection icon beside Items table (NOT green + icon)
- [x] Search "Select a product" by exact extracted SKU
  - [x] One exact match → select, click OK
  - [x] Conflict → stop for manual review
  - [x] No match → Cancel, proceed to creation branch
- [x] **VAT resolution (before product creation):**
  - [x] Open Data > VATs, search exact name (e.g. "VAT 19%")
  - [x] Exact match (Name, Value %) → reuse
  - [ ] VAT code = S/Standard rate checked on reuse — *not done: the VATs list has no VAT-code column (its columns are Standard, Name, Description, Value), so a pre-existing rate carrying a different code is reused silently. Rates this bot creates always get the S code.*
  - [x] Conflict → stop for manual review
  - [x] No match → create: Name/Description = "VAT {pct}%", VAT code = S (Standard rate), Value = pct, leave Standard VAT unchanged, Save
- [x] **Product creation branch** (only after required VAT exists):
  - [x] Click "New product"
  - [x] Item Number = extracted SKU
  - [x] Name and Description = extracted item description
  - [x] Price (gross) = unit net price × (1 + VAT% / 100), rounded to 2 decimals — do NOT apply line discount here
  - [x] Cost price (net) = 0.00
  - [x] Select exact VAT
  - [x] Stock = 0.00
  - [x] Leave Category/GTIN/supplier code/allowance/Product Picture/UDF1 blank/unchanged
  - [x] Save once
  - [x] Return to Order, reopen product selector, search SKU, select newly saved product
  - [x] If not found after save → stop for manual review
- [x] **Complete line item:** — *implemented, but NOT yet run against the live app; see "Known gaps"*
  - [x] Set Qty to extracted quantity
  - [x] Set/confirm U.Price = extracted unit net price, VAT = extracted %
  - [x] Set line Discount = extracted item discount
  - [x] Verify line Price = qty × unit net price × (1 − discount/100)
- [x] Repeat select-or-create branch for every remaining item row

## 6. Complete & Save Order
- [ ] Confirm Debtor addresses and all Product lines match source image
- [x] Confirm overall Discount = 0%, Shipping = Free of shipping costs / 0.00 (unless image specifies otherwise)
- [x] Confirm Total Net / VAT / Total match source totals
- [x] Click Save
- [ ] Open Data > Documents, confirm one Order row: correct number, Date, Cust.Ref., **open** state, correct Total

## 7. Generate Linked Invoice
- [x] From saved Order's "Create a follow-up document" area, click **Invoice** (NOT the top-toolbar Invoice button)
- [x] Wait for linked New Invoice editor

## 8. Complete & Verify Invoice
- [x] Leave auto-proposed Invoice No., Invoice Date, Service date unchanged
- [ ] Confirm Cust.Ref., Invoice/Delivery address, Order Date, VAT mode, item lines, totals copied correctly from Order
- [ ] Set/confirm Invoice payment method = extracted Payment Method (stop for manual review if unavailable)
- [x] Apply payment status:
  - [x] If PAID → check "paid", set payment date = extracted Payment Date, Value = full Invoice Total
  - [x] If not PAID → leave "paid" unchecked, do not invent date/value
- [x] Save
- [ ] Open Data > Documents, confirm Invoice row has expected state + Total, and source Order still shows correct Cust.Ref. + Total
- [ ] (Optional) Reopen Invoice to re-confirm persisted payment method, paid state, date, value
- [ ] Stop — do NOT create Delivery, Correction, or Dunning documents

## 9. Robustness / Manual-Review Handling
- [x] Central "stop for manual review" mechanism (clear log message + graceful halt, not a crash)
- [x] Handles: ambiguous Debtor match, ambiguous Product match, ambiguous/conflicting Payment Method, ambiguous/conflicting VAT, product not found after creation
- [ ] Handles: Payment Method unavailable at Invoice step (step 8 not implemented yet)

## Known gaps

**Item line values (design doc 3.13–3.16) are written blind.** The Order's
Items table is a Nebula NatTable (`DocumentEditor` →
`DocumentItemListTable.getNatTable()`), which paints every cell onto one
SWT Canvas and exposes nothing to UI Automation — so unlike every other
screen here, `workflow/line_items.py` cannot be grounded in a UIA dump.
It uses exactly one click (to give the table keyboard focus) and then
drives it with `Ctrl+Home`/arrow keys, reading each cell through the
clipboard. Columns are identified by content anchors (SKU, description)
plus the fixed `DocumentItemListDescriptor` ordering, never by counting
from the left. Every line is read back and checked, including
`Price = qty × unit net × (1 − discount/100)`, because a keystroke landing
in the wrong cell raises no error — only a wrong number.

**Field specs for the product/VAT screens have not been confirmed against
a live dump yet.** Their titles are not guesses — they were read out of
the installed Fakturama's own resource bundle
(`com.sebulli.fakturama.rcp_2.2.0.jar`, `OSGI-INF/l10n/bundle.properties`)
and cross-referenced against the message keys each editor class actually
references in its constant pool, so e.g. the new-VAT tab is `New TAX
Rate` rather than the `New VAT` a guess would have produced. That method
reproduces the two tab titles already verified against the running app
(`New Debtor`, `New Term of Payment`), but it can't confirm a control's
*type* or *position*. Run `uv run entry_point.py --image sample_order.jpg
--dump-ui` once against a live instance to check them, and read the
resolver's self-diagnosing failure output before changing any spec.
