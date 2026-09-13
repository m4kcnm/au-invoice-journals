from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request
from app.models import SessionLocal, User

SESSION_COOKIE_NAME = "gl_session"
from app.config import DATA_DIR

def _load_or_create_secret_key() -> str:
    env_secret = os.getenv("APP_SECRET_KEY")
    if env_secret:
        return env_secret.strip()
    key_file = DATA_DIR / ".secret_key"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        new_key = secrets.token_hex(32)
        key_file.write_text(new_key, encoding="utf-8")
        return new_key
    except Exception:
        return "fallback-ephemeral-key-" + secrets.token_hex(16)

SECRET_KEY = _load_or_create_secret_key()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        salt, expected_hex = hashed_password.split("$")
        actual_key = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 100_000)
        return hmac.compare_digest(actual_key.hex(), expected_hex)
    except Exception:
        return False


def create_session_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "exp": int(time.time()) + (86400 * 7),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
    sig = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        raw, sig = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("utf-8")).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def get_current_user_optional(request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    session = SessionLocal()
    try:
        user = session.get(User, payload["user_id"])
        if user:
            # Eagerly read attributes to cache in memory before session close
            _ = (user.id, user.username, user.full_name, user.role)
            session.expunge(user)
        return user
    finally:
        session.close()


def get_current_user(request: Request) -> User:
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"Location": "/login"},
        )
    return user


def require_approver(request: Request) -> User:
    user = get_current_user(request)
    if user.role != "approver":
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Approver permissions required.",
        )
    return user