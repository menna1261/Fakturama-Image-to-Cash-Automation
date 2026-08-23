"""
Known field specs for Fakturama screens, one dict per screen.

Only add an entry once you've confirmed it via tree inspection
(print_control_identifiers) against a real running instance — don't
guess ahead of what's been verified.
"""

from utils.automation.clipboard_grid import GridGeometry
from utils.automation.locators import FieldSpec, Strategy

NEW_ORDER_FIELDS = {
    "cust_ref": FieldSpec(
        name="cust_ref",
        strategy=Strategy.BY_TITLE,
        title="Cust.Ref.",
        control_type="Edit",
    ),
    "date": FieldSpec(
        name="date",
        # Fakturama's Date field is a native composite date-picker: the
        # sibling directly after the "Date" label is a Pane (not a plain
        # Edit) that wraps the actual editable sub-control. Confirmed via
        # the resolver's self-diagnosing failure log:
        #   Siblings near label 'Date': ["Edit(title='')", "Text(title='Date')",
        #                                 "Pane(title='')", "ComboBox(title='')"]
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Date",
        control_type="Pane",
    ),
    # price_mode: confirmed via dump_labels_and_fields() — this ComboBox
    # has no title of its own (empty), but sits in the same header row/
    # parent as the "Date" label and field, right after them. Reusing
    # label_text="Date" here (piggybacking off a different field's label)
    # is intentional: SIBLING_OF_LABEL just needs *some* label in the
    # same parent to anchor tree-order search from, it doesn't have to be
    # "this field's own" label.
    "price_mode": FieldSpec(
        name="price_mode",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Date",
        control_type="ComboBox",
        is_combo=True,
    ),
    # vat_mode: confirmed via dump_labels_and_fields() — the label and
    # its ComboBox share the identical title "VAT" (same pattern as
    # Cust.Ref.: Text 'Cust.Ref.' + Edit 'Cust.Ref.'), so BY_TITLE works
    # directly.
    "vat_mode": FieldSpec(
        name="vat_mode",
        strategy=Strategy.BY_TITLE,
        title="VAT",
        control_type="ComboBox",
        is_combo=True,
    ),
    # select_debtor_icon: confirmed via the resolver's self-diagnosing
    # failure log — the real siblings after the "Addresses" label are two
    # Image controls, both with empty title:
    #   ["Text(title='Addresses')", "Image(title='')", "Image(title='')"]
    # (the earlier "Line up"/"Line down" Buttons were a red herring —
    # coincidentally nearby in flat traversal order but belonging to a
    # different widget, likely the Items table's row-reorder controls).
    # SIBLING_OF_LABEL picks the FIRST Image after the label, which
    # should be the upper/existing-contact icon per the design doc — the
    # second Image (not targeted here) is the lower green-plus icon.
    "select_debtor_icon": FieldSpec(
        name="select_debtor_icon",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Addresses",
        control_type="Image",
    ),
    # select_product_icon: NOT yet confirmed against a live dump — built
    # by analogy with select_debtor_icon above, which is the same widget
    # pattern one section further down the same editor: a label followed
    # by an upper "pick an existing record" icon and a lower green-plus
    # icon, with SIBLING_OF_LABEL taking the first Image after the label.
    # The label text is the app's own string: the Items table's heading is
    # `editor.document.items = Items` in the shipped bundle.properties
    # (com.sebulli.fakturama.rcp_2.2.0.jar, OSGI-INF/l10n/bundle.properties).
    # If this resolves to the wrong icon, the resolver logs the real
    # siblings near "Items" — pick the other Image from that list.
    "select_product_icon": FieldSpec(
        name="select_product_icon",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Items",
        control_type="Image",
    ),
}

