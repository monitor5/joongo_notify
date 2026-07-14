"""알림 인터페이스와 메시지 포맷 (FR-D1).

푸시 채널(텔레그램)은 제거됨 — **웹 대시보드가 알림 표면**이다. 조건 부합 매물은
match_results에 저장되어 대시보드 "최근 매칭"·매칭 상세에서 확인하고, 운영자 경고
(FR-D4)는 로그와 adapter_health(/health)로 노출된다. Notifier는 이를 로그로 남긴다.
"""
from __future__ import annotations

import logging
from typing import Protocol

from ..models import Listing, MatchResult, Watch

logger = logging.getLogger("joongo_notify")

OUTCOME_ICONS = {"satisfied": "✅", "violated": "❌", "unmentioned": "❓"}


def format_match_message(
    watch: Watch, listing: Listing, match: MatchResult, web_base: str = ""
) -> str:
    """알림 본문: 점수 + 속성별 판정 요약과 근거 (FR-D1 AC).

    web_base가 설정되면 승인·피드백을 할 수 있는 웹 대시보드 링크를 덧붙인다
    (FR-D6 사전 승인·FR-D2 피드백은 웹 UI에서 수행).
    """
    lines = [
        f"🔔 [{watch.name}] 조건 부합 매물 — {match.score}점",
        f"{listing.title}",
        f"💰 {listing.price:,}원" if listing.price is not None else "💰 가격 미상",
    ]
    if listing.region:
        lines.append(f"📍 {listing.region}")
    lines.append(f"🏪 {listing.platform}")
    lines.append("")
    for v in match.verdicts:
        icon = OUTCOME_ICONS.get(v.outcome, "•")
        req = "[필수]" if v.required else "[선호]"
        line = f"{icon} {req} {v.attribute_name}: {v.value}"
        if getattr(v, "source", "text") == "image":
            line += " 📷사진판정"
        if getattr(v, "conflict", False):
            line += " ⚠️본문-사진 상충"
        if v.evidence:
            line += f' — "{v.evidence[:80]}"'
        lines.append(line)
    lines.append("")
    lines.append(listing.url)
    if web_base:
        base = web_base.rstrip("/")
        if watch.auto_chat_mode == "approve":
            lines.append(f"💬 문의 승인: {base}/chats")
        lines.append(f"🔧 매칭 상세·피드백: {base}/")
    return "\n".join(lines)


class Notifier(Protocol):
    async def send_match(self, watch: Watch, listing: Listing, match: MatchResult) -> None:
        ...

    async def send_operator_alert(self, text: str) -> None:
        ...


class LogNotifier:
    """웹 대시보드가 알림 표면 — 이 Notifier는 매칭·운영자 경고를 로그로 남긴다.

    매물은 이미 match_results에 저장되어 대시보드에 뜨므로, 여기서는 서버 로그에
    사람이 읽을 요약을 남기는 역할이다. sent 리스트는 테스트 검증용.
    """

    def __init__(self, web_base: str = "") -> None:
        self.sent: list[str] = []
        self.web_base = web_base

    async def send_match(self, watch: Watch, listing: Listing, match: MatchResult) -> None:
        message = format_match_message(watch, listing, match, self.web_base)
        self.sent.append(message)
        logger.info("조건 부합 매물 (%d점): %s [%s]", match.score, listing.title, listing.url)

    async def send_operator_alert(self, text: str) -> None:
        self.sent.append(text)
        logger.warning("[운영자 경고] %s", text)
