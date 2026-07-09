"""텔레그램 알림 발송기 (FR-D1, FR-A1b).

chat_id는 설정(config/env) 또는 DB settings의 'telegram_chat_id'에서 읽는다
(웹 설정 화면에서 등록 — DB 값이 우선).
"""
from __future__ import annotations

import httpx

from ..config import TelegramConfig
from ..db import Database
from ..models import Listing, MatchResult, Watch
from .base import format_match_message

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, db: Database):
        self.config = config
        self.db = db

    def _chat_id(self) -> str:
        return self.db.get_setting("telegram_chat_id", self.config.chat_id)

    async def _call(self, method: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                API.format(token=self.config.token, method=method), json=payload
            )
            resp.raise_for_status()

    async def send_text(self, text: str) -> None:
        chat_id = self._chat_id()
        if not (self.config.token and chat_id):
            raise RuntimeError("텔레그램 token/chat_id 미설정")
        await self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        )

    async def send_match(self, watch: Watch, listing: Listing, match: MatchResult) -> None:
        message = format_match_message(watch, listing, match)
        chat_id = self._chat_id()
        if listing.images:
            try:
                await self._call(
                    "sendPhoto",
                    {"chat_id": chat_id, "photo": listing.images[0], "caption": message[:1024]},
                )
                return
            except httpx.HTTPError:
                pass  # 사진 실패 시 텍스트로 폴백
        await self.send_text(message)

    async def send_operator_alert(self, text: str) -> None:
        await self.send_text(text)
