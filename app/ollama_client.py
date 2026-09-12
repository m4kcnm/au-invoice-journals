from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import DEFAULT_OLLAMA_URL, PREFERRED_TEXT_MODELS, PREFERRED_VISION_MODELS


def _client(base_url: str, timeout: float = 25.0) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)


def ping(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        with _client(base_url, timeout=2.0) as c:
            r = c.get("/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def list_models(base_url: str = DEFAULT_OLLAMA_URL) -> list[str]:
    try:
        with _client(base_url, timeout=3.0) as c:
            r = c.get("/api/tags")
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            return [n for n in names if n]
    except Exception:
        return []


def pick_model(available: list[str], prefer_vision: bool = False) -> str | None:
    names = available
    short = [n.split(":")[0] for n in names]
    preferred = PREFERRED_VISION_MODELS if prefer_vision else PREFERRED_TEXT_MODELS
    for p in preferred:
        for i, s in enumerate(short):
            if s == p or s.startswith(p):
                return names[i]
    if prefer_vision:
        for i, s in enumerate(short):
            if "vision" in s or "llava" in s:
                return names[i]
    return names[0] if names else None


def chat_json(
    *,
    base_url: str,
    model: str,
    system: str,
    user: str,
    images_b64: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any] | None:
    """Optimized JSON inference with VRAM residency and tight token bounds."""
    imgs = images_b64 or images or None
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if imgs:
        messages[1]["images"] = imgs

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": -1,  # Keep pinned in Metal GPU RAM
        "messages": messages,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": 1024,
            "top_k": 20,
            "top_p": 0.9,
        },
    }

    try:
        with _client(base_url, timeout=60.0) as c:
            r = c.post("/api/chat", json=payload)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content") or "{}"
            
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                start, end = content.find("{"), content.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(content[start : end + 1])
                return None
    except Exception:
        return None