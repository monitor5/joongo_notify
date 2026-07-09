"""②단계: 본문 → 상태 속성 추출 (FR-C2).

두 추출기를 제공한다:
- HeuristicExtractor: 온톨로지의 topic/pattern 기반 규칙. LLM 미설정 시 기본,
  그리고 LLM 실패 시 폴백.
- OllamaExtractor: 로컬 LLM(Ollama /api/chat, JSON 모드). 모델명은 설정값
  (발주자가 벤치마크 후 지정 — Q3). 출력은 스키마 검증하며, 실패 시 1회 재시도
  후 휴리스틱으로 폴백한다.

공통 규칙: 본문에 해당 topic이 없으면 값은 반드시 UNMENTIONED (환각 금지 AC).
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import LLMConfig
from ..models import UNMENTIONED, AttributeFinding, Category, ConditionAttribute

_SENTENCE_SPLIT = re.compile(r"[\n.!?。]+")

HEURISTIC_VERSION = "heuristic/v1"


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


class HeuristicExtractor:
    name = HEURISTIC_VERSION

    def extract(self, category: Category, text: str) -> list[AttributeFinding]:
        findings = []
        sentences = split_sentences(text)
        for attr in category.attributes:
            findings.append(self._extract_attribute(attr, sentences))
        return findings

    @staticmethod
    def _extract_attribute(
        attr: ConditionAttribute, sentences: list[str]
    ) -> AttributeFinding:
        relevant = [
            s for s in sentences if any(t.lower() in s.lower() for t in attr.topics)
        ]
        if not relevant:
            return AttributeFinding(attr.id, UNMENTIONED, confidence="high")

        if attr.type == "number":
            # 배터리효율 등: topic 문장에서 백분율/숫자 추출
            for sentence in relevant:
                m = re.search(r"(\d{2,3})\s*%", sentence)
                if m:
                    return AttributeFinding(
                        attr.id, m.group(1), evidence=sentence, confidence="med"
                    )
            return AttributeFinding(
                attr.id, UNMENTIONED, evidence=relevant[0], confidence="low"
            )

        for sentence in relevant:
            for av in attr.values:
                for pattern in av.patterns:
                    if pattern.lower() in sentence.lower():
                        return AttributeFinding(
                            attr.id, av.value, evidence=sentence, confidence="med"
                        )
        # topic은 있으나 값 판정 불가 → 언급없음보다는 불확실로 남긴다
        return AttributeFinding(
            attr.id, UNMENTIONED, evidence=relevant[0], confidence="low"
        )


def build_llm_prompt(category: Category, text: str) -> str:
    attr_lines = []
    for attr in category.attributes:
        if attr.type == "number":
            spec = "숫자(예: 87) 또는 언급없음"
        else:
            enum = "/".join(av.value for av in attr.values)
            spec = f"{enum}/언급없음 중 하나"
        attr_lines.append(f'- "{attr.id}" ({attr.name}): {spec}')
    attrs = "\n".join(attr_lines)
    return f"""당신은 중고 매물 본문에서 제품 상태를 추출하는 분석기다.
아래 매물 본문을 읽고, 각 속성에 대해 JSON으로만 답하라.

속성 정의:
{attrs}

규칙:
1. 본문에 근거가 없는 속성은 반드시 "언급없음"으로 표기한다. 추측 금지.
2. 각 속성에 근거가 된 본문 문장을 evidence로 그대로 인용한다 (언급없음이면 빈 문자열).
3. 출력 형식 (JSON 외 다른 텍스트 금지):
{{"findings": [{{"attribute_id": "...", "value": "...", "evidence": "...", "confidence": "low|med|high"}}]}}

매물 본문:
---
{text}
---"""


class OllamaExtractor:
    def __init__(self, config: LLMConfig, fallback: HeuristicExtractor | None = None):
        self.config = config
        self.fallback = fallback or HeuristicExtractor()
        self.name = f"ollama/{config.model}"

    async def extract_async(
        self, category: Category, text: str
    ) -> tuple[list[AttributeFinding], str]:
        """(findings, extractor_name) 반환. LLM 실패 시 휴리스틱 폴백."""
        for _ in range(2):  # 1회 재시도 (FR-C2 AC)
            try:
                content = await self._chat(build_llm_prompt(category, text))
                findings = self._validate(category, content)
                if findings is not None:
                    return findings, self.name
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError):
                continue
        # FR-C2 AC: 재시도 후에도 실패하면 리포트에 분석실패를 마킹하고 휴리스틱으로 폴백.
        # extractor 필드의 "llm-failed" 마커로 LLM 실패율을 사후 집계할 수 있다.
        return self.fallback.extract(category, text), f"llm-failed→{self.fallback.name}"

    async def _chat(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            resp = await client.post(
                f"{self.config.base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    @staticmethod
    def _validate(category: Category, content: str) -> list[AttributeFinding] | None:
        """LLM 출력 스키마 검증. 위반 시 None (재시도 유도)."""
        data = json.loads(content)
        raw_findings = data.get("findings")
        if not isinstance(raw_findings, list):
            return None
        by_id = {}
        for item in raw_findings:
            if not isinstance(item, dict):
                return None
            attr_id = item.get("attribute_id")
            attr = category.attribute(str(attr_id))
            if attr is None:
                continue  # 모르는 속성은 무시
            value = str(item.get("value", UNMENTIONED)).strip() or UNMENTIONED
            if attr.type == "enum":
                allowed = {av.value for av in attr.values} | {UNMENTIONED}
                if value not in allowed:
                    return None
            confidence = item.get("confidence", "med")
            if confidence not in ("low", "med", "high"):
                confidence = "med"
            by_id[attr.id] = AttributeFinding(
                attribute_id=attr.id,
                value=value,
                evidence=str(item.get("evidence", ""))[:300],
                confidence=confidence,
            )
        # 누락 속성은 언급없음으로 채움 (전 속성 커버 보장)
        return [
            by_id.get(a.id, AttributeFinding(a.id, UNMENTIONED, confidence="low"))
            for a in category.attributes
        ]


async def run_extractor(
    llm_config: LLMConfig, category: Category, text: str
) -> tuple[list[AttributeFinding], str]:
    """설정에 따라 LLM 또는 휴리스틱 실행. (findings, extractor_name) 반환."""
    if llm_config.enabled and llm_config.model:
        return await OllamaExtractor(llm_config).extract_async(category, text)
    heuristic = HeuristicExtractor()
    return heuristic.extract(category, text), heuristic.name
