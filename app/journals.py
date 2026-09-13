from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.abn import is_valid_abn
from app.gst import bas_labels, money, split_line
from app.mappings import resolve_mapping
from app.models import Account, Invoice, Journal, JournalLine, account_by_code

ALLOWED_INVOICE_TRANSITIONS = {
    "imported": {"extracted", "failed", "reviewed"},
    "extracted": {"imported", "reviewed", "failed"},
    "reviewed": {"imported", "reviewed", "posted", "failed"},
    "failed": {"imported", "extracted", "reviewed"},
    "posted": set(),
}


def transition_invoice(invoice: Invoice, new_status: str) -> None:
    """Apply an explicit invoice lifecycle transition and fail closed on invalid moves."""
    current = invoice.status or "imported"
    allowed = ALLOWED_INVOICE_TRANSITIONS.get(current, set())
    if new_status != current and new_status not in allowed:
        raise ValueError(f"Invalid invoice status transition: {current} -> {new_status}")
    invoice.status = new_status



def get_ap_account(session) -> Account | None:
    return (
        account_by_code(session, "2-1100")
        or account_by_code(session, "2-2000")
        or session.query(Account).filter_by(type="liability", archived=False).first()
    )


def get_gst_account(session) -> Account | None:
    return (
        account_by_code(session, "1-1200")
        or session.query(Account).filter(Account.type == "asset", Account.name.ilike("%GST%")).first()
    )


def get_rounding_account(session) -> Account | None:
    return (
        account_by_code(session, "6-9900")
        or account_by_code(session, "6-9000")
        or session.query(Account).filter_by(type="expense", archived=False).order_by(Account.code.desc()).first()
    )