# "Select the address" dialog — confirmed via a full print_control_identifiers
# dump of the dialog itself. No search_box entry: the box already has
# keyboard focus the instant the dialog opens, so resolver.search() just
# takes the dialog's first Edit descendant rather than resolving a
# specced control (the Edit is nested one level deeper than a direct
# sibling of the "Search:" label, which is why a SIBLING_OF_LABEL lookup
# for it failed). The results grid also isn't in the tree until a search query
# actually returns rows (live-filtered) — not yet inspected.
# The results grid on this dialog has NO row-level UIA representation at
# all, so its rows are read by clipboard walk instead (clipboard_grid.py),
# which needs to know where the rows are drawn. Measured against the live
# dialog with coors.py: row 1 sits at (100, 124) from the window's
# top-left corner, consecutive rows are 22px apart, and 14 rows are drawn
# before the grid starts scrolling (consistent with the dialog's own
# 440px height — row 14 lands 30px clear of the bottom, row 15 wouldn't
# fit).
SELECT_ADDRESS_GRID = GridGeometry(
    click_offset_x=100,
    first_row_offset_y=124,
    row_height=22,
    visible_rows=14,
)

# Order/Invoice totals and follow-up area (design doc 4.2-4.6). Both are
# the same DocumentEditor, so one dict serves the Order and the Invoice.
# Titles are the app's own strings, and the ones DocumentEditor actually
# references in its constant pool:
#   editorDocumentTotalnet      -> editor.document.totalnet  = Total Net
#   commonFieldVat              -> common.field.vat          = VAT
#   commonFieldTotal            -> common.field.total        = Total
#   commonFieldDiscount         -> common.field.discount     = Discount
#   editorDocumentCreateduplicate -> "Create a follow-up document"
#
# total_net, NOT "Total Gross": the label tracks the document's price
# mode, and open_order sets that to Net (design doc 1.7). The earlier
# New Order dump showed "Total Gross" only because --dump-ui runs before
# fill_fields has set the mode.
#
# followup_invoice_button is the "Invoice" Button inside the "Create a
# follow-up document" group, confirmed in that dump at (1505,293)-(1563,352)
# alongside Confirmation/Delivery/Proforma. Design doc 4.6 is explicit
# that this is NOT the top toolbar's Invoice button — that one is titled
# "Create: New Invoice", so a bare "Invoice" title can't collide with it.
DOCUMENT_TOTALS_FIELDS = {
    "total_net": FieldSpec(
        name="total_net",
        strategy=Strategy.BY_TITLE,
        title="Total Net",
        control_type="Edit",
    ),
    "vat_total": FieldSpec(
        name="vat_total",
        strategy=Strategy.BY_TITLE,
        title="VAT",
        control_type="Edit",
    ),
    "total": FieldSpec(
        name="total",
        strategy=Strategy.BY_TITLE,
        title="Total",
        control_type="Edit",
    ),
    "discount": FieldSpec(
        name="discount",
        strategy=Strategy.BY_TITLE,
        title="Discount",
        control_type="Edit",
    ),
    "shipping": FieldSpec(
        name="shipping",
        strategy=Strategy.BY_TITLE,
        title="Shipping",
        control_type="ComboBox",
        is_combo=True,
    ),
    "followup_invoice_button": FieldSpec(
        name="followup_invoice_button",
        strategy=Strategy.BY_TITLE,
        title="Invoice",
        control_type="Button",
    ),
}

# Invoice-only controls (design doc 5.2-5.3). NOT yet confirmed against a
# live dump — no Invoice editor has been opened yet. Titles come from the
# message keys DocumentEditor references for its paid controls
# (createPaidControls builds bPaid/dtPaidDate/paidValue):
#   documentOrderStatePaid       -> document.order.state.paid = paid
#   editorDocumentPaidat         -> editor.document.paidat    = at
#   editorDocumentPaidvalue      -> editor.document.paidvalue = The paid value
#
# paid_date is specced as a Pane because Fakturama's date controls are
# composite date-pickers everywhere else in this app (see NEW_ORDER_FIELDS'
# "date"); set_field_value() drills into the inner Edit either way.
INVOICE_FIELDS = {
    "paid_checkbox": FieldSpec(
        name="paid_checkbox",
        strategy=Strategy.BY_TITLE,
        title="paid",
        control_type="CheckBox",
    ),
    "paid_date": FieldSpec(
        name="paid_date",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="at",
        control_type="Pane",
    ),
    "paid_value": FieldSpec(
        name="paid_value",
        strategy=Strategy.BY_TITLE,
        title="The paid value",
        control_type="Edit",
    ),
}

