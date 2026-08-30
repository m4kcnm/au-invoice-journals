# AU Invoice Journals

A local, privacy-first Accounts Payable and General Ledger ingestion engine designed for Australian entities. The application automates document capture, hybrid AI/OCR text extraction, ABN checksum verification, Australian GST calculations, automated account mapping, and balanced double-entry journal posting.

All document processing and model inference remain strictly on-premise, ensuring complete data sovereignty.

---

## 1. Key Capabilities

* Local Document Capture & OCR: Extracts text directly from native PDFs via PyMuPDF (fitz) and automatically falls back to Tesseract OCR for scanned documents.
* Hybrid Entity Extraction: Pairs local Large Language Models (running on an Ollama daemon, e.g., llama3.2:latest) with deterministic regex heuristics to parse complex vendor bills.
* Instant UI & Asynchronous Processing: PDF documents render immediately in a split-screen viewer (<50ms) while extraction runs in a detached worker thread, updating form fields and GL previews via live status polling.
* Australian GST & BAS Compliance: Automatically enforces the 1/11th tax fraction for GST-inclusive pricing, validates supplier ABNs against the ATO Modulus-89 algorithm, and assigns Activity Statement BAS labels (G10, G11, G13, G14, 1B).
* Automated Ledger Routing: Multi-tier mapping hierarchy (Line Override -> Priority Rules -> Creditor Defaults -> Category Keywords -> Unallocated Suspense) to assign chart of accounts codes.
* Double-Entry General Ledger Engine: Enforces the fundamental accounting equation (Debits = Credits). Automatically drafts purchase journals, separates GST receivable into asset account 1-1200, and handles penny-rounding plugs.
* Maker-Checker Segregation of Duties: Role-based access control (RBAC) separating invoice ingestion/review (operator) from General Ledger posting (approver).
* Natural Language Search: Uses local LLM query parsing to translate plain English search requests (e.g., "Show utilities bills over $50 from July") into structured database queries.
* Keyboard Navigation & Power-User Features: Full keyboard shortcuts for reviewing ("]" for next bill, "Cmd/Ctrl + S" to save review, "Cmd/Ctrl + Enter" to approve and post), drag-and-drop global ingestion, and one-click CSV journal export.
* Immutable Audit Trail: Chronological compliance event logging across every invoice lifecycle stage.

---

## 2. System Architecture & Processing Pipeline

[ PDF Upload / Global Dropzone ]
|
+---> Stored locally in /data/invoices/
|
v
[ Instant Split-Screen View (<50ms) ] <--- Browser loads immediately
|
v
[ Asynchronous Worker Thread ] (Detached background execution)
+---> PyMuPDF Text Capture / Tesseract OCR Fallback
+---> Regex Anchor Detection (ABN, Gross Total, GST hints)
+---> Ollama Local Model Inference (JSON Schema, VRAM pinned)
+---> Australian Tax Normalisation & DB Commit (WAL Mode)
|
v
[ Live UI Status Polling (/status) ] ---> Form & GL Preview Auto-Refresh
|
+---> Reactive Journal Calculation & Recalculation
+---> Permanent FY Archive on GL Post (FY{YYYY-YY}/{Vendor}/)

---

## 3. Australian Tax & Accounting Specifications

The system implements statutory rules established by A New Tax System (Goods and Services Tax) Act 1999:

### Tax Calculations

* GST-Inclusive Amount: GST = Gross / 11 (rounded to nearest cent).
* Net Expense: Net = Gross - GST.
* GST-Free & Input-Taxed Supplies: Automatically identifies water rates, council charges, financial fees, and basic supplies where GST = $0.00.
* Non-Registered Entities: If the entity is flagged as not registered for GST in Settings, input tax credits are suppressed, and the gross invoice value is posted directly to the expense account.

### ABN Validation (ATO Modulus 89)

Every extracted Australian Business Number is verified against the statutory weighting algorithm:

1. Subtract 1 from the first digit.
2. Multiply each of the 11 digits by its assigned weight: (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19).
3. Sum the products.
4. Verify that the sum is divisible by 89 (sum % 89 == 0).

---

## 4. Technology Stack

* Language & Runtime: Python 3.10+
* Web Framework: FastAPI, Uvicorn, Jinja2 Templates, Vanilla JavaScript / HTML5 / CSS3
* Database & ORM: SQLite 3 with Write-Ahead Logging (PRAGMA journal_mode=WAL), SQLAlchemy 2.0
* PDF & Vision Processing: PyMuPDF (fitz), Pillow (PIL), Pytesseract
* Local AI Inference: Ollama HTTP API (llama3.2:latest, llama3.2-vision) with persistent VRAM residency (keep_alive: -1)

---

## 5. Project Structure

