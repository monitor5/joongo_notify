"""FR-C2: 휴리스틱 본문 추출 + LLM 출력 스키마 검증."""
import json

from joongo_notify.models import UNMENTIONED
from joongo_notify.pipeline.extract import HeuristicExtractor, OllamaExtractor


def _find(findings, attr_id):
    return next(f for f in findings if f.attribute_id == attr_id)


def test_burn_in_absent(catalog):
    category = catalog.categories["smartphone"]
    text = "아이폰 14 프로 팝니다. 번인 없고 상태 좋아요. 생활기스 조금 있습니다."
    findings = HeuristicExtractor().extract(category, text)
    assert _find(findings, "burn_in").value == "없음"
    assert _find(findings, "scratch").value == "생활기스"
    assert "번인" in _find(findings, "burn_in").evidence


def test_unmentioned_when_no_topic(catalog):
    """환각 금지 AC: 본문에 없는 속성은 반드시 언급없음."""
    category = catalog.categories["smartphone"]
    findings = HeuristicExtractor().extract(category, "아이폰 14 프로 급처합니다. 직거래만.")
    for f in findings:
        assert f.value == UNMENTIONED


def test_battery_percentage(catalog):
    category = catalog.categories["smartphone"]
    findings = HeuristicExtractor().extract(category, "배터리 효율 87%입니다")
    assert _find(findings, "battery").value == "87"


def test_burn_in_severe(catalog):
    category = catalog.categories["smartphone"]
    findings = HeuristicExtractor().extract(category, "번인 심한 편입니다 감안해주세요")
    assert _find(findings, "burn_in").value == "심함"


def test_llm_output_validation_accepts_valid(catalog):
    category = catalog.categories["smartphone"]
    content = json.dumps(
        {
            "findings": [
                {"attribute_id": "burn_in", "value": "없음", "evidence": "번인 없음", "confidence": "high"}
            ]
        },
        ensure_ascii=False,
    )
    findings = OllamaExtractor._validate(category, content)
    assert findings is not None
    assert _find(findings, "burn_in").value == "없음"
    # 누락 속성은 언급없음으로 채워짐 (전 속성 커버)
    assert _find(findings, "scratch").value == UNMENTIONED


def test_llm_output_validation_rejects_bad_enum(catalog):
    """스키마 위반(허용값 밖) → None (재시도 유도, FR-C2 AC)."""
    category = catalog.categories["smartphone"]
    content = json.dumps(
        {"findings": [{"attribute_id": "burn_in", "value": "아마도없음"}]}, ensure_ascii=False
    )
    assert OllamaExtractor._validate(category, content) is None