# "Select a product" (design doc 3.3) is the SAME dialog shell as "Select
# the address": both com.sebulli.fakturama.dialogs.SelectProductDialog and
# SelectContactDialog extend org.eclipse.jface.dialogs.AbstractSelectionDialog
# in the shipped rcp jar, so they share the search box on top, the
# custom-painted table below it, and OK/Cancel at the bottom — which also
# means the product grid has no UIA rows either and is read by the same
# clipboard walk. The row geometry is therefore reused rather than
# re-guessed; its own name exists so it can diverge if the two dialogs
# ever turn out to be sized differently (each class carries its own
# DEFAULT_DIALOG_SIZE). If they do, select_row()'s read-back catches it
# and says so — re-measure this one with coors.py, not the address one.
SELECT_PRODUCT_GRID = SELECT_ADDRESS_GRID

# Shared by both selection dialogs, for the same AbstractSelectionDialog
# reason as SELECT_PRODUCT_GRID above — workflow/product.py imports this
# dict rather than duplicating two identical OK/Cancel specs.
SELECT_ADDRESS_DIALOG_FIELDS = {
    "ok_button": FieldSpec(
        name="ok_button",
        strategy=Strategy.BY_TITLE,
        title="OK",
        control_type="Button",
    ),
    "cancel_button": FieldSpec(
        name="cancel_button",
        strategy=Strategy.BY_TITLE,
        title="Cancel",
        control_type="Button",
    ),
}

# Main window's left navigator panel — confirmed via find_controls_by_text().
# Two controls matched "New Contact": a Text link in the left panel
# (rect near x=11, far down the sidebar) and an unrelated SplitButton
# "Create a new contact" in the top toolbar. The design doc specifically
# wants the left-panel link, not the toolbar action, so control_type is
# pinned to "Text" to avoid matching the toolbar button.
MAIN_WINDOW_FIELDS = {
    "new_contact_link": FieldSpec(
        name="new_contact_link",
        strategy=Strategy.BY_TITLE,
        title="New Contact",
        control_type="Text",
    ),
    # terms_of_payment_link: confirmed via find_controls_by_text() — a
    # single, clean Text match in the left navigator (same pattern as
    # new_contact_link).
    "terms_of_payment_link": FieldSpec(
        name="terms_of_payment_link",
        strategy=Strategy.BY_TITLE,
        title="terms of payment",
        control_type="Text",
    ),
    # vats_link: confirmed against the live app — this link opened the
    # VATs list on the first --dump-ui run. products_link is its exact
    # neighbour: NavigationView builds the left panel's Data group from
    # the keys command.documents / command.products / command.vats / ...,
    # each rendered with its `.name` value, giving "Products" and "VATs"
    # (bundle.properties). Same "Text" pinning as new_contact_link, so
    # the top toolbar's same-named button can't match instead.
    "vats_link": FieldSpec(
        name="vats_link",
        strategy=Strategy.BY_TITLE,
        title="VATs",
        control_type="Text",
    ),
    "products_link": FieldSpec(
        name="products_link",
        strategy=Strategy.BY_TITLE,
        title="Products",
        control_type="Text",
    ),
    # There is deliberately NO "New product" navigator link spec here,
    # even though the left panel really does show one and BY_TITLE
    # resolves it in 0.33s. Clicking it opens nothing: the e4 model gives
    # the list toolbars' green + a `forcenew=true` parameter
    # (com.sebulli.fakturama.listview.product.add) and the navigator's
    # New-group action none, so the navigator action re-opens an editor
    # for the current selection and does nothing when there isn't one.
    # Products are therefore created the same way terms of payment and
    # VATs are — open the list, click its + — which is the one route in
    # this app that has actually been observed to work. See
    # PRODUCTS_LIST_FIELDS below.
    # save_button: NOT YET VERIFIED — a guess based on every other
    # Fakturama screen having a toolbar "Save" action, matching the
    # design doc's repeated "click the toolbar Save control" phrasing.
    # If BY_TITLE fails here, use find_controls_by_text(main_win, "Save")
    # to find the real control before adjusting this.
    "save_button": FieldSpec(
        name="save_button",
        strategy=Strategy.BY_TITLE,
        title="Save",
        control_type="Button",
    ),
}

