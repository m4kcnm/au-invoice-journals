from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Sequence

from app.models import Invoice


def clean_bsb(bsb: str | None) -> str:
    """Returns 6 digits formatted as XXX-XXX."""
    digits = re.sub(r"\D", "", bsb or "")
    if len(digits) == 6:
        return f"{digits[:3]}-{digits[3:]}"
    return "000-000"


def clean_account_num(acc: str | None) -> str:
    """Returns right-aligned account number padded up to 9 chars."""
    digits = re.sub(r"\D", "", acc or "")
    return digits[:9]


def generate_aba_file(
    invoices: Sequence[Invoice],
    *,
    bank_code: str,
    user_name: str,
    user_apca_number: str,
    remitter_bsb: str,
    remitter_account: str,
    processing_date: date | None = None,
) -> str:
    p_date = processing_date or date.today()
    date_str = p_date.strftime("%d%m%y")

    user_name_padded = (user_name[:26]).ljust(26)
    apca_padded = re.sub(r"\D", "", user_apca_number or "0").zfill(6)[:6]
    bank_code_padded = (bank_code[:3]).upper().ljust(3)

    header = (
        f"0"
        f"{' ' * 17}"
        f"01"
        f"{bank_code_padded}"
        f"{' ' * 7}"
        f"{user_name_padded}"
        f"{apca_padded}"
        f"{'PAYMENTS'.ljust(12)}"
        f"{date_str}"
        f"{' ' * 40}"
    )
    if len(header) != 120:
        raise ValueError(f"ABA Header must be exactly 120 characters, got {len(header)}")

    lines = [header]
    total_cents = 0
    record_count = 0

    clean_remitter_bsb = clean_bsb(remitter_bsb)
    clean_remitter_acc = clean_account_num(remitter_account).rjust(9)

    for inv in invoices:
        cred = inv.creditor
        if not cred or not cred.bsb or not cred.bank_account_number:
            continue

        amount_cents = int(round(Decimal(str(inv.total or 0)) * 100))
        if amount_cents <= 0:
            continue

        target_bsb = clean_bsb(cred.bsb)
        target_acc = clean_account_num(cred.bank_account_number).rjust(9)
        tax_indicator = " "
        txn_code = "50"
        amt_padded = str(amount_cents).zfill(10)

        payee_title = (cred.bank_account_name or cred.name or "Creditor")[:32].ljust(32)
        ref_text = f"INV {inv.invoice_number or inv.id}"[:18].ljust(18)
        remitter_short = user_name[:16].ljust(16)

        record = (
            f"1"
            f"{target_bsb}"
            f"{target_acc}"
            f"{tax_indicator}"
            f"{txn_code}"
            f"{amt_padded}"
            f"{payee_title}"
            f"{ref_text}"
            f"{clean_remitter_bsb}"
            f"{clean_remitter_acc}"
            f"{remitter_short}"
            f"00000000"
        )
        if len(record) != 120:
            raise ValueError(f"ABA Detail line must be exactly 120 characters, got {len(record)}")
        lines.append(record)

        total_cents += amount_cents
        record_count += 1

    net_total_padded = str(total_cents).zfill(10)
    count_padded = str(record_count).zfill(6)

    trailer = (
        f"7"
        f"999-999"
        f"{' ' * 12}"
        f"{net_total_padded}"
        f"{net_total_padded}"
        f"{'0' * 10}"
        f"{' ' * 24}"
        f"{count_padded}"
        f"{' ' * 40}"
    )
    if len(trailer) != 120:
        raise ValueError(f"ABA Trailer must be exactly 120 characters, got {len(trailer)}")
    lines.append(trailer)

    return "\r\n".join(lines) + "\r\n"
