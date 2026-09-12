import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
INBOX_DIR = DATA_DIR / "inbox"
INVOICES_DIR = DATA_DIR / "invoices"
HOST = "127.0.0.1"
PORT = 8743
APP_TITLE = "AU Invoice Journals"

# Support Docker environment variable or fall back to localhost
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")

PREFERRED_TEXT_MODELS = (
    "qwen3.5:4b",
    "qwen2.5:3b",
    "qwen2.5",
    "llama3.2",
    "llama3.1",
    "mistral",
)
PREFERRED_VISION_MODELS = (
    "qwen2.5-vl",
    "minicpm-v",
    "moondream",
    "llama3.2-vision",
)