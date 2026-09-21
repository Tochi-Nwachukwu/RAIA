"""Who may edit the programme.

A signed token, not a session store: HMAC over "user:expiry" with a secret from settings. The demo
credentials (settings.admin_username / admin_password) are in .env so a judge can sign in; change
them before this is anything but a demo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from functools import lru_cache

from fastapi import Header, HTTPException

from src.settings import get_settings


@lru_cache
def _secret() -> bytes:
    """The signing key. Configured, or kept in the cache directory so a restart does not sign
    everyone out mid-demo."""
    settings = get_settings()
    chosen = settings.admin_token_secret or settings.listener_hash_salt
    if not chosen:
        path = settings.cache_dir / "admin_secret"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32))
            os.chmod(path, 0o600)
        chosen = path.read_text().strip()
    return chosen.encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def issue(username: str) -> tuple[str, int]:
    """A token for this user, and the unix time it stops working."""
    expires = int(time.time()) + get_settings().admin_session_hours * 3600
    payload = f"{username}:{expires}"
    token = base64.urlsafe_b64encode(f"{payload}:{_sign(payload)}".encode()).decode()
    return token, expires


def identify(token: str | None) -> str | None:
    """The user a token names, or None if it is missing, tampered with or expired."""
    if not token:
        return None
    try:
        username, expires, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit(":", 2)
    except Exception:
        return None
    if not hmac.compare_digest(signature, _sign(f"{username}:{expires}")) or int(expires) < time.time():
        return None
    return username


def check_password(username: str, password: str) -> bool:
    settings = get_settings()
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(password, settings.admin_password)


def require_admin(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency: every newsroom endpoint needs a valid token."""
    user = identify(authorization.removeprefix("Bearer ").strip() if authorization else None)
    if not user:
        raise HTTPException(401, "Sign in to the newsroom first")
    return user