# "terms of payment" data screen — confirmed via dump_labels_and_fields()
# after clicking terms_of_payment_link. The search box is nested one
# level deeper than a direct sibling of "Search:" (same shape as the
# Select-the-address dialog's search box), so we resolve the wrapping
# Pane and pull its Edit child, not a direct SIBLING_OF_LABEL->Edit.
TERMS_OF_PAYMENT_FIELDS = {
    "search_pane": FieldSpec(
        name="search_pane",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Search:",
        control_type="Pane",
    ),
    "create_button": FieldSpec(
        name="create_button",
        strategy=Strategy.BY_TITLE,
        title="Create a new term of payment",
        control_type="Button",
    ),
}

# New Debtor editor — confirmed via dump_labels_and_fields() after
# clicking New Contact. This dump lands directly on Addresses > Main
# address (no separate navigation needed), so company/name fields and
# address fields are both covered here.
#
# Customer ID and Salutation are intentionally NOT specced: Customer ID
# is left unchanged per the design doc, and our extraction schema has no
# salutation field, so Salutation is always left at its default "---".
#
# "First Name Last Name" and "ZIP - City" are each a single Pane
# wrapping TWO Edit controls (same composite-widget shape as the Date
# field) — resolved to the Pane here, then split into its two Edit
# children by steps/debtor.py's _fill_main_address(), not by a plain
# FieldSpec/find_field() call.
NEW_DEBTOR_FIELDS = {
    "company": FieldSpec(
        name="company",
        strategy=Strategy.BY_TITLE,
        title="Company",
        control_type="Edit",
    ),
    "first_last_name_pane": FieldSpec(
        name="first_last_name_pane",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="First Name Last Name",
        control_type="Pane",
    ),
    "street": FieldSpec(
        name="street",
        strategy=Strategy.BY_TITLE,
        title="Street",
        control_type="Edit",
    ),
    "zip_city_pane": FieldSpec(
        name="zip_city_pane",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="ZIP - City",
        control_type="Pane",
    ),
    "country": FieldSpec(
        name="country",
        strategy=Strategy.BY_TITLE,
        title="Country",
        control_type="ComboBox",
        is_combo=True,
    ),
    "email": FieldSpec(
        name="email",
        strategy=Strategy.BY_TITLE,
        title="E-Mail",
        control_type="Edit",
    ),
    "telephone": FieldSpec(
        name="telephone",
        strategy=Strategy.BY_TITLE,
        title="Telephone",
        control_type="Edit",
    ),
    # misc_tab: confirmed via find_controls_by_text() — a single, clean
    # TabItem match.
    "misc_tab": FieldSpec(
        name="misc_tab",
        strategy=Strategy.BY_TITLE,
        title="Miscellaneous",
        control_type="TabItem",
    ),
    # alias_name/discount/net_or_gross: confirmed via dump_labels_and_fields()
    # after switching to the Miscellaneous tab.
    "alias_name": FieldSpec(
        name="alias_name",
        strategy=Strategy.BY_TITLE,
        title="Alias name",
        control_type="Edit",
    ),
    "discount": FieldSpec(
        name="discount",
        strategy=Strategy.BY_TITLE,
        title="Discount",
        control_type="Edit",
    ),
    "net_or_gross": FieldSpec(
        name="net_or_gross",
        strategy=Strategy.BY_TITLE,
        title="Net or Gross",
        control_type="ComboBox",
        is_combo=True,
    ),
    # payment_method: confirmed via find_controls_by_text() — no separate
    # "Payment" tab exists; this ComboBox is already visible on the
    # Miscellaneous tab (the design doc's "2.10. Open Payment" refers to
    # this field, not a distinct screen).
    "payment_method": FieldSpec(
        name="payment_method",
        strategy=Strategy.BY_TITLE,
        title="Payment",
        control_type="ComboBox",
        is_combo=True,
    ),
}

