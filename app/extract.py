from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import fitz
from dateutil import parser as dateparser

from app.abn import digits_only, is_valid_abn
from app.gst import gst_from_inclusive, money
from app.ollama_client import chat_json, list_models, pick_model

AI_SYSTEM_PROMPT = """You are an Accounts Payable and Tax Invoice AI Auditor.
Extract structured bookkeeping attributes from this invoice or bill text (handling both Australian and international invoices).

CRITICAL ENTITY DISAMBIGUATION RULES:
- "supplier_name" MUST be the selling business / vendor issuing the bill.
- NEVER use the customer / account holder name (the person or entity being billed).
- Look for the legal trading name near the top header, tax registration title, or invoice title.
- "line_items": Keep this concise. Summarize charges into at most 2-3 major line items.

INVOICE IDENTIFIER RULES:
- "invoice_number": Select the explicit commercial tax invoice or bill number (e.g. Invoice No, Tax Invoice #, Bill Number).
- Do NOT use the ongoing customer Account Number or Customer ID if an explicit Invoice Number exists.

TAX & JURISDICTION RULES:
- Australia: Standard GST is 10% (1/11th of GST-inclusive amount). Water, council rates, and financial charges are GST-free. Telco, energy, software, and general supplies are standard taxable.
- International / Overseas:
  - If the invoice is non-Australian, set "supplier_abn" to null.
  - Map any foreign tax to "gst_amount".
  - For international supplies without Australian GST registration, set line "gst_treatment" to "out_of_scope".

Return ONLY valid JSON matching this schema:
{
  "supplier_name": "Full legal or trading business name",
  "supplier_abn": "11 digit Australian ABN without spaces, or null if international / not present",
  "invoice_number": "Explicit invoice or bill number",
  "invoice_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "currency": "AUD" | "USD" | "GBP" | "EUR" | "NZD" | "CAD",
  "is_tax_invoice": true,
  "gst_inclusive": true,
  "subtotal_ex_gst": 0.00,
  "gst_amount": 0.00,
  "total": 0.00,
  "line_items": [
    {
      "description": "Item or service description",
      "amount": 0.00,
      "gst_amount": 0.00,
      "gst_treatment": "taxable" | "gst_free" | "input_taxed" | "out_of_scope"
    }
  ]
}
"""


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return dateparser.parse(str(value), dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def extract_pdf_text(path: Path) -> str:
    try:
        doc = fitz.open(path)
        parts = []
        for page in doc:
            parts.append(page.get_text("text") or "")
        doc.close()
        return "\n".join(parts).strip()
    except Exception:
        return ""


def render_page_base64(path: Path, page_index: int = 0) -> str | None:
    try:
        doc = fitz.open(path)
        if page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return base64.b64encode(png_bytes).decode("utf-8")
    except Exception:
        return None


def capture_document(path: Path) -> dict[str, Any]:
    text = extract_pdf_text(path)
    b64_image = None
    if len(text) < 40:
        b64_image = render_page_base64(path, 0)
    return {"text": text, "b64_image": b64_image}


def find_abn_candidates(text: str) -> list[str]:
    cands = []
    for m in re.finditer(r"(?:ABN|A\.B\.N\.?)[:|.\s]*([0-9\s]{11,18})", text, re.IGNORECASE):
        clean = digits_only(m.group(1))
        if len(clean) == 11 and clean not in cands:
            cands.append(clean)
    for m in re.finditer(r"\b\d{2}\s*\d{3}\s*\d{3}\s*\d{3}\b", text):
        clean = digits_only(m.group(0))
        if len(clean) == 11 and clean not in cands:
            cands.append(clean)
    return cands


def find_invoice_number_candidates(text: str) -> list[str]:
    """
    Layout-agnostic candidate finder:
    Handles same-line label/values, table cells, and two-row stacked layouts.
    """
    candidates: list[str] = []
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    
    label_patterns = [
        r"^tax\s*invoice\s*(?:no\.?|number|#)?$",
        r"^invoice\s*(?:no\.?|number|#)?$",
        r"^bill\s*(?:no\.?|number|#)?$",
        r"^inv\s*(?:no\.?|#)?$",
    ]
    stopwords = {
        "date", "issue", "due", "total", "amount", "tax", "gst", "statement", 
        "summary", "details", "page", "period", "charges", "slip", "nab", "cba"
    }

    for i, line in enumerate(lines):
        line_clean = line.strip()
        
        # 1. Inline match (e.g. "Invoice No: 0986410726" or "Invoice # 44021")
        m_inline = re.search(
            r"(?i)(?:Invoice\s*(?:No\.?|Number|#)|Tax\s*Invoice\s*(?:No\.?|#)|Bill\s*(?:Number|No\.?|#))\s*[:.\s#]*([A-Z0-9\-_/]{3,})",
            line_clean
        )
        if m_inline:
            val = m_inline.group(1).strip()
            if val.lower() not in stopwords and any(c.isdigit() for c in val):
                candidates.append(val)

        # 2. Stacked layout match (Label on line i, token on line i+1)
        for pat in label_patterns:
            if re.match(pat, line_clean, re.IGNORECASE):
                if i + 1 < len(lines):
                    next_token = lines[i + 1].strip()
                    # Verify next token looks like an identifier rather than a word or date
                    if (
                        len(next_token) >= 3 
                        and next_token.lower() not in stopwords 
                        and any(c.isdigit() for c in next_token)
                        and not re.search(r"[/.-]\d{2,4}$", next_token) # Avoid dates like 05/08/26
                        and not next_token.startswith("$")
                    ):
                        candidates.append(next_token)

    # Deduplicate while preserving order
    return list(dict.fromkeys(candidates))


def find_total_candidates(text: str) -> tuple[Decimal | None, Decimal | None]:
    total_patterns = [
        r"(?:Total\s*amount\s*due|Amount\s*(?:Payable|Due)|Total\s*charges|Total\s*(?:\(Incl\.?\s*GST\)|Inc\.?\s*GST|Due)?)\s*[:.\s|]*\$?\s*([0-9,]+\.[0-9]{2})",
        r"\$?\s*([0-9,]+\.[0-9]{2})\s*\n\s*Total\s*(?:charges|amount|due)",
        r"(?:AMOUNT\s*DUE|TOTAL\s*DUE|AMOUNT\s*PAYABLE)[\s\S]{0,100}?\$?\s*([0-9,]+\.[0-9]{2})",
    ]
    gst_patterns = [
        r"(?:Total\s*GST|GST\s*Amount|Includes\s*GST\s*of|Includes\s*GST|GST)\s*[:.\s|]*\$?\s*([0-9,]+\.[0-9]{2})",
        r"Includes\s*GST\s*of\s*\$?\s*([0-9,]+\.[0-9]{2})",
    ]

    tot = None
    for p in total_patterns:
        matches = re.findall(p, text, re.IGNORECASE)
        if matches:
            try:
                tot = money(matches[-1].replace(",", ""))
                break
            except Exception:
                pass

    gst = None
    for p in gst_patterns:
        matches = re.findall(p, text, re.IGNORECASE)
        if matches:
            try:
                gst = money(matches[-1].replace(",", ""))
                break
            except Exception:
                pass

    return tot, gst


def regex_fallback_extract(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    supplier = ""
    for ln in lines[:8]:
        low = ln.lower()
        if not any(h in low for h in ("tax invoice", "statement", "bill", "abn", "account", "date", "page", "tel:", "phone")):
            supplier = ln
            break

    abns = find_abn_candidates(text)
    abn = abns[0] if abns else ""

    m_inv = re.search(r"(?:Invoice\s*Date|Issued\s*on|Date|Bill\s*Date)\s*[:\s]*([0-9A-Za-z\s\/\-\.]+)", text, re.I)
    inv_date = parse_date(m_inv.group(1)) if m_inv else None

    m_due = re.search(r"(?:Payment\s*Due\s*Date|Due\s*Date|Pay\s*by\s*date|Due|Pay\s*by)\s*[:\s]*([0-9A-Za-z\s\/\-\.]+)", text, re.I)
    due = parse_date(m_due.group(1)) if m_due else None

    inv_candidates = find_invoice_number_candidates(text)
    if inv_candidates:
        inv_no = inv_candidates[0]
    else:
        m_acc = re.search(r"(?:Account\s*(?:Number|No\.?|#))\s*[:.\s#]*([A-Z0-9\-_/]+)", text, re.I)
        inv_no = m_acc.group(1).strip() if m_acc else ""

    tot, gst_val = find_total_candidates(text)
    total = tot or Decimal("0.00")
    gst = gst_val or Decimal("0.00")

    is_gst_free = any(w in text.lower() for w in ("sa water", "water charges", "rates", "council rate", "gst-free", "gst free"))
    if not is_gst_free and gst == 0 and total > 0:
        gst = gst_from_inclusive(total)

    sub = total - gst

    return {
        "supplier_name": supplier[:120],
        "supplier_abn": abn,
        "invoice_number": inv_no,
        "invoice_date": inv_date.isoformat() if inv_date else None,
        "due_date": due.isoformat() if due else None,
        "currency": "AUD",
        "is_tax_invoice": True,
        "gst_inclusive": True,
        "subtotal_ex_gst": sub,
        "gst_amount": gst,
        "total": total,
        "line_items": [
            {
                "description": f"{supplier or 'Invoice'} charges",
                "amount": total,
                "gst_amount": gst,
                "gst_treatment": "gst_free" if is_gst_free else "taxable",
            }
        ] if total > 0 else [],
    }


def run_hybrid_extract(
    text: str,
    ollama_url: str,
    model: str | None = None,
    b64_image: str | None = None,
) -> tuple[dict[str, Any], str]:
    abns = find_abn_candidates(text)
    inv_candidates = find_invoice_number_candidates(text)
    reg_tot, reg_gst = find_total_candidates(text)

    hints = []
    if abns:
        hints.append(f"Detected ABN candidates: {', '.join(abns)}")
    if inv_candidates:
        hints.append(f"Detected Invoice Number Candidates: {', '.join(inv_candidates)}")
    if reg_tot:
        hints.append(f"Detected Gross Total: ${reg_tot:.2f}")
    if reg_gst:
        hints.append(f"Detected GST Amount: ${reg_gst:.2f}")

    hint_str = f"\n[Document Hints: {'; '.join(hints)}]" if hints else ""

    available = list_models(ollama_url)

    if not text.strip() and b64_image:
        chosen_model = model or pick_model(available, prefer_vision=True) or "qwen2.5-vl:latest"
        user_prompt = "Extract all invoice details and tax totals from this scanned image."
        images = [b64_image]
    else:
        chosen_model = model or pick_model(available, prefer_vision=False) or "qwen2.5:3b"
        user_prompt = (
            f"Extract all invoice details from this document text.{hint_str}\n\n"
            f"--- DOCUMENT TEXT ---\n{text[:2500]}"
        )
        images = None

    data = None
    method = "Regex Fallback"

    try:
        data = chat_json(
            base_url=ollama_url,
            model=chosen_model,
            system=AI_SYSTEM_PROMPT,
            user=user_prompt,
            images=images,
        )
        if data and isinstance(data, dict):
            method = f"AI ({chosen_model})"
    except Exception:
        data = None

    if not data or not isinstance(data, dict):
        data = regex_fallback_extract(text)
        method = "Deterministic Regex Heuristics"

    return data, method


def normalize_extracted(data: dict[str, Any], raw_text: str) -> dict[str, Any]:
    abns = find_abn_candidates(raw_text)
    ai_abn = digits_only(str(data.get("supplier_abn") or ""))
    abn = ai_abn if len(ai_abn) == 11 else (abns[0] if abns else "")

    reg_tot, reg_gst = find_total_candidates(raw_text)
    total = money(data.get("total") or 0)
    if total == 0 and reg_tot:
        total = reg_tot

    gst = money(data.get("gst_amount") or 0)
    if gst == 0 and reg_gst:
        gst = reg_gst

    is_water_or_rates = any(w in raw_text.lower() for w in ("sa water", "water charges", "rates", "council rate"))
    if is_water_or_rates:
        gst = Decimal("0.00")
    elif gst == 0 and total > 0 and data.get("currency", "AUD") == "AUD":
        gst = gst_from_inclusive(total)

    sub = money(data.get("subtotal_ex_gst") or 0)
    if sub == 0 or sub == total:
        sub = total - gst

    raw_lines = data.get("line_items") or data.get("lines") or []
    norm_lines = []
    for ln in raw_lines:
        if not isinstance(ln, dict):
            continue
        desc = str(ln.get("description") or "").strip()
        amt = money(ln.get("amount") or ln.get("total") or 0)
        if not desc and amt == 0:
            continue
        ln_gst = gst if (len(raw_lines) <= 1 and gst > 0) else money(ln.get("gst_amount") or (Decimal("0.00") if is_water_or_rates else gst_from_inclusive(amt)))
        norm_lines.append({
            "description": desc or "Item",
            "amount": amt,
            "gst_amount": ln_gst,
            "gst_treatment": "gst_free" if (is_water_or_rates or ln_gst == 0) else (ln.get("gst_treatment") or "taxable"),
        })

    if not norm_lines and total > 0:
        norm_lines = [{
            "description": str(data.get("supplier_name") or "Invoice supply charges"),
            "amount": total,
            "gst_amount": gst,
            "gst_treatment": "gst_free" if is_water_or_rates else "taxable",
        }]
    elif len(norm_lines) == 1 and norm_lines[0]["amount"] != total:
        if abs((norm_lines[0]["amount"] + gst) - total) <= Decimal("0.05"):
            norm_lines[0]["amount"] = total
            norm_lines[0]["gst_amount"] = gst

    # Disambiguate Invoice Number vs Account Number
    inv_candidates = find_invoice_number_candidates(raw_text)
    ai_inv_no = str(data.get("invoice_number") or "").strip()

    if inv_candidates:
        # If AI picked an Account Number while explicit invoice candidates exist, promote candidate #1
        if not ai_inv_no or (ai_inv_no not in inv_candidates and re.search(r"Account\s*(?:No|Number)?\s*[:.\s#]*" + re.escape(ai_inv_no), raw_text, re.I)):
            final_inv_no = inv_candidates[0]
        else:
            final_inv_no = ai_inv_no
    else:
        final_inv_no = ai_inv_no

    return {
        "supplier_name": str(data.get("supplier_name") or "").strip(),
        "supplier_abn": abn,
        "abn_valid": is_valid_abn(abn) if abn else False,
        "invoice_number": final_inv_no,
        "invoice_date": parse_date(data.get("invoice_date")),
        "due_date": parse_date(data.get("due_date")),
        "currency": data.get("currency") or "AUD",
        "is_tax_invoice": bool(data.get("is_tax_invoice", True)),
        "gst_inclusive": bool(data.get("gst_inclusive", True)),
        "subtotal_ex_gst": sub,
        "gst_amount": gst,
        "total": total,
        "lines": norm_lines,
        "notes": str(data.get("notes") or ""),
    }
