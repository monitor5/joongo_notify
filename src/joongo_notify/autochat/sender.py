"""자동 채팅 RPA 발송기 (FR-D6).

본인 계정 로그인 세션(Playwright persistent context)으로 판매자에게 문의 메시지를
보낸다. 플랫폼마다 채팅 진입·입력·전송 UI가 다르므로 셀렉터를 data/chat_selectors.yaml로
분리하고, 이 모듈은 "매물 페이지 열기 → 채팅 버튼 → 입력창 → 전송"의 공통 흐름만 담는다.

안전장치:
- dry_run(기본 True): 실제 전송 버튼을 누르지 않고 입력창 채우기까지만 (셀렉터 검증용).
- 발송 상한(시간당/일): DB의 실제 sent 건수로 강제 (FR-D6 AC).
- 로그인 세션은 chat-login CLI로 사전 저장 (이 발송기는 세션을 재사용만).

실발송은 본인 계정·로그인이 필요해 자동화 환경에서 끝까지 검증 불가 → dry_run과
셀렉터 외부화로 인수 후 검증을 안전하게 하도록 설계.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..config import AutoChatConfig
from ..db import Database
from ..models import ChatMessage

logger = logging.getLogger("joongo_notify")

SELECTORS_FILE = Path(__file__).resolve().parents[3] / "data" / "chat_selectors.yaml"


class RateLimitExceeded(Exception):
    """발송 상한 초과 — 문의를 큐에 남겨 다음 기회에 재시도."""


def load_selectors(path: Path | str = SELECTORS_FILE) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def within_send_limits(db: Database, config: AutoChatConfig) -> bool:
    now = datetime.now(timezone.utc)
    hour_ago = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    if db.chats_sent_since(hour_ago) >= config.hourly_limit:
        return False
    if db.chats_sent_since(day_ago) >= config.daily_limit:
        return False
    return True


class ChatSender:
    def __init__(self, config: AutoChatConfig, selectors: dict | None = None):
        self.config = config
        self.selectors = selectors if selectors is not None else load_selectors()

    def _profile_dir(self, platform: str) -> Path:
        return Path(self.config.profile_dir) / platform

    async def send(self, chat: ChatMessage) -> None:
        """문의 1건 발송. 실패 시 예외 — 호출자가 상태 기록.

        Playwright는 실제 발송 경로에서만 임포트 (테스트/미설치 환경 보호).
        """
        platform_sel = self.selectors.get(chat.platform)
        if not platform_sel:
            raise RuntimeError(f"{chat.platform} 채팅 셀렉터 미정의 (data/chat_selectors.yaml)")

        from playwright.async_api import async_playwright

        profile = self._profile_dir(chat.platform)
        profile.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(profile), headless=self.config.headless
            )
            try:
                page = await context.new_page()
                await page.goto(chat.listing_url, timeout=self.config.send_timeout_seconds * 1000)
                await self._drive_chat(page, platform_sel, chat.message)
            finally:
                await context.close()

    async def _drive_chat(self, page, sel: dict, message: str) -> None:
        timeout = self.config.send_timeout_seconds * 1000
        # 로그인 여부 확인 (셀렉터가 정의된 경우) — 미로그인이면 즉시 실패
        login_marker = sel.get("logged_in_marker")
        if login_marker and await page.query_selector(login_marker) is None:
            raise RuntimeError("로그인 세션 없음 — `joongo-notify chat-login`으로 먼저 로그인하세요")

        await page.click(sel["chat_button"], timeout=timeout)
        await page.fill(sel["message_input"], message, timeout=timeout)
        if self.config.dry_run:
            logger.info("[dry-run] 전송 생략 — 입력창까지만 채움: %s", message[:40])
            return
        await page.click(sel["send_button"], timeout=timeout)
        # 전송 확인 마커 (있으면 대기)
        if sel.get("sent_marker"):
            await page.wait_for_selector(sel["sent_marker"], timeout=timeout)


async def process_chat_queue(db: Database, config: AutoChatConfig) -> int:
    """queued 상태 문의를 상한 내에서 발송. 발송 성공 건수 반환."""
    if not config.enabled:
        return 0
    sender = ChatSender(config)
    sent = 0
    for chat in db.chats_by_status("queued"):
        if not within_send_limits(db, config):
            logger.info("자동 채팅 발송 상한 도달 — 나머지는 다음 기회에")
            break
        try:
            await sender.send(chat)
            # dry_run이면 실제로 보내지 않았으므로 상한 소진 방지 위해 별도 상태
            if config.dry_run:
                db.set_chat_status(chat.id, "queued", error="dry-run: 미발송")
                logger.info("[dry-run] chat %s 발송 시뮬레이션 완료", chat.id)
            else:
                db.set_chat_status(chat.id, "sent", sent=True)
                sent += 1
        except Exception as exc:
            db.set_chat_status(chat.id, "failed", error=str(exc))
            logger.warning("chat %s 발송 실패: %s", chat.id, exc)
    return sent
