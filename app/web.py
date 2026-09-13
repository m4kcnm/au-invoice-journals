from __future__ import annotations

import re

import uuid
import urllib.parse

import csv
import io
import json
import os
import traceback
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import fitz
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.aba import clean_bsb, generate_aba_file
from app.abn import digits_only, format_abn, is_valid_abn
from app.auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_current_user,
    get_current_user_optional,
    require_approver,
    verify_password,
)
from app.config import INBOX_DIR
from app.gst import TREATMENT_LABELS, TREATMENTS
from app.journals import build_journal_preview, persist_journal, transition_invoice
from app.mappings import preview_resolution
from app.models import (
    Account,
    AuditLog,
    Creditor,
    CreditorAuditLog,
    Invoice,
    InvoiceType,
    Journal,
    MappingRule,
    SessionLocal,
    User,
    get_setting,
    init_db,
    set_setting,
)
from app.ollama_client import list_models, ping
from app.sample import write_sample_invoice
from app.search import execute_invoice_search, parse_natural_query
from app.seed import seed_if_empty
from app.services import (
    build_transient_invoice,
    apply_invoice_form,
    archive_posted_invoice,
    extract_invoice,
    ingest_inbox,
    ingest_pdf,
    log_audit_event,
    log_creditor_audit,
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["abn"] = lambda v: format_abn(v) if v else ""
TEMPLATES.env.filters["aud"] = lambda v: f"${v:,.2f}" if v is not None else ""
TEMPLATES.env.filters["isodate"] = lambda v: v.isoformat() if v else ""


def boot() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    seed_if_empty()


@asynccontextmanager
async def lifespan(app: FastAPI):
    boot()
    yield


app = FastAPI(title="AU Invoice Journals", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.exception_handler(HTTPException)
async def auth_exception_handler(request: Request, exc: HTTPException):
    # 1. Authentication redirects
    if exc.status_code == 401:
        if request.url.path.startswith("/api/") or request.url.path.endswith("/status"):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        return RedirectResponse(url="/login", status_code=303)

    # 2. Return JSON only for pure API fetch requests
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    accept = request.headers.get("accept", "")
    if request.url.path.startswith("/api/") or ("application/json" in accept and not "text/html" in accept) or is_ajax:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # 3. For all browser form POSTs and navigations, redirect back with error popup parameter
    referer = request.headers.get("referer")
    if referer:
        base_url = referer.split("?")[0]
        return RedirectResponse(f"{base_url}?error={urllib.parse.quote(str(exc.detail))}", status_code=303)

    return HTMLResponse(
        status_code=exc.status_code,
        content=f"""
        <div style="font-family: system-ui; max-width: 520px; margin: 4rem auto; padding: 2rem; border: 1px solid #d8e0e6; border-radius: 8px;">
          <h2 style="color: #c02b0a; margin-top: 0;">Notice</h2>
          <p>{exc.detail}</p>
          <p><a href="/" style="color: #002f48; font-weight: 600;">&larr; Return to Dashboard</a></p>
        </div>
        """,
    )


def ctx(request: Request, **extra):
    session = SessionLocal()
    try:
        user = get_current_user_optional(request)
        base = {
            "request": request,
            "current_user": user,
            "entity": get_setting(session, "entity_name", "My Australian Business"),
            "gst_on": get_setting(session, "gst_registered", "true") == "true",
            "treatments": TREATMENTS,
            "treatment_labels": TREATMENT_LABELS,
        }
        base.update(extra)
        return base
    finally:
        session.close()


def find_next_pending_invoice_id(current_id: int) -> int | None:
    session = SessionLocal()
    try:
        next_inv = (
            session.query(Invoice)
            .filter(Invoice.id != current_id, Invoice.status != "posted")
            .order_by(Invoice.id.asc())
            .first()
        )
        return next_inv.id if next_inv else None
    finally:
        session.close()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=b"", media_type="image/x-icon")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if get_current_user_optional(request):
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(username=username.strip()).first()
        if not user or not verify_password(password, user.password_hash):
            return RedirectResponse("/login?error=Invalid+username+or+password", status_code=303)

        token = create_session_token(user)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,
        )
        return response
    except Exception as e:
        return RedirectResponse(f"/login?error=Login+error:+{str(e)}", status_code=303)
    finally:
        session.close()


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        invoices = session.query(Invoice).order_by(Invoice.id.desc()).limit(12).all()
        journals = session.query(Journal).order_by(Journal.id.desc()).limit(8).all()
        pending = session.query(Invoice).filter(Invoice.status != "posted").count()
        return TEMPLATES.TemplateResponse(
            "dashboard.html",
            ctx(
                request,
                invoices=invoices,
                journals=journals,
                pending=pending,
                ollama_ok=ping(get_setting(session, "ollama_url")),
            ),
        )
    finally:
        session.close()


