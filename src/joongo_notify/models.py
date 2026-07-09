"""도메인 모델 (계획서 §3.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

UNMENTIONED = "언급없음"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AttributeValue:
    """열거형 속성의 값 하나와, 본문에서 이 값을 찾기 위한 휴리스틱 패턴."""

    value: str
    patterns: list[str] = field(default_factory=list)


@dataclass
class ConditionAttribute:
    """카테고리별 상태 속성 정의 (FR-B2)."""

    id: str
    name: str
    type: str  # enum | number | bool | list
    values: list[AttributeValue] = field(default_factory=list)
    visual: bool = False
    topics: list[str] = field(default_factory=list)  # 본문에서 이 속성을 다루는 문장을 찾는 키워드


@dataclass
class Category:
    id: str
    name: str
    parent: str | None
    attributes: list[ConditionAttribute] = field(default_factory=list)

    def attribute(self, attr_id: str) -> ConditionAttribute | None:
        return next((a for a in self.attributes if a.id == attr_id), None)


@dataclass
class Product:
    """제품 카탈로그 항목 + 별칭 사전 (FR-B3)."""

    id: str
    name: str
    category_id: str
    aliases: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)  # "케이스", "필름" 등 액세서리 오탐 방지


@dataclass
class WatchCondition:
    """Watch의 조건 1개 (FR-A4: required=필수/선호)."""

    attribute_id: str
    accepted_values: list[str]  # 이 값들 중 하나면 충족
    required: bool = False


@dataclass
class Watch:
    id: int | None
    product_id: str
    name: str
    price_min: int | None
    price_max: int | None
    region: str | None
    interval_minutes: int
    threshold: int
    conditions: list[WatchCondition]
    status: str = "active"  # active | paused
    last_run_at: str | None = None
    created_at: str = field(default_factory=utcnow_iso)


@dataclass
class Listing:
    """정규화된 매물 (모든 플랫폼 공통)."""

    id: int | None
    platform: str
    platform_id: str
    url: str
    title: str
    description: str
    price: int | None
    region: str | None
    images: list[str]
    posted_at: str | None
    collected_at: str = field(default_factory=utcnow_iso)
    raw: dict = field(default_factory=dict)


@dataclass
class AttributeFinding:
    """분석 결과의 속성 1건: 값 + 근거 + 신뢰도 + 출처 (NFR-7 대비 extractor는 리포트에)."""

    attribute_id: str
    value: str
    evidence: str = ""
    confidence: str = "med"  # low | med | high
    source: str = "text"  # text | image


@dataclass
class AnalysisReport:
    listing_id: int
    category_id: str
    extractor: str  # 예: "heuristic/v1", "ollama/<model>"
    findings: list[AttributeFinding]
    created_at: str = field(default_factory=utcnow_iso)

    def finding(self, attribute_id: str) -> AttributeFinding | None:
        return next((f for f in self.findings if f.attribute_id == attribute_id), None)


@dataclass
class ConditionVerdict:
    attribute_id: str
    attribute_name: str
    required: bool
    outcome: str  # satisfied | violated | unmentioned
    value: str
    evidence: str
    delta: int  # 점수 반영량


@dataclass
class MatchResult:
    watch_id: int
    listing_id: int
    score: int
    passed: bool  # threshold 초과 & 필수 조건 위반 없음
    verdicts: list[ConditionVerdict]
    created_at: str = field(default_factory=utcnow_iso)
    notified_at: str | None = None
