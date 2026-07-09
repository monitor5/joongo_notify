"""자체 인증 (FR-A1): 단일 계정 ID/PW + 서명 세션 쿠키 + 로그인 실패 잠금."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

PBKDF2_ITERATIONS = 240_000
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14일


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), iterations
    )
    return f"pbkdf2:{iterations}:{salt}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split(":")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


class LoginGuard:
    """로그인 실패 N회 → 일시 잠금 (FR-A1 AC)."""

    def __init__(self, max_failures: int, lockout_minutes: int):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_minutes * 60
        self._failures = 0
        self._locked_until = 0.0

    def is_locked(self) -> bool:
        return time.monotonic() < self._locked_until

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.max_failures:
            self._locked_until = time.monotonic() + self.lockout_seconds
            self._failures = 0

    def record_success(self) -> None:
        self._failures = 0
        self._locked_until = 0.0


class SessionManager:
    def __init__(self, secret: str):
        self.serializer = URLSafeTimedSerializer(secret, salt="joongo-session")

    def issue(self, username: str) -> str:
        return self.serializer.dumps({"u": username})

    def verify(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            data = self.serializer.loads(token, max_age=SESSION_MAX_AGE)
            return data.get("u")
        except BadSignature:
            return None
