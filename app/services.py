from __future__ import annotations

import json
import re
import shutil
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.abn import digits_only
from app.config import DATA_DIR, INBOX_DIR, INVOICES_DIR
from app.extract import capture_document, normalize_extracted, run_hybrid_extract
from app.gst import money
from app.models import (
    AuditLog,
    Creditor,
    Invoice,
    InvoiceLine,
    SessionLocal,
    get_setting,
    set_setting,
)

ARCHIVE_DIR = DATA_DIR / "archive"


def sanitize_name(val: str, default: str = "Unknown") -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", (val or "").strip())
    return cleaned if cleaned else default


def get_australian_fy(d: date | None) -> str:
    if not d:
        d = date.today()
    start_year = d.year if d.month >= 7 else d.year - 1
    end_year = (start_year + 1) % 100
    return f"FY{start_year}-{end_year:02d}"


def log_audit_event(session, action: str, invoice_id: int | None = None, user=None, details: str = "") -> None:
    user_id = getattr(user, "id", None) if user else None
    username = getattr(user, "username", "System") if user else "System"
    session.add(
        AuditLog(
            action=action,
            invoice_id=invoice_id,
            user_id=user_id,
            username=username,
            details=details,
        )
    )


def upsert_creditor(session, name: str, abn: str) -> Creditor | None:
    from app.abn import digits_only
    name = (name or "").strip()
    abn_clean = digits_only(abn)

    # 1. Prioritize lookup by 11-digit ABN
    if len(abn_clean) == 11:
        # Check against both digit-only and spaced ABNs in DB
        all_creditors = session.query(Creditor).filter(Creditor.abn.isnot(None)).all()
        for c in all_creditors:
            if digits_only(c.abn) == abn_clean:
                # If existing creditor had a truncated/partial name, update it
                if name and (len(c.name) < len(name) or c.name.lower() in name.lower()):
                    c.name = name
                return c

    # 2. Fall back to name lookup (if valid name provided)
    if len(name) >= 3:
        row = session.query(Creditor).filter(Creditor.name.ilike(name)).first()
        if row:
            if abn_clean and not row.abn:
                row.abn = abn_clean
            return row

        # 3. Create new creditor if neither matched
        row = Creditor(name=name, abn=abn_clean or "")
        session.add(row)
        session.flush()
        return row

    return None

def ingest_pdf(src: Path, original_name: str | None = None, user=None) -> Invoice:
    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    session = SessionLocal()
    try:
        fname = original_name or src.name
        user_id = getattr(user, "id", None) if user else None
        inv = Invoice(
            filename=fname,
            stored_path="",
            status="imported",
            uploaded_by_id=user_id,
        )
        session.add(inv)
        session.flush()
        dest = INVOICES_DIR / f"{inv.id}_{src.name}"
        shutil.copy2(src, dest)
        inv.stored_path = str(dest)
        log_audit_event(session, "UPLOADED", invoice_id=inv.id, user=user, details=f"Ingested PDF {fname}")
        session.commit()
        session.refresh(inv)
        return inv
    finally:
        session.close()


def extract_invoice(invoice_id: int) -> None:
    # 1. Fetch file path on a short connection
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return
        stored_path = inv.stored_path
        url = get_setting(session, "ollama_url", "http://127.0.0.1:11434")
        model = get_setting(session, "ollama_model", "") or None
        gst_registered = get_setting(session, "gst_registered", "true") == "true"
    finally:
        session.close()

    # 2. Run AI extraction with no active database lock
    captured = capture_document(Path(stored_path))
    raw_text = captured.get("text") or ""
    b64_image = captured.get("b64_image")
    raw_data, method_used = run_hybrid_extract(raw_text, ollama_url=url, model=model, b64_image=b64_image)
    norm = normalize_extracted(raw_data, raw_text)

    # 3. Write structured results atomically
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return

        # --- ABN-FIRST CREDITOR LOOKUP ---
        extracted_abn = digits_only(norm.get("supplier_abn") or "")
        extracted_name = (norm.get("supplier_name") or "").strip()

        matched_creditor = None
        if len(extracted_abn) == 11:
            for c in session.query(Creditor).filter(Creditor.abn.isnot(None)).all():
                if digits_only(c.abn) == extracted_abn:
                    matched_creditor = c
                    break

        if matched_creditor:
            inv.supplier_name = matched_creditor.name
            inv.creditor_id = matched_creditor.id
            if matched_creditor.invoice_type_id:
                inv.invoice_type_id = matched_creditor.invoice_type_id
        else:
            inv.supplier_name = extracted_name
            cred = upsert_creditor(session, inv.supplier_name, extracted_abn)
            if cred:
                inv.creditor_id = cred.id
                if cred.invoice_type_id:
                    inv.invoice_type_id = cred.invoice_type_id

        inv.raw_text = raw_text
        inv.supplier_abn = extracted_abn
        inv.invoice_number = norm.get("invoice_number") or ""
        inv.invoice_date = norm.get("invoice_date")
        inv.due_date = norm.get("due_date")
        inv.is_tax_invoice = bool(norm.get("is_tax_invoice", True))
        inv.gst_inclusive = bool(norm.get("gst_inclusive", True))
        inv.subtotal_ex_gst = money(norm.get("subtotal_ex_gst") or 0)
        inv.gst_amount = money(norm.get("gst_amount") or 0)
        inv.total = money(norm.get("total") or 0)
        inv.gst_registered = gst_registered

        # Clear existing lines and persist newly normalized lines
        for ln in list(inv.lines):
            session.delete(ln)
        session.flush()

        for idx, ln in enumerate(norm.get("lines", [])):
            inv.lines.append(
                InvoiceLine(
                    line_number=idx,
                    description=ln.get("description") or "Item",
                    gst_treatment=ln.get("gst_treatment") or "taxable",
                    amount=money(ln.get("amount") or 0),
                    gst_amount=money(ln.get("gst_amount") or 0),
                )
            )

        inv.status = "extracted"
        log_audit_event(session, "EXTRACTED", invoice_id=inv.id, details=f"Extracted via {method_used}")
        session.commit()
    except Exception as e:
        session.rollback()
        traceback.print_exc()
        raise e
    finally:
        session.close()

