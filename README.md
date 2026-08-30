# AU Invoice Journals

Local desktop app: drop Australian tax invoice PDFs, extract them with OCR + a local Ollama model, map creditors / invoice types to ledgers, and post GST-aware purchase journals.

Nothing is sent to a cloud API.

## Start

Double-click `start.command`, or in Terminal:

```bash
cd ~/au-invoice-journals
chmod +x start.sh start.command
./start.sh
```

First launch creates a virtualenv and installs Python packages. A window opens; if that fails, use `./start.sh --server` and open [http://127.0.0.1:8743](http://127.0.0.1:8743).

### Ollama (required for capture)

1. [Ollama](https://ollama.com) should already be installed.
2. Pull a model once: `ollama pull llama3.2`
3. For scans with little text: `ollama pull llama3.2-vision`

The app starts `ollama serve` if nothing is listening on port 11434. Tesseract (`brew install tesseract`) is used when a PDF has no text layer.

Drop extra PDFs into `data/inbox/` and use **Scan inbox folder** on the home screen.

## What it does

- **Ingest** PDFs, extract text (PyMuPDF) / OCR (Tesseract), then structured capture via Ollama JSON.
- **Australian GST**: 10% rate, **1/11** inclusive split, ABN checksum, tax-invoice hints, treatments (taxable, GST-free, input-taxed, out of scope, capital).
- **Journals**: debit expense (ex GST) + GST receivable (BAS **1B** / **G11** or **G10** for capital) + credit accounts payable.
- **Mapping studio**: creditors, invoice types (keywords), and ordered rules. Line overrides win, then rules, creditor, type, then Unallocated purchases.

Export a journal as CSV for your GL.

This is a bookkeeping aid, not a lodged BAS or tax advice.
