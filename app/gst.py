"""Australian GST helpers (A New Tax System (Goods and Services Tax) Act 1999).

Uses the 1/11 tax fraction for GST-inclusive prices (standard 10% GST).
BAS labels follow the ATO activity statement (G10/G11 purchases, 1B GST on purchases).
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

TWOPLACE = Decimal("0.01")
GST_RATE = Decimal("0.10")
TAX_FRACTION = Decimal("1") / Decimal("11")  # GST component of an inclusive price

TREATMENTS = (
    "requires_review",
    "taxable",
    "gst_free",
    "input_taxed",
    "out_of_scope",
    "capital",
)

TREATMENT_LABELS = {
    "taxable": "Taxable (10% GST) — claim input tax credits",
    "gst_free": "GST-free (food, medical, education, exports)",
    "input_taxed": "Input-taxed (financial supplies, residential rent) — no ITC",
    "out_of_scope": "Out of scope (wages, super, stamps, bank principal)",
    "capital": "Capital acquisition (BAS G10) — 10% GST if taxable",
}


def money(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWOPLACE, rounding=ROUND_HALF_UP)


def gst_from_inclusive(gross: Decimal) -> Decimal:
    """ATO 1/11 method: GST = inclusive price ÷ 11, rounded to cents."""
    return money(money(gross) * TAX_FRACTION)


def net_from_inclusive(gross: Decimal) -> Decimal:
    g = money(gross)
    return g - gst_from_inclusive(g)


def gst_from_exclusive(net: Decimal) -> Decimal:
    return money(money(net) * GST_RATE)


def inclusive_from_exclusive(net: Decimal) -> Decimal:
    n = money(net)
    return n + gst_from_exclusive(n)


def split_line(
    *,
    treatment: str,
    gst_inclusive: bool,
    amount: Decimal,
    gst_amount: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (ex_gst, gst, inc_gst) for one line or header total."""
    treatment = treatment or "taxable"
    amount = money(amount)
    if treatment in ("gst_free", "input_taxed", "out_of_scope"):
        return amount, Decimal("0.00"), amount

    taxable = treatment in ("taxable", "capital")
    if not taxable:
        return amount, Decimal("0.00"), amount

    if gst_amount is not None and money(gst_amount) > 0:
        gst = money(gst_amount)
        if gst_inclusive:
            inc = amount
            ex = inc - gst
        else:
            ex = amount
            inc = ex + gst
        return money(ex), gst, money(inc)

    if gst_inclusive:
        inc = amount
        gst = gst_from_inclusive(inc)
        return inc - gst, gst, inc
    ex = amount
    gst = gst_from_exclusive(ex)
    return ex, gst, ex + gst


def bas_labels(treatment: str, is_purchase: bool = True) -> tuple[str, str | None]:
    """Return (amount_label, gst_label) for a purchase journal line."""
    if not is_purchase:
        return "G1", "1A"
    if treatment == "capital":
        return "G10", "1B"
    if treatment == "requires_review":
        # GST unverified: do not recognize input tax credit until verified
        return amount, Decimal("0.00"), amount

    if treatment == "gst_free":
        return "G14", None
    if treatment == "input_taxed":
        return "G13", None
    if treatment == "out_of_scope":
        return "N/A", None
    return "G11", "1B"


def infer_treatment_from_text(text: str) -> str | None:
    t = (text or "").lower()
    if any(w in t for w in ("wage", "salary", "payg", "superannuation", "sgc")):
        return "out_of_scope"
    if any(w in t for w in ("bank fee", "interest charged", "loan repayment")):
        return "input_taxed"
    if any(w in t for w in ("fresh fruit", "unprocessed", "gst free", "gst-free")):
        return "gst_free"
    if any(w in t for w in ("laptop", "computer hardware", "motor vehicle", "plant & equipment")):
        return "capital"
    return None