MAX_UPLOAD_BYTES = 15 * 1024 * 1024


@app.post("/upload")
def upload(bg: BackgroundTasks, file: UploadFile = File(...), user: User = Depends(get_current_user)):
    # Keep the demo deliberately small and fail closed on non-PDF uploads.
    safe_name = Path(file.filename or "upload.pdf").name
    declared_type = (file.content_type or "").lower()
    if declared_type not in {"application/pdf", ""} and not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF invoice uploads are supported.")

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF upload exceeds the 15 MB demo limit.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF document.")

    # Strip any user-supplied directory traversal components
    clean_stem = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", safe_name)
    secure_disk_name = f"{uuid.uuid4().hex}_{clean_stem}"
    tmp = INBOX_DIR / secure_disk_name
    tmp.write_bytes(data)
    inv = ingest_pdf(tmp, original_name=safe_name, user=user)
    tmp.unlink(missing_ok=True)
    bg.add_task(extract_invoice, inv.id)
    return RedirectResponse(f"/invoices/{inv.id}", status_code=303)


@app.post("/inbox")
def scan_inbox(bg: BackgroundTasks, user: User = Depends(get_current_user)):
    created = ingest_inbox(user=user)
    for inv in created:
        bg.add_task(extract_invoice, inv.id)
    if len(created) == 1:
        return RedirectResponse(f"/invoices/{created[0].id}", status_code=303)
    return RedirectResponse("/invoices", status_code=303)


@app.post("/sample")
def sample(bg: BackgroundTasks, user: User = Depends(get_current_user)):
    path = write_sample_invoice()
    inv = ingest_pdf(path, path.name, user=user)
    bg.add_task(extract_invoice, inv.id)
    return RedirectResponse(f"/invoices/{inv.id}", status_code=303)