def build_journal_preview(session, invoice: Invoice) -> dict:
    ap = get_ap_account(session)
    gst_recv = get_gst_account(session)
    lines_out = []
    total_dr = Decimal("0.00")
    total_cr = Decimal("0.00")

    gst_registered = invoice.gst_registered and invoice.is_tax_invoice
    header_gst = money(invoice.gst_amount)
    line_gst_sum = Decimal("0.00")
    net_by_account: dict[int, dict] = {}

    for line in invoice.lines:
        mapped = resolve_mapping(session, invoice, line)
        treatment = mapped["gst_treatment"]
        amount = money(line.amount)
        gst_hint = money(line.gst_amount) if money(line.gst_amount) else None

        if not gst_registered:
            ex, gst, inc = amount, Decimal("0.00"), amount
            treatment = "out_of_scope"
        else:
            ex, gst, inc = split_line(
                treatment=treatment,
                gst_inclusive=invoice.gst_inclusive,
                amount=amount,
                gst_amount=gst_hint,
            )

        acc = mapped["account"]
        if acc is None:
            continue

        bucket = net_by_account.setdefault(
            acc.id,
            {"account": acc, "ex": Decimal("0"), "gst": Decimal("0"), "treatment": treatment, "desc": []},
        )
        bucket["ex"] += ex
        bucket["gst"] += gst
        bucket["desc"].append(line.description)
        line_gst_sum += gst

    # Proportional scaling if header GST deviates slightly from line rounding
    if gst_registered and header_gst and invoice.gst_inclusive and abs(header_gst - line_gst_sum) >= Decimal("0.02"):
        total = money(invoice.total)
        remaining_net = total - header_gst
        summed_ex = sum((b["ex"] for b in net_by_account.values()), Decimal("0"))
        if summed_ex:
            for b in net_by_account.values():
                share = (b["ex"] / summed_ex) if summed_ex else Decimal("0")
                b["ex"] = money(remaining_net * share)
                b["gst"] = Decimal("0.00")
        line_gst_sum = Decimal("0.00")
        header_override_gst = header_gst
    else:
        header_override_gst = None

    narration = f"{invoice.supplier_name or 'Supplier'} inv {invoice.invoice_number or invoice.id}".strip()

    # 1. Expense debits
    for acc_id, b in net_by_account.items():
        ex = money(b["ex"])
        if ex == 0:
            continue
        amt_label, _ = bas_labels(b["treatment"])
        lines_out.append(
            {
                "account_id": acc_id,
                "account_code": b["account"].code,
                "account_name": b["account"].name,
                "description": "; ".join(d for d in b["desc"] if d)[:280] or narration,
                "debit": ex,
                "credit": Decimal("0.00"),
                "gst_treatment": b["treatment"],
                "bas_label": amt_label,
            }
        )
        total_dr += ex

    # 2. GST Receivable debit (BAS 1B)
    gst_total = header_override_gst if header_override_gst is not None else money(
        sum((b["gst"] for b in net_by_account.values()), Decimal("0"))
    )

    if gst_total > 0 and gst_recv:
        lines_out.append(
            {
                "account_id": gst_recv.id,
                "account_code": gst_recv.code,
                "account_name": gst_recv.name,
                "description": "GST on purchases (BAS 1B)",
                "debit": gst_total,
                "credit": Decimal("0.00"),
                "gst_treatment": "taxable",
                "bas_label": "1B",
            }
        )
        total_dr += gst_total

    # 3. Accounts Payable credit
    payable = money(invoice.total)
    if payable == 0:
        payable = total_dr

    if ap:
        lines_out.append(
            {
                "account_id": ap.id,
                "account_code": ap.code,
                "account_name": ap.name,
                "description": narration,
                "debit": Decimal("0.00"),
                "credit": payable,
                "gst_treatment": "bas_excluded",
                "bas_label": "",
            }
        )
        total_cr += payable

    # 4. Rounding adjustment
    diff = money(total_dr - total_cr)
    if diff != 0 and abs(diff) <= Decimal("0.10"):
        rounding = get_rounding_account(session)
        if rounding:
            if diff > 0:
                lines_out.append(
                    {
                        "account_id": rounding.id,
                        "account_code": rounding.code,
                        "account_name": rounding.name,
                        "description": "Rounding",
                        "debit": Decimal("0.00"),
                        "credit": diff,
                        "gst_treatment": "out_of_scope",
                        "bas_label": "N/A",
                    }
                )
                total_cr += diff
            else:
                lines_out.append(
                    {
                        "account_id": rounding.id,
                        "account_code": rounding.code,
                        "account_name": rounding.name,
                        "description": "Rounding",
                        "debit": abs(diff),
                        "credit": Decimal("0.00"),
                        "gst_treatment": "out_of_scope",
                        "bas_label": "N/A",
                    }
                )
                total_dr += abs(diff)

    warnings = []
    unallocated_count = sum(1 for line in invoice.lines if resolve_mapping(session, invoice, line)["account"] is None)
    if unallocated_count:
        warnings.append(
            f"{unallocated_count} invoice line(s) have no mapped GL account. Posting is blocked until they are allocated."
        )
    if invoice.supplier_abn and len(invoice.supplier_abn) == 11:
        if not is_valid_abn(invoice.supplier_abn):
            warnings.append("Supplier ABN fails the ATO checksum — check before claiming GST credits.")
    if gst_registered and invoice.is_tax_invoice and money(invoice.total) >= Decimal("82.50") and not invoice.supplier_abn:
        warnings.append("Tax invoices over $82.50 (inc GST) should show the supplier ABN.")
    if not gst_registered:
        warnings.append("Entity is not GST-registered: GST is not separated; full amount goes to expense.")

    return {
        "date": invoice.invoice_date or date.today(),
        "narration": narration,
        "lines": lines_out,
        "total_dr": money(total_dr),
        "total_cr": money(total_cr),
        "balanced": money(total_dr) == money(total_cr),
        "warnings": warnings,
    }


def persist_journal(session, invoice: Invoice, *, post: bool) -> Journal:
    preview = build_journal_preview(session, invoice)
    if post and not preview.get("balanced"):
        raise ValueError(
            f"Posting Rejected: Journal is out of balance (Debits: ${preview.get('total_dr')}, Credits: ${preview.get('total_cr')})."
        )
    if post and any("Posting is blocked" in warning for warning in preview.get("warnings", [])):
        raise ValueError("Posting Rejected: every invoice line must resolve to an explicit GL account before posting.")

    # Upsert draft journal if one already exists for this invoice
    j = (
        session.query(Journal)
        .filter(Journal.invoice_id == invoice.id, Journal.status == "draft")
        .first()
    )

    if not j:
        j = Journal(
            invoice_id=invoice.id,
            date=preview["date"],
            narration=preview["narration"],
            status="posted" if post else "draft",
        )
        session.add(j)
        session.flush()
    else:
        j.date = preview["date"]
        j.narration = preview["narration"]
        j.status = "posted" if post else "draft"
        for ln in list(j.lines):
            session.delete(ln)
        session.flush()

    for row in preview["lines"]:
        session.add(
            JournalLine(
                journal_id=j.id,
                account_id=row["account_id"],
                description=row["description"],
                debit=row["debit"],
                credit=row["credit"],
                gst_treatment=row["gst_treatment"],
                bas_label=row["bas_label"],
            )
        )

    transition_invoice(invoice, "posted" if post else "reviewed")
    session.flush()
    return j
