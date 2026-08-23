"""
Structured schema for data extracted from a single order image.

Field names/shape mirror what the Order-first automation flow needs:
Order Date + External Reference, Debtor identity/addresses/payment,
line items, and totals. Dates are kept as ISO strings (YYYY-MM-DD) and
numbers as plain floats — normalization is enforced via the extraction
prompt rather than a separate post-processing pass, since the LLM call
is already the natural place to do it.
"""

from pydantic import BaseModel, Field


class Address(BaseModel):
    street: str = Field(description="Street and house number")
    zip: str = Field(description="Postal code, kept as a string (leading zeros matter)")
    city: str
    country: str


class Debtor(BaseModel):
    company: str
    contact_first_name: str = Field(default="", description="Empty string if not supplied")
    contact_last_name: str = Field(default="", description="Empty string if not supplied")
    alias: str = Field(default="", description="Customer alias/short code, if present")
    email: str = Field(default="")
    phone: str = Field(default="")
    billing_address: Address
    delivery_address: Address = Field(
        description="Same as billing_address if the image shows no separate delivery address"
    )


class Payment(BaseModel):
    method: str = Field(description='e.g. "Bank Transfer", "Credit Card", "SEPA Direct Debit"')
    paid_status: str = Field(description='Exactly as shown, e.g. "PAID" or "UNPAID"')
    payment_date: str | None = Field(
        default=None,
        description="ISO format YYYY-MM-DD. Null if not paid or no date is shown.",
    )


class LineItem(BaseModel):
    sku: str
    description: str
    quantity: float
    unit: str = Field(default="", description='e.g. "pcs", empty string if not shown')
    unit_net_price: float
    discount_pct: float = Field(default=0, description="0-100, 0 if no discount shown")
    vat_pct: float = Field(description="0-100, e.g. 19 for 19% VAT")
    line_total: float = Field(description="Source line total exactly as shown on the image")


class OrderExtraction(BaseModel):
    order_date: str = Field(description="ISO format YYYY-MM-DD")
    external_reference: str
    currency: str = Field(default="EUR")
    debtor: Debtor
    payment: Payment
    items: list[LineItem]
    net_total: float
    vat_total: float
    gross_total: float
