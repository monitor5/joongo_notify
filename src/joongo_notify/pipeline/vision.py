"""③단계: VL 사진 검증 (FR-C3).

시각검증가능(visual=true) 속성 중 Watch 조건에 걸려 있는 것을 대상으로,
매물 사진을 로컬 VL 모델(Ollama)에 보내 판정한다.

정확도 스탠스 (계획서 §6): VL 판정은 확정이 아니라 신뢰도 딸린 소견이다.
- 본문에 언급이 없던 속성 → 사진 판정으로 채움 (source="image")
- 본문 주장과 사진 판정이 다름 → 본문 값을 유지하되 conflict 플래그 + 신뢰도 하향
- 검증 시도는 **속성 단위로** finding.vl_checked에 기록한다 — Watch마다 조건이
  달라도 각자 필요한 속성이 검증되고, 이미 시도한 속성은 재검증하지 않는다.
  (리포트 extractor 문자열은 건드리지 않으므로 DB에서 같은 행이 갱신된다)
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
from .ollama import chat_json, shared_client

logger = logging.getLogger("joongo_notify")

UNDECIDABLE = "판정불가"


def attrs_needing_vl(
    category: Category, report: AnalysisReport, condition_attr_ids: list[str]
) -> list[ConditionAttribute]:
    """Watch 조건 중 시각검증가능하고 아직 VL을 시도하지 않은 속성 (FR-C3 실행 범위)."""
    result = []
    for attr_id in condition_attr_ids:
        attr = category.attribute(attr_id)
        if attr is None or not attr.visual:
            continue
        finding = report.finding(attr_id)
        if finding is not None and finding.vl_checked:
            continue
        result.append(attr)
    return result


def build_vl_prompt(attrs: list[ConditionAttribute]) -> str:
    attr_lines = []
    for attr in attrs:
        enum = "/".join(av.value for av in attr.values) or "자유값"
        attr_lines.append(f'- "{attr.id}" ({attr.name}): {enum}/{UNDECIDABLE} 중 하나')
    joined = "\n".join(attr_lines)
    return f"""당신은 중고 매물 사진에서 제품 상태를 감정하는 검수자다.
첨부된 사진들을 보고 각 속성을 판정해 JSON으로만 답하라.

속성 정의:
{joined}

규칙:
1. 사진만으로 판단할 수 없으면 반드시 "{UNDECIDABLE}"로 표기한다. 추측 금지.
2. evidence에는 판단 근거를 짧게 쓰고, image_index에 근거 사진 번호(1부터)를 쓴다.
3. confidence는 low/med/high — 조명·해상도·각도가 나쁘면 low.
4. 출력 형식 (JSON 외 다른 텍스트 금지):
{{"findings": [{{"attribute_id": "...", "value": "...", "evidence": "...", "image_index": 1, "confidence": "low"}}]}}"""


def merge_findings(
    report: AnalysisReport,
    image_findings: list[AttributeFinding],
    requested_attr_ids: list[str],
) -> tuple[AnalysisReport, int]:
    """텍스트 리포트에 사진 판정 병합. (병합 리포트, 상충 건수) 반환.

    요청했던 모든 속성은 결과와 무관하게 vl_checked=True로 기록되어
    다음 사이클/다른 Watch에서 같은 속성을 재검증하지 않는다.
    """
    conflicts = 0
    by_id = {f.attribute_id: f for f in report.findings}
    image_by_id = {f.attribute_id: f for f in image_findings}

    for attr_id in requested_attr_ids:
        img = image_by_id.get(attr_id)
        decided = img is not None and img.value not in (UNMENTIONED, UNDECIDABLE, "")
        text_finding = by_id.get(attr_id)

        if decided:
            img.vl_checked = True
            if text_finding is None or text_finding.value == UNMENTIONED:
                by_id[attr_id] = img  # 본문에 없던 정보를 사진이 채움
                continue
            if text_finding.value != img.value:
                # 본문 주장 유지 + 상충 표시 (FR-C3 AC) — 최종 판단은 사람이
                text_finding.conflict = True
                text_finding.confidence = "low"
                text_finding.evidence = (
                    f"{text_finding.evidence} / 사진 판정: {img.value} ({img.evidence})"
                )[:300]
                conflicts += 1
            else:
                text_finding.confidence = "high"  # 본문과 사진 일치
            text_finding.vl_checked = True
        else:
            # 판정불가여도 시도 자체는 기록 (무한 재시도 방지)
            if text_finding is None:
                text_finding = AttributeFinding(attr_id, UNMENTIONED, confidence="low")
                by_id[attr_id] = text_finding
            text_finding.vl_checked = True

    report.findings = list(by_id.values())
    return report, conflicts


class OllamaVision:
    def __init__(self, config: VLConfig):
        self.config = config
        self.name = f"vl/{config.model}"

    async def _download_images(self, urls: list[str]) -> list[str]:
        images: list[str] = []
        client = shared_client()
        for url in urls[: self.config.max_images]:
            try:
                resp = await client.get(url, timeout=30.0)
                resp.raise_for_status()
                images.append(base64.b64encode(resp.content).decode())
            except httpx.HTTPError as exc:
                logger.warning("VL 이미지 다운로드 실패 (%s): %s", url, exc)
        return images

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
        # 열거값이 정의된 속성만 값 제약. 자유값(수치 등) 속성은 어떤 답도 허용.
        allowed_by_id = {
            a.id: ({av.value for av in a.values} | {UNDECIDABLE}) if a.values else None
            for a in attrs
        }
        findings = []
        for item in raw_findings:
            if not isinstance(item, dict):
                return None
            attr_id = str(item.get("attribute_id", ""))
            if attr_id not in allowed_by_id:
                continue
            value = str(item.get("value", UNDECIDABLE)).strip() or UNDECIDABLE
            allowed = allowed_by_id[attr_id]
            if allowed is not None and value not in allowed:
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
        attrs = attrs_needing_vl(category, report, condition_attr_ids)
        if not attrs or not listing.images:
            return None
        images = await self._download_images(listing.images)
        if not images:
            return None
        try:
            content = await chat_json(
                self.config.base_url, self.config.model,
                build_vl_prompt(attrs), self.config.timeout_seconds, images_b64=images,
            )
        except httpx.HTTPError as exc:
            logger.warning("VL 호출 실패 (listing=%s): %s", listing.id, exc)
            return None
        image_findings = self.parse_output(attrs, content)
        if image_findings is None:
            logger.warning("VL 출력 스키마 위반 (listing=%s)", listing.id)
            return None
        requested = [a.id for a in attrs]
        merged, conflicts = merge_findings(report, image_findings, requested)
        # FR-C3 AC: 실행 건수를 로그로 확인 가능해야 함 — 성공 실행도 기록
        logger.info("VL 실행 (listing=%s, 속성=%s, 상충=%d)", listing.id, requested, conflicts)
        return merged
