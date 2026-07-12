"""설정 로딩.

config.yaml + 환경변수(민감정보) 조합. 비밀번호는 해시로만 저장한다 (FR-A1).
환경변수가 같은 키의 yaml 값보다 우선한다.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    username: str = "admin"
    # `joongo-notify hash-password`로 생성한 "pbkdf2:<iterations>:<salt_hex>:<hash_hex>"
    password_hash: str = ""
    session_secret: str = ""
    max_login_failures: int = 5
    lockout_minutes: int = 10


class LLMConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"  # Ollama
    model: str = ""  # 발주자가 벤치마크 후 선정 (Q3)
    timeout_seconds: float = 120.0


class VLConfig(BaseModel):
    """③단계 VL 사진 검증 (FR-C3). 모델은 발주자 벤치마크 후 선정 (Q4)."""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:11434"  # Ollama (LLM과 공유 가능)
    model: str = ""  # 예: "qwen2.5vl:3b"
    max_images: int = 3
    timeout_seconds: float = 180.0


class TelegramConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    chat_id: str = ""


class CollectConfig(BaseModel):
    # NFR-3 / 10번 문서 §8: 저빈도 확정 전제
    platforms: list[str] = ["bunjang", "daangn", "joongna"]
    default_interval_minutes: int = 30
    min_interval_minutes: int = 15
    request_delay_min_seconds: float = 2.0
    request_delay_max_seconds: float = 5.0
    max_requests_per_minute: int = 10
    max_new_details_per_cycle: int = 30
    user_agent: str = "joongo-notify/0.1 (personal use)"
    adapter_failure_threshold: int = 5  # FR-D4


class ScoringConfig(BaseModel):
    base_score: int = 100
    required_unmentioned_penalty: int = 10
    soft_satisfied_bonus: int = 5
    soft_violated_penalty: int = 15
    soft_unmentioned_penalty: int = 5
    low_confidence_factor: float = 0.5
    default_threshold: int = 60


class AutoChatConfig(BaseModel):
    """자동 채팅 (FR-D6). 발주자 결정: 완전 자동(RPA) 허용, 계정 제재 리스크 인지·수용.

    dry_run이 기본값 — 플랫폼별 셀렉터를 본인 로그인 세션으로 검증한 뒤
    직접 false로 바꿔야 실발송된다 (미검증 셀렉터로 오발송 방지).
    """

    enabled: bool = False
    dry_run: bool = True
    hourly_limit: int = 2  # FR-D6 AC: 계정 보호 발송 상한
    daily_limit: int = 5
    headless: bool = True
    profile_dir: str = "chat_profiles"  # 플랫폼별 로그인 세션(브라우저 프로필) 저장 위치
    send_timeout_seconds: float = 60.0


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8320
    # 알림 메시지에 들어갈 웹 UI 외부 주소 (승인 링크용). 미설정 시 host:port 사용
    base_url: str = ""


class Config(BaseModel):
    db_path: str = "joongo_notify.db"
    data_dir: str = "data"
    auth: AuthConfig = Field(default_factory=AuthConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    vl: VLConfig = Field(default_factory=VLConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    collect: CollectConfig = Field(default_factory=CollectConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    autochat: AutoChatConfig = Field(default_factory=AutoChatConfig)
    web: WebConfig = Field(default_factory=WebConfig)


_ENV_OVERRIDES = {
    "JOONGO_DB_PATH": ("db_path",),
    "JOONGO_DATA_DIR": ("data_dir",),
    "JOONGO_AUTH_USERNAME": ("auth", "username"),
    "JOONGO_AUTH_PASSWORD_HASH": ("auth", "password_hash"),
    "JOONGO_SESSION_SECRET": ("auth", "session_secret"),
    "JOONGO_LLM_BASE_URL": ("llm", "base_url"),
    "JOONGO_LLM_MODEL": ("llm", "model"),
    "JOONGO_VL_BASE_URL": ("vl", "base_url"),
    "JOONGO_VL_MODEL": ("vl", "model"),
    "JOONGO_TELEGRAM_TOKEN": ("telegram", "token"),
    "JOONGO_TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
}


def load_config(path: str | os.PathLike | None = None) -> Config:
    raw: dict = {}
    candidate = Path(path) if path else Path(os.environ.get("JOONGO_CONFIG", "config.yaml"))
    if candidate.is_file():
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}

    for env_key, key_path in _ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value is None:
            continue
        node = raw
        for key in key_path[:-1]:
            node = node.setdefault(key, {})
        node[key_path[-1]] = value

    return Config.model_validate(raw)
