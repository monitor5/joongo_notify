"""문의 메시지 템플릿 (FR-D6 AC: 매물 속성 기반 — 미확인 조건 질문 + 가격 제시)."""
from __future__ import annotations

from ..models import MatchResult, Watch

# 속성별 질문 문구 (미확인 조건에 대해서만 질문)
_QUESTIONS = {
    "burn_in": "화면에 번인이나 잔상이 있을까요?",
    "scratch": "외관에 기스나 찍힘이 있는지 궁금합니다.",
    "battery": "배터리 효율(성능 상태)은 몇 %인가요?",
    "water_damage": "침수 이력은 없을까요?",
    "full_box": "박스랑 구성품은 어떻게 되나요?",
}


def build_chat_message(watch: Watch, match: MatchResult, product_name: str) -> str:
    lines = [f"안녕하세요! 올려주신 {product_name} 구매 의사가 있어 연락드립니다."]

    questions = []
    for v in match.verdicts:
        if v.outcome == "unmentioned":
            question = _QUESTIONS.get(v.attribute_id)
            if question:
                questions.append(question)
        elif v.conflict:
            questions.append(f"{v.attribute_name} 상태를 한 번 더 확인 부탁드려도 될까요?")
    if questions:
        lines.append("몇 가지만 여쭤봅니다:")
        lines.extend(f"- {q}" for q in questions)

    if watch.auto_chat_price:
        lines.append(f"상태 괜찮으면 {watch.auto_chat_price:,}원에 바로 구매 가능합니다.")
    lines.append("확인 부탁드립니다. 감사합니다!")
    return "\n".join(lines)
