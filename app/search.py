from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.gst import money
from app.models import Account, Invoice, InvoiceLine, InvoiceType, SessionLocal
from app.ollama_client import chat_json, list_models, pick_model

SEARCH_SYSTEM = """You are an intelligent search query parser for an Australian general ledger and Accounts Payable system.
Convert the user's natural language search query into structured search filters.
Current date context: August 2026.

Return ONLY valid JSON matching this schema:
{
  "supplier_keywords": [string],
  "ledger_keywords": [string],
  "content_keywords": [string],
  "min_amount": number or null,
  "max_amount": number or null,
  "start_date": "YYYY-MM-DD" or null,
  "end_date": "YYYY-MM-DD" or null,
  "status": "posted"|"reviewed"|"imported"|null
}
Rules:
- Specific vendor/trading names go into supplier_keywords.
- Words describing general ledger accounts, categories, or expense types (e.g. 'utilities', 'software', 'rent', 'electricity', 'telecom', 'internet', 'phone') go into BOTH ledger_keywords and content_keywords.
- Extract numeric thresholds (e.g. 'over $50' -> min_amount: 50.0).
- Extract ISO date ranges if months, years, or dates are mentioned.
"""


def parse_natural_query(query: str, ollama_url: str, model: str | None = None) -> dict[str, Any]:
    """Uses the local model to parse query constraints, falling back to deterministic token extraction."""
    if not query:
        return {}

    try:
        available = list_models(ollama_url)
        chosen = model or pick_model(available, prefer_vision=False)
        if chosen:
            data = chat_json(
                base_url=ollama_url,
                model=chosen,
                system=SEARCH_SYSTEM,
                user=f"Search Query: {query}",
            )
            if isinstance(data, dict):
                data.setdefault("supplier_keywords", [])
                data.setdefault("ledger_keywords", [])
                data.setdefault("content_keywords", [])
                return data
    except Exception:
        pass

    # Deterministic regex fallback
    min_amt = None
    m = re.search(r"(?:over|above|greater than|>\s*)\$?(\d+(?:\.\d{2})?)", query, re.I)
    if m:
        try:
            min_amt = float(m.group(1))
        except ValueError:
            min_amt = None

    tokens = [w for w in query.split() if len(w) >= 2]
    return {
        "supplier_keywords": tokens,
        "ledger_keywords": tokens,
        "content_keywords": [query] + tokens,
        "min_amount": min_amt,
        "max_amount": None,
        "start_date": None,
        "end_date": None,
        "status": None,
    }


def execute_invoice_search(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Queries SQLite and serializes the result dictionaries inside the session context."""
    session = SessionLocal()
    try:
        q = session.query(Invoice).options(
            joinedload(Invoice.lines).joinedload(InvoiceLine.account),
            joinedload(Invoice.invoice_type),
        )

        if not parsed:
            invoices = q.order_by(Invoice.id.desc()).all()
            return [_serialize_invoice(inv) for inv in invoices]

        if parsed.get("status"):
            q = q.filter(Invoice.status == parsed["status"])

        if parsed.get("min_amount") is not None:
            q = q.filter(Invoice.total >= money(parsed["min_amount"]))
        if parsed.get("max_amount") is not None:
            q = q.filter(Invoice.total <= money(parsed["max_amount"]))

        if parsed.get("start_date"):
            try:
                q = q.filter(Invoice.invoice_date >= date.fromisoformat(parsed["start_date"]))
            except (ValueError, TypeError):
                pass
        if parsed.get("end_date"):
            try:
                q = q.filter(Invoice.invoice_date <= date.fromisoformat(parsed["end_date"]))
            except (ValueError, TypeError):
                pass

        conditions = []

        for term in parsed.get("supplier_keywords", []):
            if term:
                conditions.append(Invoice.supplier_name.ilike(f"%{term}%"))
                conditions.append(Invoice.filename.ilike(f"%{term}%"))

        for term in parsed.get("content_keywords", []):
            if term:
                conditions.append(Invoice.raw_text.ilike(f"%{term}%"))
                conditions.append(Invoice.invoice_number.ilike(f"%{term}%"))

        ledger_terms = parsed.get("ledger_keywords", [])
        if ledger_terms:
            account_conditions = []
            for term in ledger_terms:
                if term:
                    account_conditions.append(Account.name.ilike(f"%{term}%"))
                    account_conditions.append(Account.code.ilike(f"%{term}%"))

            matched_accounts = session.query(Account.id).filter(or_(*account_conditions)).all()
            matched_acc_ids = [a[0] for a in matched_accounts]

            type_conditions = []
            for term in ledger_terms:
                if term:
                    type_conditions.append(InvoiceType.name.ilike(f"%{term}%"))
                    type_conditions.append(InvoiceType.keywords.ilike(f"%{term}%"))

            matched_types = session.query(InvoiceType.id).filter(or_(*type_conditions)).all()
            matched_type_ids = [t[0] for t in matched_types]

            if matched_type_ids:
                conditions.append(Invoice.invoice_type_id.in_(matched_type_ids))

            if matched_acc_ids:
                matched_line_invoice_ids = (
                    session.query(InvoiceLine.invoice_id)
                    .filter(InvoiceLine.account_id.in_(matched_acc_ids))
                    .distinct()
                    .all()
                )
                line_inv_ids = [li[0] for li in matched_line_invoice_ids]
                if line_inv_ids:
                    conditions.append(Invoice.id.in_(line_inv_ids))

        if conditions:
            q = q.filter(or_(*conditions))

        invoices = q.distinct().order_by(Invoice.invoice_date.desc().nullslast(), Invoice.id.desc()).all()
        return [_serialize_invoice(inv) for inv in invoices]
    finally:
        session.close()


def _serialize_invoice(inv: Invoice) -> dict[str, Any]:
    primary_ledger = "Unallocated"
    if inv.lines and len(inv.lines) > 0 and inv.lines[0].account:
        primary_ledger = f"{inv.lines[0].account.code} {inv.lines[0].account.name}"
    elif inv.invoice_type:
        primary_ledger = inv.invoice_type.name

    return {
        "id": inv.id,
        "date": inv.invoice_date.isoformat() if inv.invoice_date else "—",
        "supplier": inv.supplier_name or inv.filename,
        "invoice_number": inv.invoice_number or "—",
        "gst_amount": f"${inv.gst_amount:,.2f}" if inv.gst_amount is not None else "—",
        "total": f"${inv.total:,.2f}" if inv.total is not None else "—",
        "status": inv.status,
        "ledger": primary_ledger,
    }