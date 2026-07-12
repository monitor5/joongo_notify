"""알림 인터페이스와 메시지 포맷 (FR-D1)."""
from __future__ import annotations

from typing import Protocol

from ..models import Listing, MatchResult, Watch

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
        lines.append(f"🔧 관리·피드백: {base}/")
    return "\n".join(lines)


class Notifier(Protocol):
    async def send_match(self, watch: Watch, listing: Listing, match: MatchResult) -> None:
        ...

    async def send_operator_alert(self, text: str) -> None:
        ...


class ConsoleNotifier:
    """텔레그램 미설정 시 / 스모크 테스트용."""

    def __init__(self, web_base: str = "") -> None:
        self.sent: list[str] = []
        self.web_base = web_base

    async def send_match(self, watch: Watch, listing: Listing, match: MatchResult) -> None:
        message = format_match_message(watch, listing, match, self.web_base)
        self.sent.append(message)
        print("\n" + "=" * 60 + "\n" + message + "\n" + "=" * 60)

    async def send_operator_alert(self, text: str) -> None:
        self.sent.append(text)
        print(f"[운영자 알림] {text}")