# New Term of Payment editor — confirmed via dump_labels_and_fields()
# after clicking "Create a new term of payment". Not specced: the three
# "Text 'unpaid'/'deposit'/'paid'" fields (left blank per design doc) and
# "Standard" (never click "Set as standard").
#
# payment_code's title is literally "!editorPaymentPaymentcode!" — an
# untranslated i18n key in this Fakturama build, not a lookup bug.
NEW_PAYMENT_METHOD_FIELDS = {
    "name": FieldSpec(
        name="name",
        strategy=Strategy.BY_TITLE,
        title="Name",
        control_type="Edit",
    ),
    "description": FieldSpec(
        name="description",
        strategy=Strategy.BY_TITLE,
        title="Description",
        control_type="Edit",
    ),
    "payment_code": FieldSpec(
        name="payment_code",
        strategy=Strategy.BY_TITLE,
        title="!editorPaymentPaymentcode!",
        control_type="ComboBox",
        is_combo=True,
    ),
    "cash_discount": FieldSpec(
        name="cash_discount",
        strategy=Strategy.BY_TITLE,
        title="Cash discount",
        control_type="Edit",
    ),
    "discount_days": FieldSpec(
        name="discount_days",
        strategy=Strategy.BY_TITLE,
        title="Discount Days",
        control_type="Edit",
    ),
    "net_days": FieldSpec(
        name="net_days",
        strategy=Strategy.BY_TITLE,
        title="Net Days",
        control_type="Edit",
    ),
}

# "VATs" data screen (design doc 3.4) — same list-view shape as the terms
# of payment screen: a "Search:" label whose Edit sits one level deeper
# inside a Pane, and a green + button in the upper right whose title is
# its tooltip. Titles come from the shipped bundle.properties, where the
# VATs list and the terms-of-payment list are neighbouring entries:
#   common.label.searchfield  = Search:
#   main.menu.new.vat.tooltip = Create a new tax rate
# (compare main.menu.new.payment.tooltip = Create a new term of payment,
# which is the already-verified title of TERMS_OF_PAYMENT_FIELDS'
# create_button — same key pattern, so the same shape is expected here.)
VATS_LIST_FIELDS = {
    "search_pane": FieldSpec(
        name="search_pane",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Search:",
        control_type="Pane",
    ),
    "create_button": FieldSpec(
        name="create_button",
        strategy=Strategy.BY_TITLE,
        title="Create a new tax rate",
        control_type="Button",
    ),
}

# "Products" data screen — the same list-view shape as the terms of
# payment and VATs screens, and the route by which a new Product is
# created (design doc 3.7's "New product"); see the note in
# MAIN_WINDOW_FIELDS for why the navigator's own New-product link isn't
# used. The + button's title is its tooltip, command.new.product.tooltip
# = "Create a new product", exactly as the verified terms-of-payment
# create_button's title is main.menu.new.payment.tooltip.
PRODUCTS_LIST_FIELDS = {
    "search_pane": FieldSpec(
        name="search_pane",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Search:",
        control_type="Pane",
    ),
    "create_button": FieldSpec(
        name="create_button",
        strategy=Strategy.BY_TITLE,
        title="Create a new product",
        control_type="Button",
    ),
}

# New TAX Rate editor (design doc 3.6) — the editor Fakturama opens from
# the VATs list's green +. Field titles are the exact message values that
# com.sebulli.fakturama.parts.VatEditor references, read out of its
# constant pool in the shipped rcp jar and resolved against
# bundle.properties:
#   commonFieldName        -> common.field.name        = Name
#   commonFieldDescription -> common.field.description = Description
#   editorVatEinvoiceCode  -> editor.vat.einvoice.code = VAT code (E-Invoice)
#   commonFieldValue       -> common.field.value       = Value
#
# Not specced: Category, and the "This TAX Rate" standard-rate button —
# the design doc leaves the displayed Standard VAT unchanged.
#
# vat_code is filled explicitly rather than left at its default. The
# design doc treats "S (Standard rate)" as part of what makes a VAT row
# correct (3.5), so setting it is what makes the created row match what
# the reuse path checks for.
NEW_VAT_FIELDS = {
    "name": FieldSpec(
        name="name",
        strategy=Strategy.BY_TITLE,
        title="Name",
        control_type="Edit",
    ),
    "description": FieldSpec(
        name="description",
        strategy=Strategy.BY_TITLE,
        title="Description",
        control_type="Edit",
    ),
    "vat_code": FieldSpec(
        name="vat_code",
        strategy=Strategy.BY_TITLE,
        title="VAT code (E-Invoice)",
        control_type="ComboBox",
        is_combo=True,
    ),
    "value": FieldSpec(
        name="value",
        strategy=Strategy.BY_TITLE,
        title="Value",
        control_type="Edit",
    ),
}

