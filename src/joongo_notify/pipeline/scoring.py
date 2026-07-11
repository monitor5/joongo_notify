"""매칭 스코어링 (FR-C5) — 순수 함수.

규칙 (기능 요구서 FR-C5, 설정으로 수치 조정 — ScoringConfig):
- 필수 조건 위반          → 즉시 탈락 (passed=False, score=0)
- 필수 조건 언급없음      → 탈락 아님, 감점 (정보 없음 ≠ 위반)
- 선호 조건 충족          → 가점
- 선호 조건 위반          → 감점
- 선호 조건 언급없음      → 소감점
- 근거 신뢰도 low         → 해당 조정치에 low_confidence_factor 적용
점수는 base_score에서 시작해 0~100으로 클램프.
"""
from __future__ import annotations

from ..config import ScoringConfig
from ..models import (
    UNMENTIONED,
    AnalysisReport,
    Category,
    ConditionVerdict,
    Watch,
)

SATISFIED = "satisfied"
VIOLATED = "violated"
UNMENTIONED_OUTCOME = "unmentioned"


def _judge(accepted_values: list[str], value: str) -> str:
    if value == UNMENTIONED:
        return UNMENTIONED_OUTCOME
    if _numeric_accepts(accepted_values, value):
        return SATISFIED
    return SATISFIED if value in accepted_values else VIOLATED


def _numeric_accepts(accepted_values: list[str], value: str) -> bool:
    """수치 속성 조건: accepted_values가 [">=87"] 형태면 숫자 비교."""
    try:
        number = float(value)
    except ValueError:
        return False
    for accepted in accepted_values:
        accepted = accepted.strip()
        try:
            if accepted.startswith(">="):
                if number >= float(accepted[2:]):
                    return True
            elif accepted.startswith("<="):
                if number <= float(accepted[2:]):
                    return True
            elif float(accepted) == number:
                return True
        except ValueError:
            continue
    return False


def score_listing(
    watch: Watch,
    report: AnalysisReport,
    category: Category,
    config: ScoringConfig,
) -> tuple[int, bool, list[ConditionVerdict]]:
    score = float(config.base_score)
    hard_violation = False
    verdicts: list[ConditionVerdict] = []

    for condition in watch.conditions:
        attr = category.attribute(condition.attribute_id)
        attr_name = attr.name if attr else condition.attribute_id
        finding = report.finding(condition.attribute_id)
        value = finding.value if finding else UNMENTIONED
        evidence = finding.evidence if finding else ""
        confidence = finding.confidence if finding else "low"
        source = finding.source if finding else "text"
        conflict = finding.conflict if finding else False
        # 신뢰도 low 또는 본문-사진 상충 시 조정치 가중 축소 (FR-C5)
        factor = config.low_confidence_factor if (confidence == "low" or conflict) else 1.0

        outcome = _judge(condition.accepted_values, value)
        delta = 0.0
        if condition.required:
            if outcome == VIOLATED and not conflict:
                hard_violation = True
            elif outcome == VIOLATED and conflict:
                # 상충 상태의 위반은 확정 탈락 대신 감점 (사람이 최종 판단 — 계획서 §6)
                delta = -config.soft_violated_penalty
            elif outcome == UNMENTIONED_OUTCOME:
                delta = -config.required_unmentioned_penalty
        else:
            if outcome == SATISFIED:
                delta = config.soft_satisfied_bonus * factor
            elif outcome == VIOLATED:
                delta = -config.soft_violated_penalty * factor
            else:
                delta = -config.soft_unmentioned_penalty

        score += delta
        verdicts.append(
            ConditionVerdict(
                attribute_id=condition.attribute_id,
                attribute_name=attr_name,
                required=condition.required,
                outcome=outcome,
                value=value,
                evidence=evidence,
                delta=round(delta),
                source=source,
                conflict=conflict,
            )
        )

    if hard_violation:
        return 0, False, verdicts

    final = max(0, min(100, round(score)))
    return final, final >= watch.threshold, verdicts
