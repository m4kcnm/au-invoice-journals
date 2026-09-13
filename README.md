Australian Accounts Payable & General Ledger Automation

An automated Accounts Payable (AP) and Tax Invoice processing system designed for Australian bookkeeping workflows. The platform combines deterministic heuristics and local AI extraction to validate Australian Business Numbers (ABNs), compute Goods and Services Tax (GST) allocations, match vendor banking details, maintain central audit history, and prepare ABA disbursement files for Australian banks.

---

## Key Features

### 1. Document Extraction & Entity Disambiguation
* **Hybrid Regex & Local AI Pipeline:** Uses PyMuPDF alongside local LLMs (via Ollama, defaulting to `qwen2.5:3b` or vision models) with deterministic fallbacks.
* **Layout-Agnostic Candidate Extraction:** Extracts invoice identifiers across multiple layout structures (inline `Invoice No: X`, deposit slips, stacked table rows, and account fallbacks).
* **Automatic ATO ABN Modulus 89 Validation:** Validates Australian Business Numbers in real-time against the statutory Modulus 89 weighting algorithm.

### 2. Australian GST & Journal Allocation Engine
* **Statutory Australian Tax Treatments:** Automated splitting for standard taxable supplies (1/11th inclusive calculation), GST-free supplies (council rates, water utilities, financial fees), and out-of-scope transactions.
* **General Ledger Entry Generation:** Automatically balances multi-line double-entry accrual journals (`Debit: Expense`, `Debit: GST Receivable (1B)`, `Credit: Accounts Payable`).
* **Interactive Line Splitting:** In-browser line-item editing and allocation against dynamic chart of accounts.

### 3. Creditor & Banking Maintenance (APCA / ABA)
* **Searchable Vendor Directory:** Instant client-side filtering by trading name, ABN, BSB, or account number with status toggling for payment compliance.
* **Disbursement Verification:** Maintains verified BSB and Account numbers for direct electronic funds transfer.
* **ABA Payment File Generation:** Generates compliant Australian Payments Clearing Association (APCA) / Direct Entry text files for batch upload to corporate banking portals (e.g., NAB Connect, CommBiz, ANZ Transactive).

### 4. Governance & Security
* **Role-Based Workflow:**
  * `clerk`: Document upload, text extraction review, and draft maintenance.
  * `manager`: Final ledger approval, journal posting, and vendor profile modification.
* **Central Audit Trail:** Logs all modifications to creditor bank details and invoice lifecycle transitions with timestamps and user identifiers.
* **Air-Gapped & Local-First:** Runs completely on-premise or within private Docker containers; invoice documents and extraction prompts never leave your local infrastructure.

---

## Technical Stack

* **Backend Framework:** FastAPI (Python 3.11) with Uvicorn
* **Database & ORM:** SQLite with SQLAlchemy 2.0 (configured with `PRAGMA journal_mode=DELETE` for robust cross-platform container volume compatibility)
* **Document Processing:** PyMuPDF (`fitz`), Python-dateutil
* **Local Inference: Ollama API (qwen3.5:4b, qwen2.5:3b, qwen2.5-vl)
* **Templating & UI:** Jinja2, vanilla responsive CSS, and lightweight vanilla JavaScript

---

## Directory Structure

```text
.
├── app/
│   ├── abn.py              # ATO Modulus 89 validation algorithm
│   ├── auth.py             # PBKDF2 hashing, session tokens, RBAC dependencies
│   ├── extract.py          # Multi-layout regex & Ollama AI extraction engine
│   ├── gst.py              # Australian GST division & rounding utilities
│   ├── journals.py         # Double-entry general ledger balance generator
│   ├── models.py           # SQLAlchemy database schema and audit log definitions
│   ├── ollama_client.py    # Local inference client with JSON schema enforcement
│   ├── search.py           # Natural language and tokenized ledger query parser
│   ├── seed.py             # Default chart of accounts and baseline test users
│   ├── services.py         # Business operations (file storage, state transitions)
│   ├── web.py              # FastAPI endpoint routing and view controllers
│   └── templates/          # Jinja2 views (creditors, journals, invoices, audit)
├── data/                   # SQLite database file and document file storage (git-ignored)
├── inbox/                  # Monitored directory for automated batch ingestion
├── docker-compose.yml      # Multi-container orchestration definition
├── Dockerfile              # Container build specifications
├── requirements.txt        # Python package dependencies
└── README.md

## Directory Structure

```text
.
├── app/
│   ├── abn.py              # ATO Modulus 89 validation algorithm
│   ├── aba.py              # APCA Direct Entry (ABA) export file generator
│   ├── auth.py             # PBKDF2 hashing, signed session tokens, RBAC dependencies
│   ├── extract.py          # Multi-layout regex & Ollama AI extraction engine
│   ├── gst.py              # Australian GST calculation, splitting & statutory guards
│   ├── journals.py         # Double-entry general ledger balance & posting safeguards
│   ├── mappings.py         # Heuristic GL account rule engine & priority matcher
│   ├── models.py           # SQLAlchemy schema, payment batches & audit definitions
│   ├── ollama_client.py    # Local inference client with structured JSON schema
│   ├── search.py           # Tokenized invoice & ledger query parser
│   ├── seed.py             # Default chart of accounts, routing rules & demo users
│   ├── services.py         # Business operations, file storage & state transitions
│   ├── web.py              # FastAPI routes, lifecycle endpoints & view controllers
│   ├── static/             # Vanilla CSS styles and interactive JavaScript (app.js)
│   └── templates/          # Jinja2 views (payments, creditors, journals, audit)
├── data/                   # SQLite database and document storage (git-ignored)
├── inbox/                  # Monitored directory for automated batch ingestion
├── docker-compose.yml      # Container orchestration definition
├── Dockerfile              # Container build specifications
├── requirements.txt        # Python package dependencies
├── run.py                  # Local Python server entrypoint
├── start.sh                # Local convenience launch script
└── README.md
```