# New product editor (design doc 3.8-3.10). Same provenance as
# NEW_VAT_FIELDS: these are the message values that
# com.sebulli.fakturama.parts.ProductEditor actually references —
#   exporterDataItemnumber            -> exporter.data.itemnumber          = Item Number
#   commonFieldName                   -> common.field.name                 = Name
#   commonFieldDescription            -> common.field.description          = Description
#   editorProductFieldGrosspriceName  -> editor.product.field.grossprice.name = Price (gross)
#   editorProductFieldCostprice       -> editor.product.field.costprice    = cost price (net)
#   commonFieldVat                    -> common.field.vat                  = VAT
#   commonFieldQuantity               -> common.field.quantity             = Stock
# — so the lowercase "cost price (net)" and the bracketed names are the
# app's own casing, not a typo.
#
# Not specced, because the design doc says to leave them alone: Category,
# GTIN, supplier code, allowance, Product Picture, user defined field 1,
# Quantity unit, and the "Price (net)" field (3.9 sets the GROSS price).
#
# The product editor can render several scaled price blocks
# (ProductEditor keeps netText/grossText as arrays, up to
# MAX_NUMBER_OF_PRICES). The live dump confirms scaled prices are off in
# this install — Fakturama's default — so there is exactly one price row,
# labelled "Price (gross)", and no "Price (net)" field on screen at all.
# If a dump ever shows several "from" blocks, both price specs need
# anchoring on a specific block rather than on the bare label.
NEW_PRODUCT_FIELDS = {
    "item_number": FieldSpec(
        name="item_number",
        strategy=Strategy.BY_TITLE,
        title="Item Number",
        control_type="Edit",
    ),
    "name": FieldSpec(
        name="name",
        strategy=Strategy.BY_TITLE,
        title="Name",
        control_type="Edit",
    ),
    "description": FieldSpec(
        name="description",
        strategy=Strategy.BY_TITLE,
        title="Description",
        control_type="Edit",
    ),
    # price_gross / cost_price are the editor's only composite fields, and
    # the only two that BY_TITLE cannot reach. Confirmed via
    # dump_labels_and_fields() on the live New product editor:
    #   Text title='Price (gross)'     rect=(L450, T441, R531, B461)
    #   Pane title=''                  rect=(L536, T438, R689, B464)
    #   Edit title=''                  rect=(L555, T438, R689, B464)
    #   Text title='cost price (net)'  rect=(L432, T472, R531, B492)
    #   Pane title=''                  rect=(L536, T469, R1258, B495)
    #   Edit title=''                  rect=(L536, T469, R670, B495)
    # Both are NetText/GrossText-style widgets wrapping a Nebula
    # FormattedText, so the label's title doesn't propagate onto the inner
    # Edit the way it does for Stock or Item Number — BY_TITLE failed with
    # "no Edit titled 'Price (gross)' found". Same shape as the New Order
    # Date field, so the same treatment: resolve the Pane, and let
    # set_field_value() drill into its first Edit descendant.
    #
    # Note cost_price's Pane is a whole ROW container (it runs to x=1258
    # and also holds the "allowance" label and its Edit at x=747). Taking
    # the FIRST Edit descendant is what makes that safe: the cost-price
    # Edit at x=536 comes before allowance's in tree order. Don't switch
    # this to a last/only-Edit lookup.
    "price_gross": FieldSpec(
        name="price_gross",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="Price (gross)",
        control_type="Pane",
    ),
    "cost_price": FieldSpec(
        name="cost_price",
        strategy=Strategy.SIBLING_OF_LABEL,
        label_text="cost price (net)",
        control_type="Pane",
    ),
    "vat": FieldSpec(
        name="vat",
        strategy=Strategy.BY_TITLE,
        title="VAT",
        control_type="ComboBox",
        is_combo=True,
    ),
    "stock": FieldSpec(
        name="stock",
        strategy=Strategy.BY_TITLE,
        title="Stock",
        control_type="Edit",
    ),
}
