from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
INBOX_DIR = DATA_DIR / "inbox"
INVOICES_DIR = DATA_DIR / "invoices"
HOST = "127.0.0.1"
PORT = 8743
APP_TITLE = "AU Invoice Journals"

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
PREFERRED_TEXT_MODELS = (
    "llama3.2",
    "llama3.1",
    "llama3",
    "qwen2.5",
    "mistral",
    "gemma2",
    "phi3",
)
PREFERRED_VISION_MODELS = (
    "llama3.2-vision",
    "llava",
    "minicpm-v",
    "moondream",
)
