from __future__ import annotations

import os

from app.auth import hash_password
from app.models import Account, Creditor, InvoiceType, SessionLocal, Setting, User


def seed_if_empty() -> None:
    session = SessionLocal()
    try:
        # 1. Seed Default Users
        if session.query(User).count() == 0:
            session.add_all([
                User(
                    username="clerk",
                    full_name="Accounts Clerk",
                    role="operator",
                    password_hash=hash_password(os.getenv("DEFAULT_CLERK_PASSWORD", "password123")),
                ),
                User(
                    username="manager",
                    full_name="Financial Controller",
                    role="approver",
                    password_hash=hash_password(os.getenv("DEFAULT_MANAGER_PASSWORD", "password123")),
                ),
            ])
            session.commit()

        # 2. Seed Default Settings
        if session.query(Setting).count() == 0:
            session.add_all([
                Setting(key="entity_name", value="My Australian Business Pty Ltd"),
                Setting(key="gst_registered", value="true"),
                Setting(key="accounting_basis", value="accrual"),
                Setting(key="ollama_url", value=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")),
                Setting(key="ollama_model", value=""),
            ])
            session.commit()

        # 3. Seed Standard Australian Chart of Accounts
        required_accounts = [
            ("1-1100", "Main Operating Bank Account", "asset", "bas_excluded"),
            ("1-1200", "GST Receivable (1B Input Credits)", "asset", "taxable"),
            ("1-1600", "Computer & Electronic Equipment", "asset", "taxable"),
            ("2-1100", "Accounts Payable (Trade Creditors)", "liability", "bas_excluded"),
            ("6-1100", "Electricity & Gas", "expense", "taxable"),
            ("6-1200", "Computer, Software & IT", "expense", "taxable"),
            ("6-1300", "Telecommunications & Internet", "expense", "taxable"),
            ("6-1400", "Rent & Premises Costs", "expense", "taxable"),
            ("6-1500", "Advertising & Marketing", "expense", "taxable"),
            ("6-1600", "Accounting & Legal Fees", "expense", "taxable"),
            ("6-1700", "Bank & Merchant Fees", "expense", "input_taxed"),
            ("6-1800", "Council Rates & Water Charges", "expense", "gst_free"),
            ("6-9000", "General & Unallocated Expenses", "expense", "taxable"),
            ("6-9900", "Rounding & Penny Suspense", "expense", "out_of_scope"),
        ]

        for code, name, acc_type, gst_treat in required_accounts:
            existing = session.query(Account).filter_by(code=code).first()
            if not existing:
                session.add(Account(code=code, name=name, type=acc_type, gst_treatment=gst_treat))
        session.commit()

        # 4. Seed Standard Expense Categories
        if session.query(InvoiceType).count() == 0:
            it_acc = session.query(Account).filter_by(code="6-1200").first()
            util_acc = session.query(Account).filter_by(code="6-1100").first()
            tel_acc = session.query(Account).filter_by(code="6-1300").first()
            session.add_all([
                InvoiceType(name="Software & Cloud", account_id=it_acc.id if it_acc else None, gst_treatment="taxable", keywords="software,subscription,saas,cloud,adobe,github"),
                InvoiceType(name="Utilities", account_id=util_acc.id if util_acc else None, gst_treatment="taxable", keywords="energy,gas,power,water,electricity,agl,origin"),
                InvoiceType(name="Telecommunications", account_id=tel_acc.id if tel_acc else None, gst_treatment="taxable", keywords="broadband,phone,mobile,internet,flip,telstra,belong,service charges"),
            ])
            session.commit()

        # 5. Seed Recognized Australian Creditors
        if session.query(Creditor).count() == 0:
            c_util = session.query(InvoiceType).filter_by(name="Utilities").first()
            c_tel = session.query(InvoiceType).filter_by(name="Telecommunications").first()
            session.add_all([
                Creditor(name="AGL Energy", abn="74 115 061 375", invoice_type_id=c_util.id if c_util else None, gst_treatment="taxable"),
                Creditor(name="Origin Energy", abn="30 000 051 696", invoice_type_id=c_util.id if c_util else None, gst_treatment="taxable"),
                Creditor(name="Belong", abn="64 086 174 781", invoice_type_id=c_tel.id if c_tel else None, gst_treatment="taxable"),
                Creditor(name="Telstra", abn="33 051 775 556", invoice_type_id=c_tel.id if c_tel else None, gst_treatment="taxable"),
                Creditor(name="FLIP TV Pty Ltd", abn="78 600 712 230", invoice_type_id=c_tel.id if c_tel else None, gst_treatment="taxable"),
            ])
            session.commit()
    finally:
        session.close()
