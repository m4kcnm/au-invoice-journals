from __future__ import annotations

from app.gst import infer_treatment_from_text
from app.models import Account, Creditor, Invoice, InvoiceLine, InvoiceType, MappingRule, account_by_code


def _ci_contains(hay: str, needle: str) -> bool:
    return (needle or "").strip().lower() in (hay or "").lower()


def match_invoice_type(session, invoice: Invoice, line: InvoiceLine | None = None) -> InvoiceType | None:
    if invoice.invoice_type_id:
        return session.get(InvoiceType, invoice.invoice_type_id)

    blob = " ".join(
        [
            invoice.supplier_name or "",
            invoice.filename or "",
            (line.description if line else ""),
            " ".join(ln.description for ln in invoice.lines),
        ]
    ).lower()

    types = session.query(InvoiceType).all()
    for t in types:
        for kw in (t.keywords or "").split(","):
            kw = kw.strip().lower()
            if kw and kw in blob:
                return t
    return None


def match_rule(session, invoice: Invoice, line: InvoiceLine | None) -> MappingRule | None:
    from app.abn import digits_only
    rules = (
        session.query(MappingRule)
        .order_by(MappingRule.priority.asc(), MappingRule.id.asc())
        .all()
    )
    desc = (line.description if line else "") or ""
    inv_abn = digits_only(invoice.supplier_abn)

    for rule in rules:
        p = rule.pattern or ""
        if rule.match_on == "supplier" and _ci_contains(invoice.supplier_name, p):
            return rule
        if rule.match_on == "abn" and digits_only(p) and digits_only(p) == inv_abn:
            return rule
        if rule.match_on == "description" and (_ci_contains(desc, p) or _ci_contains(invoice.filename, p)):
            return rule
        if rule.match_on == "invoice_type":
            t = match_invoice_type(session, invoice, line)
            if t and (_ci_contains(t.name, p) or str(t.id) == p):
                return rule
    return None


def resolve_mapping(session, invoice: Invoice, line: InvoiceLine | None = None) -> dict:
    from app.abn import digits_only
    unallocated = (
        account_by_code(session, "6-9000")
        or account_by_code(session, "6-1300")
        or session.query(Account).filter_by(type="expense", archived=False).first()
    )

    account = None
    treatment = ""
    source = "unallocated"

    # 1. Match category
    itype = match_invoice_type(session, invoice, line)

    # 2. Match creditor: ABN-first fallback
    creditor = session.get(Creditor, invoice.creditor_id) if invoice.creditor_id else None
    
    inv_abn = digits_only(invoice.supplier_abn)
    if not creditor and len(inv_abn) == 11:
        for c in session.query(Creditor).filter(Creditor.abn.isnot(None)).all():
            if digits_only(c.abn) == inv_abn:
                creditor = c
                break

    if not creditor and invoice.supplier_name:
        creditor = session.query(Creditor).filter(Creditor.name.ilike(invoice.supplier_name.strip())).first()

    # If creditor has a linked invoice category and none was detected, inherit it
    if creditor and creditor.invoice_type_id and not itype:
        itype = session.get(InvoiceType, creditor.invoice_type_id)

    # 3. Rule matching
    rule = match_rule(session, invoice, line)
    if line and line.account_id:
        account = session.get(Account, line.account_id)
        source = "line"
    elif rule and rule.account_id:
        account = session.get(Account, rule.account_id)
        source = f"rule:{rule.name}"
        if rule.gst_treatment:
            treatment = rule.gst_treatment
        if rule.invoice_type_id and not itype:
            itype = session.get(InvoiceType, rule.invoice_type_id)
    elif creditor and creditor.default_account_id:
        account = session.get(Account, creditor.default_account_id)
        source = f"creditor:{creditor.name}"
        treatment = creditor.gst_treatment or treatment
    elif itype and itype.account_id:
        account = session.get(Account, itype.account_id)
        source = f"type:{itype.name}"
        treatment = itype.gst_treatment or treatment

    # 4. Line-level GST treatment overrides
    if line and line.gst_treatment:
        treatment = line.gst_treatment
        if source == "unallocated":
            source = "line"

    if not treatment:
        guessed = infer_treatment_from_text((line.description if line else "") + " " + (invoice.supplier_name or ""))
        if guessed:
            treatment = guessed
            source = source if source != "unallocated" else "gst-hint"

    if not treatment and account:
        treatment = account.gst_treatment

    if not treatment:
        treatment = "taxable"

    if account is None:
        account = unallocated

    return {
        "account": account,
        "account_id": account.id if account else None,
        "gst_treatment": treatment,
        "invoice_type": itype,
        "creditor": creditor,
        "source": source,
    }

def preview_resolution(supplier: str, abn: str, description: str, invoice_type_id: int | None) -> dict:
    from app.models import SessionLocal
    session = SessionLocal()
    try:
        inv = Invoice(
            supplier_name=supplier,
            supplier_abn=abn or "",
            filename=description,
            invoice_type_id=invoice_type_id,
        )
        line = InvoiceLine(description=description, invoice=inv)
        inv.lines = [line]

        res = resolve_mapping(session, inv, line)

        return {
            "account_code": res["account"].code if res["account"] else "",
            "account_name": res["account"].name if res["account"] else "",
            "gst_treatment": res["gst_treatment"],
            "source": res["source"],
            "invoice_type": res["invoice_type"].name if res["invoice_type"] else "",
            "creditor": res["creditor"].name if res["creditor"] else "",
        }
    finally:
        session.close()