@app.get("/invoices", response_class=HTMLResponse)
def invoices_list(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        rows = session.query(Invoice).order_by(Invoice.id.desc()).all()
        return TEMPLATES.TemplateResponse("invoices.html", ctx(request, invoices=rows))
    finally:
        session.close()


@app.get("/payments/aba")
def download_aba_file(user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        invoices = (
            session.query(Invoice)
            .filter(Invoice.status == "posted", Invoice.payment_status != "batched")
            .order_by(Invoice.id.asc())
            .all()
        )

        if not invoices:
            return JSONResponse(
                status_code=400,
                content={"error": "No unbatched posted invoices available for ABA export."}
            )

        bank_code = get_setting(session, "bank_code", "PCU")
        user_name = get_setting(session, "entity_name", "My Australian Business")
        apca = get_setting(session, "user_apca_number", "123456")
        bsb = get_setting(session, "remitter_bsb", "085-005")
        acc = get_setting(session, "remitter_account", "123456789")

        try:
            aba_content, included_ids, skipped_info = generate_aba_file(
                invoices,
                bank_code=bank_code,
                user_name=user_name,
                user_apca_number=apca,
                remitter_bsb=bsb,
                remitter_account=acc,
            )
        except ValueError as err:
            return JSONResponse(status_code=400, content={"error": str(err)})

        # Fail clean: If ANY invoice has invalid or missing bank details, abort entirely.
        if skipped_info:
            header_msg = "ABA Export Blocked - The following invoices are missing valid banking details:"
            lines = [f"- Bill #{iid} ({inv.supplier_name or 'Unknown'}): {reason}"
                     for iid, reason in skipped_info
                     for inv in invoices if inv.id == iid]
            footer_msg = "Please update the vendor bank details before exporting the pay run."
            full_msg = header_msg + "\n\n" + "\n".join(lines) + "\n\n" + footer_msg
            return JSONResponse(status_code=400, content={"error": full_msg})

        # Mark all as batched only once the entire batch has passed validation
        for inv in invoices:
            inv.payment_status = "batched"
            log_audit_event(
                session,
                "BATCHED_FOR_PAYMENT",
                invoice_id=inv.id,
                user=user,
                details="Included in exported ABA pay run."
            )
        session.commit()

        today_str = date.today().strftime("%Y%m%d")
        return Response(
            content=aba_content,
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename=PAYRUN_{today_str}.aba"},
        )
    finally:
        session.close()


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int, error: str = "", user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return RedirectResponse("/invoices", status_code=303)

        accounts = session.query(Account).filter_by(archived=False).order_by(Account.code).all()
        types = session.query(InvoiceType).order_by(InvoiceType.name).all()
        creditor = session.get(Creditor, inv.creditor_id) if inv.creditor_id else None

        duplicate_invoice = None
        if inv.supplier_abn and inv.invoice_number:
            duplicate_invoice = (
                session.query(Invoice)
                .filter(
                    Invoice.id != inv.id,
                    Invoice.supplier_abn == inv.supplier_abn,
                    Invoice.invoice_number == inv.invoice_number,
                )
                .first()
            )

        preview = None
        preview_error = error
        try:
            if inv.lines or inv.total:
                preview = build_journal_preview(session, inv)
        except Exception as e:
            preview_error = preview_error or str(e)
            traceback.print_exc()

        page_count = 1
        try:
            if Path(inv.stored_path).exists():
                doc = fitz.open(inv.stored_path)
                page_count = len(doc)
                doc.close()
        except Exception:
            pass

        next_id = find_next_pending_invoice_id(invoice_id)

        response = TEMPLATES.TemplateResponse(
            "invoice_detail.html",
            ctx(
                request,
                inv=inv,
                creditor=creditor,
                accounts=accounts,
                types=types,
                preview=preview,
                error=preview_error,
                page_count=page_count,
                duplicate_invoice=duplicate_invoice,
                next_id=next_id,
                abn_ok=is_valid_abn(inv.supplier_abn) if inv.supplier_abn else None,
            ),
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    finally:
        session.close()


@app.get("/invoices/{invoice_id}/status")
def invoice_status(invoice_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return JSONResponse(status_code=404, content={"error": "not found"})
        return JSONResponse({
            "id": inv.id,
            "status": inv.status,
            "is_extracted": inv.status not in ("imported",),
            "supplier_name": inv.supplier_name or "",
            "total": float(inv.total or 0),
        })
    finally:
        session.close()


@app.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        return FileResponse(inv.stored_path, media_type="application/pdf", filename=inv.filename)
    finally:
        session.close()


@app.get("/invoices/{invoice_id}/page/{page_num}")
def invoice_page_image(invoice_id: int, page_num: int = 0, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv or not Path(inv.stored_path).exists():
            return Response(status_code=404)

        doc = fitz.open(inv.stored_path)
        if page_num < 0 or page_num >= len(doc):
            doc.close()
            return Response(status_code=404)

        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return Response(content=png_bytes, media_type="image/png")
    finally:
        session.close()


@app.post("/invoices/{invoice_id}/extract")
def invoice_extract(bg: BackgroundTasks, invoice_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if inv:
            if inv.status == "posted":
                raise HTTPException(status_code=400, detail="Posted invoices cannot be re-extracted.")
            transition_invoice(inv, "imported")
            log_audit_event(session, "RE_EXTRACTED", invoice_id=invoice_id, user=user, details="Triggered AI re-extraction")
            session.commit()
    finally:
        session.close()

    bg.add_task(extract_invoice, invoice_id)
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@app.post("/invoices/{invoice_id}/recalculate")
async def invoice_recalculate(request: Request, invoice_id: int, user: User = Depends(get_current_user)):
    form = dict(await request.form())
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if not inv:
            return JSONResponse(status_code=404, content={"error": "Invoice not found"})

        # Pure in-memory calculation: evaluate preview without persisting changes or logging audit events
        transient_inv = build_transient_invoice(inv, form)
        preview = build_journal_preview(session, transient_inv)
        lines = []
        for l in preview["lines"]:
            lines.append({
                "account_code": l["account_code"],
                "account_name": l["account_name"],
                "description": l["description"],
                "bas_label": l["bas_label"],
                "debit": f"${l['debit']:,.2f}" if l["debit"] > 0 else "",
                "credit": f"${l['credit']:,.2f}" if l["credit"] > 0 else "",
            })

        return JSONResponse({
            "balanced": preview["balanced"],
            "total_dr": f"${preview['total_dr']:,.2f}",
            "total_cr": f"${preview['total_cr']:,.2f}",
            "warnings": preview["warnings"],
            "lines": lines,
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "Preview computation failed"})
    finally:
        session.close()


@app.post("/invoices/{invoice_id}/save")
async def invoice_save(request: Request, invoice_id: int, user: User = Depends(get_current_user)):
    form = dict(await request.form())
    try:
        apply_invoice_form(invoice_id, form, user=user)
    except ValueError as val_err:
        return RedirectResponse(f"/invoices/{invoice_id}?error={urllib.parse.quote(str(val_err))}", status_code=303)
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@app.post("/invoices/{invoice_id}/journal")
async def invoice_journal(
    request: Request,
    invoice_id: int,
    action: str = Form("draft"),
    user: User = Depends(get_current_user),
):
    form = dict(await request.form())
    try:
        apply_invoice_form(invoice_id, form, user=user)
    except ValueError as val_err:
        return RedirectResponse(f"/invoices/{invoice_id}?error={urllib.parse.quote(str(val_err))}", status_code=303)

    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if action == "post":
            if user.role != "approver":
                raise HTTPException(
                    status_code=403,
                    detail="Approval Denied: Your account role does not have authorization to post General Ledger entries.",
                )

            inv.approved_by_id = user.id
            try:
                persist_journal(session, inv, post=True)
            except ValueError as post_err:
                session.rollback()
                return RedirectResponse(f"/invoices/{invoice_id}?error={urllib.parse.quote(str(post_err))}", status_code=303)
            log_audit_event(session, "APPROVED_AND_POSTED", invoice_id=invoice_id, user=user, details="Posted purchase journal to General Ledger.")
            session.commit()
            archive_posted_invoice(invoice_id)

            next_id = find_next_pending_invoice_id(invoice_id)
            if next_id:
                return RedirectResponse(f"/invoices/{next_id}?toast=posted", status_code=303)
            return RedirectResponse("/inbox/cleared", status_code=303)

        persist_journal(session, inv, post=False)
        log_audit_event(session, "SAVED_DRAFT", invoice_id=invoice_id, user=user, details="Saved as draft purchase journal.")
        session.commit()
        return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)
    finally:
        session.close()


@app.post("/invoices/{invoice_id}/delete")
def invoice_delete(invoice_id: int, user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        inv = session.get(Invoice, invoice_id)
        if inv:
            if inv.status == "posted":
                raise HTTPException(status_code=400, detail="Posted invoices cannot be deleted. Use a reversal/void workflow instead.")
            log_audit_event(session, "DELETED", invoice_id=invoice_id, user=user, details=f"Deleted invoice #{inv.invoice_number}")
            if inv.stored_path and os.path.exists(inv.stored_path):
                try:
                    os.remove(inv.stored_path)
                except OSError:
                    pass
            for log in inv.audit_logs:
                log.invoice_id = None
            session.flush()
            for j in list(inv.journals):
                session.delete(j)
            session.delete(inv)
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/invoices?toast=deleted", status_code=303)


# --- DEDICATED CREDITORS HUB ROUTES ---
@app.get("/creditors", response_class=HTMLResponse)
def creditors_list(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        return TEMPLATES.TemplateResponse(
            "creditors.html",
            ctx(
                request,
                creditors=session.query(Creditor).order_by(Creditor.name).all(),
                types=session.query(InvoiceType).order_by(InvoiceType.name).all(),
                accounts=session.query(Account).filter_by(archived=False).order_by(Account.code).all(),
            ),
        )
    finally:
        session.close()


@app.get("/creditors/{creditor_id}", response_class=HTMLResponse)
def creditor_detail(request: Request, creditor_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        c = session.get(Creditor, creditor_id)
        if not c:
            return RedirectResponse("/creditors", status_code=303)
        invoices = session.query(Invoice).filter_by(creditor_id=creditor_id).order_by(Invoice.id.desc()).all()
        accounts = session.query(Account).filter_by(archived=False).order_by(Account.code).all()
        types = session.query(InvoiceType).order_by(InvoiceType.name).all()
        return TEMPLATES.TemplateResponse(
            "creditor_detail.html",
            ctx(
                request,
                c=c,
                invoices=invoices,
                accounts=accounts,
                types=types,
            ),
        )
    finally:
        session.close()


@app.post("/creditors/new")
def create_creditor(
    name: str = Form(...),
    abn: str = Form(""),
    default_account_id: str = Form(""),
    gst_treatment: str = Form("taxable"),
    invoice_type_id: str = Form(""),
    bsb: str = Form(""),
    bank_account_number: str = Form(""),
    bank_account_name: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        clean_b = clean_bsb(bsb) if bsb else None
        clean_acc = bank_account_number.strip() if bank_account_number else None

        cred = Creditor(
            name=name.strip(),
            abn=digits_only(abn),
            default_account_id=int(default_account_id) if default_account_id else None,
            gst_treatment=gst_treatment,
            invoice_type_id=int(invoice_type_id) if invoice_type_id else None,
            bsb=clean_b,
            bank_account_number=clean_acc,
            bank_account_name=bank_account_name.strip() if bank_account_name else None,
            notes=notes.strip(),
        )
        session.add(cred)
        session.flush()

        if clean_b or clean_acc:
            log_creditor_audit(
                session,
                creditor_id=cred.id,
                action="INITIAL_BANK_SETUP",
                user=user,
                old_bsb=None,
                new_bsb=clean_b,
                old_acc=None,
                new_acc=clean_acc,
                details=f"Creditor profile registered by {user.username}.",
            )
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/creditors", status_code=303)


@app.post("/creditors/{creditor_id}/update")
def update_creditor(
    creditor_id: int,
    name: str = Form(...),
    abn: str = Form(""),
    default_account_id: str = Form(""),
    gst_treatment: str = Form("taxable"),
    invoice_type_id: str = Form(""),
    bsb: str = Form(""),
    bank_account_number: str = Form(""),
    bank_account_name: str = Form(""),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        c = session.get(Creditor, creditor_id)
        if c:
            new_clean_bsb = clean_bsb(bsb) if bsb else None
            new_clean_acc = bank_account_number.strip() if bank_account_number else None

            if c.bsb != new_clean_bsb or c.bank_account_number != new_clean_acc:
                log_creditor_audit(
                    session,
                    creditor_id=c.id,
                    action="MANUAL_BANK_UPDATE",
                    user=user,
                    old_bsb=c.bsb,
                    new_bsb=new_clean_bsb,
                    old_acc=c.bank_account_number,
                    new_acc=new_clean_acc,
                    details=f"Disbursement banking updated by approver {user.username}.",
                )

            c.name = name.strip()
            c.abn = digits_only(abn)
            c.default_account_id = int(default_account_id) if default_account_id else None
            c.gst_treatment = gst_treatment
            c.invoice_type_id = int(invoice_type_id) if invoice_type_id else None
            c.bsb = new_clean_bsb
            c.bank_account_number = new_clean_acc
            c.bank_account_name = bank_account_name.strip() if bank_account_name else None
            session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/creditors/{creditor_id}", status_code=303)


@app.post("/creditors/{creditor_id}/delete")
def delete_creditor(creditor_id: int, user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        c = session.get(Creditor, creditor_id)
        if c:
            for log in list(c.audit_logs):
                log.creditor_id = None
            log_creditor_audit(session, creditor_id=None, action="CREDITOR_DELETED", user=user, details=f"Deleted creditor profile '{c.name}' (ABN: {c.abn or 'None'}).")
            for inv in session.query(Invoice).filter_by(creditor_id=creditor_id):
                inv.creditor_id = None
            session.delete(c)
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/creditors", status_code=303)


# --- CENTRAL AUDIT LOG ROUTE ---
@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        inv_logs = session.query(AuditLog).all()
        cred_logs = session.query(CreditorAuditLog).all()

        combined = []
        for l in inv_logs:
            combined.append({
                "timestamp": l.timestamp,
                "domain": "Invoice",
                "username": l.username,
                "action": l.action,
                "entity_id": l.invoice_id,
                "entity_title": f"Invoice #{l.invoice.invoice_number or l.invoice_id}" if l.invoice else "Bill",
                "diff": None,
                "details": l.details,
            })

        for l in cred_logs:
            diff_str = None
            if l.old_bsb or l.new_bsb or l.old_account or l.new_account:
                diff_str = f"{l.old_bsb or 'None'}/{l.old_account or 'None'} → {l.new_bsb or 'None'}/{l.new_account or 'None'}"
            combined.append({
                "timestamp": l.timestamp,
                "domain": "Creditor",
                "username": l.username,
                "action": l.action,
                "entity_id": l.creditor_id,
                "entity_title": l.creditor.name if l.creditor else "Creditor",
                "diff": diff_str,
                "details": l.details,
            })

        combined.sort(key=lambda x: x["timestamp"], reverse=True)

        return TEMPLATES.TemplateResponse(
            "audit.html",
            ctx(request, all_events=combined),
        )
    finally:
        session.close()


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, user: User = Depends(get_current_user)):
    return TEMPLATES.TemplateResponse("search.html", ctx(request))


@app.get("/api/search")
def api_search(q: str = "", user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        results = []
        parsed_intent = None
        if q.strip():
            url = get_setting(session, "ollama_url", "http://127.0.0.1:11434")
            model = get_setting(session, "ollama_model") or None
            parsed_intent = parse_natural_query(q, url, model)
            results = execute_invoice_search(parsed_intent)

        return JSONResponse({
            "results": results,
            "parsed_intent": parsed_intent,
            "count": len(results),
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"results": [], "error": str(e), "parsed_intent": None, "count": 0},
        )
    finally:
        session.close()


@app.get("/inbox/cleared", response_class=HTMLResponse)
def inbox_cleared(request: Request, user: User = Depends(get_current_user)):
    return TEMPLATES.TemplateResponse("inbox_cleared.html", ctx(request))


@app.get("/mapping", response_class=HTMLResponse)
def mapping(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        return TEMPLATES.TemplateResponse(
            "mapping.html",
            ctx(
                request,
                types=session.query(InvoiceType).order_by(InvoiceType.name).all(),
                rules=session.query(MappingRule).order_by(MappingRule.priority).all(),
                accounts=session.query(Account).filter_by(archived=False).order_by(Account.code).all(),
            ),
        )
    finally:
        session.close()


@app.post("/mapping/types")
def save_type(
    name: str = Form(...),
    account_id: str = Form(""),
    gst_treatment: str = Form("taxable"),
    keywords: str = Form(""),
    type_id: str = Form(""),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        if type_id:
            t = session.get(InvoiceType, int(type_id))
            if t:
                t.name = name.strip()
                t.account_id = int(account_id) if account_id else None
                t.gst_treatment = gst_treatment
                t.keywords = keywords.strip()
        else:
            session.add(
                InvoiceType(
                    name=name.strip(),
                    account_id=int(account_id) if account_id else None,
                    gst_treatment=gst_treatment,
                    keywords=keywords.strip(),
                )
            )
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/mapping#types", status_code=303)


@app.post("/mapping/types/{type_id}/delete")
def delete_type(type_id: int, user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        t = session.get(InvoiceType, type_id)
        if t:
            for r in session.query(MappingRule).filter_by(invoice_type_id=type_id):
                r.invoice_type_id = None
            for c in session.query(Creditor).filter_by(invoice_type_id=type_id):
                c.invoice_type_id = None
            session.delete(t)
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/mapping#types", status_code=303)


@app.post("/mapping/rules")
def save_rule(
    name: str = Form(...),
    match_on: str = Form(...),
    pattern: str = Form(...),
    account_id: str = Form(""),
    gst_treatment: str = Form(""),
    invoice_type_id: str = Form(""),
    priority: int = Form(100),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        session.add(
            MappingRule(
                name=name.strip(),
                match_on=match_on,
                pattern=pattern.strip(),
                account_id=int(account_id) if account_id else None,
                gst_treatment=gst_treatment,
                invoice_type_id=int(invoice_type_id) if invoice_type_id else None,
                priority=priority,
            )
        )
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/mapping#rules", status_code=303)


@app.post("/mapping/rules/{rule_id}/delete")
def delete_rule(rule_id: int, user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        r = session.get(MappingRule, rule_id)
        if r:
            session.delete(r)
            session.commit()
    finally:
        session.close()
    return RedirectResponse("/mapping#rules", status_code=303)


@app.get("/mapping/test")
def mapping_test(
    supplier: str = "",
    abn: str = "",
    description: str = "",
    invoice_type_id: str = "",
    user: User = Depends(get_current_user),
):
    result = preview_resolution(
        supplier,
        abn,
        description,
        int(invoice_type_id) if invoice_type_id else None,
    )
    return JSONResponse(result)


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        rows = session.query(Account).order_by(Account.code).all()
        return TEMPLATES.TemplateResponse("accounts.html", ctx(request, accounts=rows))
    finally:
        session.close()


@app.post("/accounts")
def add_account(
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    gst_treatment: str = Form("taxable"),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        session.add(Account(code=code.strip(), name=name.strip(), type=type, gst_treatment=gst_treatment))
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/accounts", status_code=303)


@app.get("/journals", response_class=HTMLResponse)
def journals_list(request: Request, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        rows = session.query(Journal).order_by(Journal.id.desc()).all()
        return TEMPLATES.TemplateResponse("journals.html", ctx(request, journals=rows))
    finally:
        session.close()


@app.get("/journals/{journal_id}", response_class=HTMLResponse)
def journal_detail(request: Request, journal_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        j = session.get(Journal, journal_id)
        dr = sum((ln.debit or 0) for ln in j.lines)
        cr = sum((ln.credit or 0) for ln in j.lines)
        return TEMPLATES.TemplateResponse("journal_detail.html", ctx(request, j=j, dr=dr, cr=cr))
    finally:
        session.close()


@app.get("/journals/{journal_id}/csv")
def journal_csv(journal_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        j = session.get(Journal, journal_id)
        def csv_safe(value) -> str:
            text = "" if value is None else str(value)
            return "\t" + text if text.startswith(("=", "+", "-", "@")) else text

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Narration", "Account code", "Account name", "Description", "Debit", "Credit", "GST treatment", "BAS"])
        for ln in j.lines:
            w.writerow(
                [
                    csv_safe(j.date.isoformat() if j.date else ""),
                    csv_safe(j.narration),
                    csv_safe(ln.account.code),
                    csv_safe(ln.account.name),
                    csv_safe(ln.description),
                    f"{ln.debit:.2f}",
                    f"{ln.credit:.2f}",
                    ln.gst_treatment,
                    ln.bas_label,
                ]
            )
        data = buf.getvalue().encode("utf-8")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=journal-{j.id}.csv"},
        )
    finally:
        session.close()


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(require_approver)):
    session = SessionLocal()
    try:
        url = get_setting(session, "ollama_url")
        return TEMPLATES.TemplateResponse(
            "settings.html",
            ctx(
                request,
                entity_name=get_setting(session, "entity_name"),
                gst_registered=get_setting(session, "gst_registered") == "true",
                accounting_basis=get_setting(session, "accounting_basis"),
                bank_code=get_setting(session, "bank_code", "PCU"),
                user_apca_number=get_setting(session, "user_apca_number", "123456"),
                remitter_bsb=get_setting(session, "remitter_bsb", "085-005"),
                remitter_account=get_setting(session, "remitter_account", "123456789"),
                ollama_url=url,
                ollama_model=get_setting(session, "ollama_model"),
                ollama_ok=ping(url),
                models=list_models(url),
            ),
        )
    finally:
        session.close()


@app.post("/settings")
def save_settings(
    entity_name: str = Form(...),
    gst_registered: str = Form("false"),
    accounting_basis: str = Form("accrual"),
    bank_code: str = Form("PCU"),
    user_apca_number: str = Form("123456"),
    remitter_bsb: str = Form("085-005"),
    remitter_account: str = Form("123456789"),
    ollama_url: str = Form(...),
    ollama_model: str = Form(""),
    user: User = Depends(require_approver),
):
    session = SessionLocal()
    try:
        setting_keys = [
            "entity_name", "gst_registered", "accounting_basis", "bank_code",
            "user_apca_number", "remitter_bsb", "remitter_account",
            "ollama_url", "ollama_model",
        ]
        before = {key: get_setting(session, key) for key in setting_keys}

        set_setting(session, "entity_name", entity_name)
        set_setting(session, "gst_registered", "true" if gst_registered == "true" else "false")
        set_setting(session, "accounting_basis", accounting_basis)
        set_setting(session, "bank_code", bank_code.strip())
        set_setting(session, "user_apca_number", user_apca_number.strip())
        set_setting(session, "remitter_bsb", clean_bsb(remitter_bsb))
        set_setting(session, "remitter_account", remitter_account.strip())
        set_setting(session, "ollama_url", ollama_url.strip())
        set_setting(session, "ollama_model", ollama_model.strip())

        after = {key: get_setting(session, key) for key in setting_keys}
        changed = {key: {"before": before[key], "after": after[key]} for key in setting_keys if before[key] != after[key]}
        if changed:
            changed_text = json.dumps(changed, sort_keys=True)
            log_audit_event(session, "SETTINGS_CHANGED", user=user, details=changed_text[:4000])
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/settings", status_code=303)

@app.post("/change-password")
def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    user: User = Depends(get_current_user),
):
    session = SessionLocal()
    try:
        db_user = session.get(User, user.id)
        if not db_user or not verify_password(current_password, db_user.password_hash):
            raise HTTPException(status_code=400, detail="Current password incorrect.")
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
        from app.auth import hash_password
        db_user.password_hash = hash_password(new_password)
        log_audit_event(session, "PASSWORD_CHANGED", user=user, details="User updated password.")
        session.commit()
        return RedirectResponse("/?toast=password_updated", status_code=303)
    finally:
        session.close()