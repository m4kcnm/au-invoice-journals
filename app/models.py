from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "app.db"
ENGINE = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30.0},
)


@event.listens_for(ENGINE, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    full_name = Column(String(128), nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False, default="operator")
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(64), nullable=False, default="System")
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    action = Column(String(64), nullable=False)
    details = Column(Text, nullable=True)

    user = relationship("User")
    invoice = relationship("Invoice", back_populates="audit_logs")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)


def get_setting(session, key: str, default: str = "") -> str:
    s = session.get(Setting, key)
    return s.value if s else default


def set_setting(session, key: str, value: str) -> None:
    s = session.get(Setting, key)
    if s:
        s.value = value
    else:
        session.add(Setting(key=key, value=value))


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False)
    gst_treatment = Column(String(32), default="taxable")
    archived = Column(Boolean, default=False)


class InvoiceType(Base):
    __tablename__ = "invoice_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    gst_treatment = Column(String(32), default="taxable")
    keywords = Column(Text, default="")

    account = relationship("Account")


class Creditor(Base):
    __tablename__ = "creditors"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    abn = Column(String(14), nullable=True, index=True)
    default_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    gst_treatment = Column(String(32), default="taxable")
    invoice_type_id = Column(Integer, ForeignKey("invoice_types.id"), nullable=True)
    notes = Column(Text, default="")

    # Banking details for Australian direct entry (ABA)
    bsb = Column(String(7), nullable=True)
    bank_account_number = Column(String(10), nullable=True)
    bank_account_name = Column(String(32), nullable=True)

    default_account = relationship("Account")
    invoice_type = relationship("InvoiceType")


class MappingRule(Base):
    __tablename__ = "mapping_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    priority = Column(Integer, default=100)
    match_on = Column(String(32), nullable=False)
    pattern = Column(String(128), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    gst_treatment = Column(String(32), nullable=True)
    invoice_type_id = Column(Integer, ForeignKey("invoice_types.id"), nullable=True)

    account = relationship("Account")
    invoice_type = relationship("InvoiceType")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    filename = Column(String(256), nullable=False)
    stored_path = Column(String(512), nullable=False)
    raw_text = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(32), default="imported")

    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    supplier_name = Column(String(128), nullable=True, index=True)
    supplier_abn = Column(String(14), nullable=True)
    invoice_number = Column(String(64), nullable=True)
    invoice_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)

    is_tax_invoice = Column(Boolean, default=True)
    gst_inclusive = Column(Boolean, default=True)
    gst_registered = Column(Boolean, default=True)

    subtotal_ex_gst = Column(Numeric(12, 2), default=Decimal("0.00"))
    gst_amount = Column(Numeric(12, 2), default=Decimal("0.00"))
    total = Column(Numeric(12, 2), default=Decimal("0.00"))

    creditor_id = Column(Integer, ForeignKey("creditors.id"), nullable=True)
    invoice_type_id = Column(Integer, ForeignKey("invoice_types.id"), nullable=True)

    creditor = relationship("Creditor")
    invoice_type = relationship("InvoiceType")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")
    journals = relationship("Journal", back_populates="invoice", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="invoice", order_by="AuditLog.timestamp.desc()", cascade="all, delete-orphan")

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    line_number = Column(Integer, default=0)
    description = Column(String(256), default="")
    amount = Column(Numeric(12, 2), default=Decimal("0.00"))
    gst_amount = Column(Numeric(12, 2), default=Decimal("0.00"))
    gst_treatment = Column(String(32), default="taxable")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)

    invoice = relationship("Invoice", back_populates="lines")
    account = relationship("Account")


class Journal(Base):
    __tablename__ = "journals"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    date = Column(Date, nullable=False)
    narration = Column(String(256), nullable=False)
    status = Column(String(32), default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)

    invoice = relationship("Invoice", back_populates="journals")
    lines = relationship("JournalLine", back_populates="journal", cascade="all, delete-orphan")


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id = Column(Integer, primary_key=True)
    journal_id = Column(Integer, ForeignKey("journals.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    description = Column(String(256), default="")
    debit = Column(Numeric(12, 2), default=Decimal("0.00"))
    credit = Column(Numeric(12, 2), default=Decimal("0.00"))
    gst_treatment = Column(String(32), default="taxable")
    bas_label = Column(String(16), nullable=True)

    journal = relationship("Journal", back_populates="lines")
    account = relationship("Account")


def account_by_code(session, code: str) -> Account | None:
    return session.query(Account).filter(Account.code == code, Account.archived == False).first()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(ENGINE)
