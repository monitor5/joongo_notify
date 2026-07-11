"""FR-C3: VL 사진 검증 — 병합/상충/스키마 검증 (VL 모델 호출 없이)."""
import json

from joongo_notify.models import UNMENTIONED, AnalysisReport, AttributeFinding
from joongo_notify.pipeline.vision import (
    OllamaVision,
    attrs_needing_vl,
    merge_findings,
)


def make_report(findings):
    return AnalysisReport(listing_id=1, category_id="smartphone", extractor="heuristic/v1",
                          findings=findings)


def img_finding(attr_id, value, confidence="med"):
    return AttributeFinding(attr_id, value, evidence="사진#1: 근거", confidence=confidence,
                            source="image")


def test_merge_fills_unmentioned():
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    merged, conflicts = merge_findings(report, [img_finding("burn_in", "없음")], ["burn_in"])
    f = merged.finding("burn_in")
    assert f.value == "없음" and f.source == "image"
    assert f.vl_checked is True
    assert conflicts == 0


def test_merge_flags_conflict_keeps_text_value():
    """본문 '번인 없음' vs 사진 '심함' → 본문 값 유지 + 상충 플래그 (FR-C3 AC)."""
    report = make_report([AttributeFinding("burn_in", "없음", evidence="번인 없음")])
    merged, conflicts = merge_findings(report, [img_finding("burn_in", "심함")], ["burn_in"])
    f = merged.finding("burn_in")
    assert f.value == "없음"
    assert f.conflict is True
    assert f.confidence == "low"
    assert "사진 판정: 심함" in f.evidence
    assert conflicts == 1


def test_merge_agreement_raises_confidence():
    report = make_report([AttributeFinding("burn_in", "없음", confidence="med")])
    merged, _ = merge_findings(report, [img_finding("burn_in", "없음")], ["burn_in"])
    assert merged.finding("burn_in").confidence == "high"


def test_merge_marks_undecidable_as_checked():
    """판정불가여도 시도가 기록되어 무한 재시도하지 않는다."""
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    merged, _ = merge_findings(report, [img_finding("burn_in", "판정불가")], ["burn_in"])
    f = merged.finding("burn_in")
    assert f.value == UNMENTIONED
    assert f.vl_checked is True


def test_merge_does_not_change_extractor():
    """extractor 불변 → DB에서 같은 행이 갱신됨 (리포트 행 중복 축적 방지)."""
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    merged, _ = merge_findings(report, [img_finding("burn_in", "없음")], ["burn_in"])
    assert merged.extractor == "heuristic/v1"


def test_attrs_needing_vl_limited_to_visual_conditions(catalog):
    category = catalog.categories["smartphone"]
    report = make_report([])
    attrs = attrs_needing_vl(category, report, ["burn_in", "battery"])
    assert [a.id for a in attrs] == ["burn_in"]  # battery는 visual=false


def test_attrs_needing_vl_per_attribute_across_watches(catalog):
    """Watch A가 burn_in만 검증한 뒤에도, Watch B의 scratch는 검증 대상 (교차 Watch 커버리지)."""
    category = catalog.categories["smartphone"]
    report = make_report([
        AttributeFinding("burn_in", UNMENTIONED),
        AttributeFinding("scratch", UNMENTIONED),
    ])
    # Watch A: burn_in만 VL 실행
    merged, _ = merge_findings(report, [img_finding("burn_in", "없음")], ["burn_in"])
    # Watch B: scratch 조건 — 여전히 검증 필요로 판정되어야 함
    assert [a.id for a in attrs_needing_vl(category, merged, ["scratch"])] == ["scratch"]
    # Watch A 조건 재확인: burn_in은 이미 시도됨 → 재검증 없음
    assert attrs_needing_vl(category, merged, ["burn_in"]) == []


def test_parse_output_schema(catalog):
    attrs = [catalog.categories["smartphone"].attribute("burn_in")]
    good = json.dumps({"findings": [
        {"attribute_id": "burn_in", "value": "없음", "evidence": "화면 깨끗", "image_index": 2, "confidence": "high"}
    ]}, ensure_ascii=False)
    findings = OllamaVision.parse_output(attrs, good)
    assert findings[0].value == "없음"
    assert findings[0].source == "image"
    assert "사진#2" in findings[0].evidence

    bad_enum = json.dumps({"findings": [{"attribute_id": "burn_in", "value": "아마도"}]}, ensure_ascii=False)
    assert OllamaVision.parse_output(attrs, bad_enum) is None
    assert OllamaVision.parse_output(attrs, "not json") is None


def test_parse_output_free_value_attribute_accepted(catalog):
    """열거값이 없는 자유값(수치 등) 속성은 어떤 답도 스키마 위반이 아니다."""
    from joongo_notify.models import ConditionAttribute

    free_attr = ConditionAttribute(id="battery", name="배터리효율", type="number",
                                   values=[], visual=True)
    content = json.dumps({"findings": [
        {"attribute_id": "battery", "value": "87", "confidence": "med"}
    ]}, ensure_ascii=False)
    findings = OllamaVision.parse_output([free_attr], content)
    assert findings is not None and findings[0].value == "87"


def test_conflict_verdict_in_scoring(catalog):
    """상충 상태의 필수 위반은 확정 탈락이 아니라, 가중 축소된 감점 (사람이 최종 판단)."""
    from joongo_notify.config import ScoringConfig
    from joongo_notify.models import Watch, WatchCondition
    from joongo_notify.pipeline.scoring import score_listing

    cfg = ScoringConfig()
    watch = Watch(id=1, product_id="iphone-14-pro", name="t", price_min=None,
                  price_max=None, region=None, interval_minutes=30, threshold=60,
                  conditions=[WatchCondition("burn_in", ["없음"], required=True)])
    finding = AttributeFinding("burn_in", "심함", conflict=True, confidence="low")
    report = make_report([finding])
    score, passed, verdicts = score_listing(watch, report, catalog.categories["smartphone"], cfg)
    assert score > 0  # 확정 탈락 아님
    # 상충은 신뢰도가 낮으므로 감점에 low_confidence_factor가 적용되어야 함
    expected = cfg.base_score - round(cfg.soft_violated_penalty * cfg.low_confidence_factor)
    assert score == expected
    assert verdicts[0].conflict is True
