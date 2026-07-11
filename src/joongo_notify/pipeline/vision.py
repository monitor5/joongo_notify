"""③단계: VL 사진 검증 (FR-C3).

시각검증가능(visual=true) 속성 중 Watch 조건에 걸려 있는 것을 대상으로,
매물 사진을 로컬 VL 모델(Ollama)에 보내 판정한다.

정확도 스탠스 (계획서 §6): VL 판정은 확정이 아니라 신뢰도 딸린 소견이다.
- 본문에 언급이 없던 속성 → 사진 판정으로 채움 (source="image")
- 본문 주장과 사진 판정이 다름 → 본문 값을 유지하되 conflict 플래그 + 신뢰도 하향
- 리포트당 1회만 실행 (extractor에 "+vl" 마커) — 비용 통제
"""
from __future__ import annotations

import base64
import json
import logging

import httpx

from ..config import VLConfig
from ..models import (
    UNMENTIONED,
    AnalysisReport,
    AttributeFinding,
    Category,
    ConditionAttribute,
    Listing,
)

logger = logging.getLogger("joongo_notify")

VL_MARKER = "+vl"


def vl_already_ran(report: AnalysisReport) -> bool:
    return VL_MARKER in report.extractor


def visual_attributes_to_check(
    category: Category, report: AnalysisReport, condition_attr_ids: list[str]
) -> list[ConditionAttribute]:
    """Watch 조건에 포함된 시각검증가능 속성만 (FR-C3: 실행 범위 제한)."""
    result = []
    for attr_id in condition_attr_ids:
        attr = category.attribute(attr_id)
        if attr is not None and attr.visual:
            result.append(attr)
    return result


def build_vl_prompt(attrs: list[ConditionAttribute]) -> str:
    attr_lines = []
    for attr in attrs:
        enum = "/".join(av.value for av in attr.values) or "자유값"
        attr_lines.append(f'- "{attr.id}" ({attr.name}): {enum}/판정불가 중 하나')
    joined = "\n".join(attr_lines)
    return f"""당신은 중고 매물 사진에서 제품 상태를 감정하는 검수자다.
첨부된 사진들을 보고 각 속성을 판정해 JSON으로만 답하라.

속성 정의:
{joined}

규칙:
1. 사진만으로 판단할 수 없으면 반드시 "판정불가"로 표기한다. 추측 금지.
2. evidence에는 판단 근거를 짧게 쓰고, image_index에 근거 사진 번호(1부터)를 쓴다.
3. confidence는 low/med/high — 조명·해상도·각도가 나쁘면 low.
4. 출력 형식 (JSON 외 다른 텍스트 금지):
{{"findings": [{{"attribute_id": "...", "value": "...", "evidence": "...", "image_index": 1, "confidence": "low"}}]}}"""


def merge_findings(
    report: AnalysisReport, image_findings: list[AttributeFinding]
) -> tuple[AnalysisReport, int]:
    """텍스트 리포트에 사진 판정 병합. (병합 리포트, 상충 건수) 반환."""
    conflicts = 0
    by_id = {f.attribute_id: f for f in report.findings}
    for img in image_findings:
        if img.value in (UNMENTIONED, "판정불가", ""):
            continue
        text_finding = by_id.get(img.attribute_id)
        if text_finding is None or text_finding.value == UNMENTIONED:
            by_id[img.attribute_id] = img  # 본문에 없던 정보를 사진이 채움
        elif text_finding.value != img.value:
            # 본문 주장 유지 + 상충 표시 (FR-C3 AC) — 최종 판단은 사람이
            text_finding.conflict = True
            text_finding.confidence = "low"
            text_finding.evidence = (
                f"{text_finding.evidence} / 사진 판정: {img.value} ({img.evidence})"
            )[:300]
            conflicts += 1
        else:
            # 본문과 사진이 일치 → 신뢰도 상향
            text_finding.confidence = "high"
    report.findings = list(by_id.values())
    return report, conflicts


class OllamaVision:
    def __init__(self, config: VLConfig):
        self.config = config
        self.name = f"vl/{config.model}"

    async def _download_images(self, urls: list[str]) -> list[str]:
        images: list[str] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for url in urls[: self.config.max_images]:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    images.append(base64.b64encode(resp.content).decode())
                except httpx.HTTPError as exc:
                    logger.warning("VL 이미지 다운로드 실패 (%s): %s", url, exc)
        return images

    async def _chat(self, prompt: str, images_b64: list[str]) -> str:
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            resp = await client.post(
                f"{self.config.base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "user", "content": prompt, "images": images_b64}
                    ],
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    @staticmethod
    def parse_output(
        attrs: list[ConditionAttribute], content: str
    ) -> list[AttributeFinding] | None:
        """VL 출력 스키마 검증. 위반 시 None."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return None
        raw_findings = data.get("findings")
        if not isinstance(raw_findings, list):
            return None
        allowed_by_id = {
            a.id: {av.value for av in a.values} | {"판정불가"} for a in attrs
        }
        findings = []
        for item in raw_findings:
            if not isinstance(item, dict):
                return None
            attr_id = str(item.get("attribute_id", ""))
            if attr_id not in allowed_by_id:
                continue
            value = str(item.get("value", "판정불가")).strip() or "판정불가"
            if allowed_by_id[attr_id] and value not in allowed_by_id[attr_id]:
                return None
            confidence = item.get("confidence", "low")
            if confidence not in ("low", "med", "high"):
                confidence = "low"
            image_index = item.get("image_index")
            evidence = str(item.get("evidence", ""))[:200]
            if isinstance(image_index, int):
                evidence = f"사진#{image_index}: {evidence}"
            findings.append(
                AttributeFinding(
                    attribute_id=attr_id,
                    value=value,
                    evidence=evidence,
                    confidence=confidence,
                    source="image",
                )
            )
        return findings

    async def verify(
        self, category: Category, listing: Listing, report: AnalysisReport,
        condition_attr_ids: list[str],
    ) -> AnalysisReport | None:
        """사진 검증 후 병합 리포트 반환. 실행 불가/실패면 None (리포트 원본 유지)."""
        attrs = visual_attributes_to_check(category, report, condition_attr_ids)
        if not attrs or not listing.images:
            return None
        images = await self._download_images(listing.images)
        if not images:
            return None
        try:
            content = await self._chat(build_vl_prompt(attrs), images)
        except httpx.HTTPError as exc:
            logger.warning("VL 호출 실패 (listing=%s): %s", listing.id, exc)
            return None
        image_findings = self.parse_output(attrs, content)
        if image_findings is None:
            logger.warning("VL 출력 스키마 위반 (listing=%s)", listing.id)
            return None
        merged, conflicts = merge_findings(report, image_findings)
        merged.extractor = f"{report.extractor}{VL_MARKER}/{self.config.model}"
        if conflicts:
            logger.info("listing %s: 본문-사진 상충 %d건", listing.id, conflicts)
        return merged
