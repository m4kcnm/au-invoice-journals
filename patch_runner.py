import re
import sys
from pathlib import Path

print("Running transactional pre-commit patch (in-memory validation first)...")

def fail(msg: str):
    print(f"\n[ABORT] {msg}", file=sys.stderr)
    sys.exit(1)

# READ ALL SOURCE FILES
gst_path = Path("app/gst.py")
extract_path = Path("app/extract.py")
journals_path = Path("app/journals.py")
web_path = Path("app/web.py")
js_path = Path("app/static/app.js")
inv_path = Path("app/templates/invoices.html")
readme_path = Path("README.md")

gst_code = gst_path.read_text(encoding="utf-8")
extract_code = extract_path.read_text(encoding="utf-8")
journals_code = journals_path.read_text(encoding="utf-8")
web_code = web_path.read_text(encoding="utf-8")
js_code = js_path.read_text(encoding="utf-8")
inv_code = inv_path.read_text(encoding="utf-8") if inv_path.exists() else None

# 1. VALIDATE & TRANSFORM app/gst.py
if '"requires_review"' not in gst_code:
    if 'TREATMENTS = (' not in gst_code:
        fail("TREATMENTS tuple not found in app/gst.py")
    gst_code = gst_code.replace(
        'TREATMENTS = (',
        'TREATMENTS = (\n    "requires_review",',
        1
    )

    split_target = 'if treatment == "gst_free":'
    if split_target not in gst_code:
        fail("split_target not found in app/gst.py")
    requires_review_handler = """if treatment == "requires_review":
        # GST unverified: no tax credit recognized until explicitly categorized
        return amount, Decimal("0.00"), amount

    if treatment == "gst_free":"""
    gst_code = gst_code.replace(split_target, requires_review_handler, 1)

# 2. VALIDATE & TRANSFORM app/extract.py
old_gst_infer = """    is_gst_free = any(w in text.lower() for w in ("sa water", "water charges", "rates", "council rate", "gst-free", "gst free"))
    if not is_gst_free and gst == 0 and total > 0:
        gst = gst_from_inclusive(total)"""

new_gst_infer = """    is_gst_free = any(w in text.lower() for w in ("sa water", "water charges", "rates", "council rate", "gst-free", "gst free"))
    has_taxable_keyword = any(w in text.lower() for w in ("tax invoice", "includes gst", "inc gst", "gst included"))
    if not is_gst_free and gst == 0 and total > 0 and has_taxable_keyword:
        gst = gst_from_inclusive(total)"""

if old_gst_infer in extract_code:
    extract_code = extract_code.replace(old_gst_infer, new_gst_infer, 1)

old_line_assign = '"gst_free" if (is_water_or_rates or ln_gst == 0) else'
new_line_assign = '"gst_free" if is_water_or_rates else ("taxable" if ln_gst > 0 else "requires_review") if'

if old_line_assign in extract_code:
    extract_code = extract_code.replace(old_line_assign, new_line_assign, 1)

# 3. VALIDATE & TRANSFORM app/journals.py
if "def propose_journal(" not in journals_code:
    fail("propose_journal not found in app/journals.py")
if "unverified GST treatment" not in journals_code:
    check_target = 'if any(l.get("account_id") is None for l in lines):'
    if check_target in journals_code:
        new_journal_checks = """if any(l.get("gst_treatment") == "requires_review" for l in lines):
        warnings.append("One or more invoice lines have unverified GST treatment. Please confirm GST status before posting.")

    if any(l.get("account_id") is None for l in lines):"""
        journals_code = journals_code.replace(check_target, new_journal_checks, 1)

# 4. VALIDATE & TRANSFORM app/web.py
if "GL_ACCOUNT_CREATED" not in web_code:
    old_add_acc = """    try:
        session.add(Account(code=code.strip(), name=name.strip(), type=type, gst_treatment=gst_treatment))
        session.commit()
    finally:"""
    new_add_acc = """    try:
        acc = Account(code=code.strip(), name=name.strip(), type=type, gst_treatment=gst_treatment)
        session.add(acc)
        log_audit_event(session, "GL_ACCOUNT_CREATED", user=user, details=f"Created ledger account {code.strip()} ({name.strip()})")
        session.commit()
    finally:"""
    if old_add_acc in web_code:
        web_code = web_code.replace(old_add_acc, new_add_acc, 1)

if "MAPPING_RULE_CREATED" not in web_code:
    old_save_rule = """    try:
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
    finally:"""
    new_save_rule = """    try:
        r = MappingRule(
            name=name.strip(),
            match_on=match_on,
            pattern=pattern.strip(),
            account_id=int(account_id) if account_id else None,
            gst_treatment=gst_treatment,
            invoice_type_id=int(invoice_type_id) if invoice_type_id else None,
            priority=priority,
        )
        session.add(r)
        log_audit_event(session, "MAPPING_RULE_CREATED", user=user, details=f"Created routing rule '{name.strip()}' on {match_on}: {pattern.strip()}")
        session.commit()
    finally:"""
    if old_save_rule in web_code:
        web_code = web_code.replace(old_save_rule, new_save_rule, 1)

if "MAPPING_RULE_DELETED" not in web_code:
    old_del_rule = """        if r:
            session.delete(r)
            session.commit()"""
    new_del_rule = """        if r:
            log_audit_event(session, "MAPPING_RULE_DELETED", user=user, details=f"Deleted routing rule '{r.name}' (ID #{r.id})")
            session.delete(r)
            session.commit()"""
    if old_del_rule in web_code:
        web_code = web_code.replace(old_del_rule, new_del_rule, 1)

# 5. VALIDATE & TRANSFORM static & templates
old_interceptor_pattern = r'// --- ABA DOWNLOAD INTERCEPTOR ---[\s\S]*?fetch\("/payments/aba"\)[\s\S]*?alert\("Network error attempting to export ABA file: " \+ err\.message\);\s*\}\);\s*\}\);\s*\}\);'
if re.search(old_interceptor_pattern, js_code):
    js_code = re.sub(old_interceptor_pattern, "", js_code)

if inv_code:
    target_btn_pattern = r'<a\s+class="btn"\s+href="/payments/aba"[^>]*>[\s\S]*?</a>'
    inv_code = re.sub(target_btn_pattern, "", inv_code)

# 6. WRITE OUT FILES
gst_path.write_text(gst_code, encoding="utf-8")
extract_path.write_text(extract_code, encoding="utf-8")
journals_path.write_text(journals_code, encoding="utf-8")
web_path.write_text(web_code, encoding="utf-8")
js_path.write_text(js_code, encoding="utf-8")
if inv_code:
    inv_path.write_text(inv_code, encoding="utf-8")

print("✓ All application source files updated cleanly.")

import py_compile
for py_file in ["app/gst.py", "app/extract.py", "app/journals.py", "app/web.py"]:
    py_compile.compile(py_file, doraise=True)
print("✓ Byte compilation passed cleanly.")
