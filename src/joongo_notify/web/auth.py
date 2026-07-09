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
    """로그인 실패 N회 → 일시 잠금 (FR-A1 AC).

    클라이언트(IP)별로 집계한다 — 전역 카운터면 외부의 실패 시도가
    소유자 본인을 잠그는 로그인 DoS가 되기 때문.
    """

    def __init__(self, max_failures: int, lockout_minutes: int):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_minutes * 60
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str) -> bool:
        return time.monotonic() < self._locked_until.get(key, 0.0)

    def record_failure(self, key: str) -> None:
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.max_failures:
            self._locked_until[key] = time.monotonic() + self.lockout_seconds
            self._failures[key] = 0

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


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