def apply_invoice_form(invoice_id: int, form: dict, user=None) -> None:
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return
        inv.supplier_name = (form.get("supplier_name") or "").strip()
        inv.supplier_abn = digits_only(form.get("supplier_abn") or "")
        inv.invoice_number = (form.get("invoice_number") or "").strip()
        inv.invoice_date = _d(form.get("invoice_date"))
        inv.due_date = _d(form.get("due_date"))
        inv.is_tax_invoice = form.get("is_tax_invoice") == "on"
        inv.gst_inclusive = form.get("gst_inclusive") == "on"
        inv.gst_registered = form.get("gst_registered") == "on"
        inv.total = money(form.get("total") or 0)
        inv.gst_amount = money(form.get("gst_amount") or 0)
        inv.subtotal_ex_gst = money(form.get("subtotal_ex_gst") or 0)

        itype = form.get("invoice_type_id") or ""
        inv.invoice_type_id = int(itype) if itype else None

        cred = upsert_creditor(session, inv.supplier_name, inv.supplier_abn)
        inv.creditor_id = cred.id if cred else None

        for ln in list(inv.lines):
            session.delete(ln)
        session.flush()

        for i in range(0, 40):
            desc = (form.get(f"line_desc_{i}") or "").strip()
            amt = form.get(f"line_amount_{i}")
            if not desc and not amt:
                continue
            acc = form.get(f"line_account_{i}") or ""
            inv.lines.append(
                InvoiceLine(
                    line_number=i,
                    description=desc or "Item",
                    amount=money(amt or 0),
                    gst_amount=money(form.get(f"line_gst_{i}") or 0),
                    gst_treatment=form.get(f"line_treatment_{i}") or "",
                    account_id=int(acc) if acc else None,
                )
            )

        inv.status = "reviewed"
        log_audit_event(session, "SAVED_REVIEW", invoice_id=invoice_id, user=user, details="Modified bill details and line items.")
        session.commit()
    except Exception as e:
        session.rollback()
        traceback.print_exc()
        raise e
    finally:
        session.close()


def _d(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def ingest_inbox(user=None) -> list[Invoice]:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    for p in sorted(INBOX_DIR.glob("*.pdf")):
        created.append(ingest_pdf(p, p.name, user=user))
        p.unlink(missing_ok=True)
    return created


def archive_posted_invoice(invoice_id: int) -> Path | None:
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv or not inv.stored_path or not Path(inv.stored_path).exists():
            return None

        fy_folder = get_australian_fy(inv.invoice_date)
        supplier_name = sanitize_name(inv.supplier_name, "Unidentified Supplier")
        abn_part = f" (ABN {inv.supplier_abn})" if inv.supplier_abn else ""
        creditor_folder = f"{supplier_name}{abn_part}"

        target_dir = ARCHIVE_DIR / fy_folder / creditor_folder
        target_dir.mkdir(parents=True, exist_ok=True)

        inv_date_str = inv.invoice_date.isoformat() if inv.invoice_date else "undated"
        inv_no_str = sanitize_name(inv.invoice_number, f"INV{inv.id}")
        cat_str = sanitize_name(inv.invoice_type.name if inv.invoice_type else "Unallocated", "Expense")
        total_str = f"${inv.total:.2f}"

        new_filename = f"{inv_date_str}_{inv_no_str}_{cat_str}_{total_str}.pdf"
        target_path = target_dir / new_filename

        src_path = Path(inv.stored_path)
        if src_path != target_path:
            shutil.move(src_path, target_path)
            inv.stored_path = str(target_path)
            session.commit()

        return target_path
    finally:
        session.close()