au-invoice-journals/
|-- app/
|   |-- abn.py              # ATO Modulus 89 ABN formatting and checksum verification
|   |-- auth.py             # Cryptographic session management and PBKDF2 authentication
|   |-- config.py           # Application paths, ports, and model defaults
|   |-- extract.py          # PyMuPDF capture, Tesseract OCR, and hybrid extraction
|   |-- gst.py              # ATO 1/11th GST calculation, tax splits, and BAS labels
|   |-- journals.py         # Proposed General Ledger journal engine and balancing rules
|   |-- mappings.py         # Multi-tiered general ledger routing engine
|   |-- models.py           # SQLAlchemy database schema and SQLite WAL configuration
|   |-- ollama_client.py    # High-performance HTTP client for Ollama inference
|   |-- sample.py           # Deterministic PDF generator for test invoices
|   |-- search.py           # Natural language query parsing and SQL filter execution
|   |-- seed.py             # Standard Australian Chart of Accounts and seed data
|   |-- services.py         # Invoice ingestion, extraction execution, and archive filer
|   |-- web.py              # FastAPI endpoints, route handlers, and async dispatchers
|   |-- static/
|   |   |-- app.js          # Dynamic journal recalculation, status polling, and dropzone
|   |   +-- style.css       # Design system, layout, responsive breakpoints, and animations
|   +-- templates/
|       |-- accounts.html   # Chart of accounts management
|       |-- base.html       # Base navigation layout and global dropzone overlay
|       |-- dashboard.html  # Inbox queue and recent journal activity
|       |-- invoice_detail.html # Split-screen PDF viewer, reactive form, and GL preview
|       |-- invoices.html   # Creditor invoice listing
|       |-- journal_detail.html # Journal inspection and CSV export
|       |-- journals.html   # Posted General Ledger journals
|       |-- login.html      # Authentication portal
|       |-- mapping.html    # Mapping Studio for rules, types, and creditors
|       |-- search.html     # Smart search interface
|       +-- settings.html   # Entity configuration and Ollama model selection
|-- data/                   # Local database, inbox, invoices, and archives (git-ignored)
|-- run.py                  # Server runner and automatic browser launcher
|-- start.sh                # Shell bootstrap script
+-- requirements.txt        # Python dependency manifest

---

## 6. Installation & Prerequisites

### Prerequisites

* macOS or Linux
* Python 3.10 or higher
* Tesseract OCR (brew install tesseract on macOS)
* Ollama with a local language model:
ollama pull llama3.2

### Setup Instructions

1. Clone the repository:
git clone [https://github.com/m4kcnm/au-invoice-journals.git](https://github.com/m4kcnm/au-invoice-journals.git)
cd au-invoice-journals
2. Create and activate a Python virtual environment:
python3 -m venv .venv
source .venv/bin/activate
3. Install dependencies:
pip install -r requirements.txt
4. Make the start script executable and launch:
chmod +x start.sh
./start.sh

The application initializes the database schema, seeds default accounts, opens [http://127.0.0.1:8743](http://127.0.0.1:8743) in your default browser, and displays the login screen.

---

## 7. Default Authentication Credentials

The system initializes with two pre-configured accounts to demonstrate maker-checker controls:

* clerk / password123 (Role: Operator)
Permissions: Ingest invoices, edit line items, trigger AI re-extraction, save review state.
* manager / password123 (Role: Approver)
Permissions: Full operator access plus General Ledger journal posting, invoice deletion, and settings configuration.

---

## 8. Chart of Accounts Configuration

The initial database seed provisions a standard small-business Australian chart of accounts:

* 1-1100: Main Operating Bank Account (Asset, BAS Excluded)
* 1-1200: GST Receivable / 1B Input Credits (Asset, Taxable 10% GST)
* 1-1600: Computer & Electronic Equipment (Asset, Capital BAS G10)
* 2-1100: Accounts Payable / Trade Creditors (Liability, BAS Excluded)
* 6-1100: Electricity & Gas (Expense, Taxable 10% GST)
* 6-1200: Computer, Software & IT (Expense, Taxable 10% GST)
* 6-1300: Telecommunications & Internet (Expense, Taxable 10% GST)
* 6-1400: Rent & Premises Costs (Expense, Taxable 10% GST)
* 6-1500: Advertising & Marketing (Expense, Taxable 10% GST)
* 6-1600: Accounting & Legal Fees (Expense, Taxable 10% GST)
* 6-1700: Bank & Merchant Fees (Expense, Input-Taxed No GST)
* 6-1800: Council Rates & Water Charges (Expense, GST-Free)
* 6-9000: General & Unallocated Expenses (Expense, Taxable 10% GST)
* 6-9900: Rounding & Penny Suspense (Expense, Out of Scope)

---

## 9. Security & Data Privacy

* Zero Cloud Egress: Document payloads and raw text strings are processed exclusively on the host system via local Ollama sockets.
* Credential Isolation: Session cookies use cryptographic HMAC-SHA256 signatures with HttpOnly and SameSite=Lax headers.
* Safe Archiving: Posted invoices are automatically filed in a deterministic, structured archive tree: data/archive/FY{YYYY-YY}/{Vendor}/.