from __future__ import annotations

from pathlib import Path

import fitz

from app.config import DATA_DIR


def write_sample_invoice(dest: Path | None = None) -> Path:
    dest = dest or (DATA_DIR / "sample_origin_energy.pdf")
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    text = """TAX INVOICE

Origin Energy Electricity Ltd
ABN 33 071 052 287
Level 45, Australia Square, Sydney NSW 2000

Bill to:
Acme Pty Ltd
ABN 51 824 753 556
12 Example Street
Adelaide SA 5000

Invoice number: OE-10482
Invoice date: 15 August 2026
Due date: 5 September 2026
Supply address: 12 Example Street Adelaide SA 5000

Description                          Amount (inc GST)
Electricity usage — peak                  $220.00
Electricity usage — off-peak              $88.00
Supply charge                             $33.00
Network contribution                      $22.00

Subtotal (ex GST)                        $330.00
GST 10%                                   $33.00
Total amount payable                     $363.00

Please pay by EFT. This is a tax invoice for GST purposes.
"""
    page.insert_textbox(fitz.Rect(50, 50, 545, 780), text, fontsize=11, fontname="helv")
    doc.save(dest)
    doc.close()
    return dest
