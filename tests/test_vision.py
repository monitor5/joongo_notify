"""FR-C3: VL 사진 검증 — 병합/상충/스키마 검증 (VL 모델 호출 없이)."""
import json

from joongo_notify.models import UNMENTIONED, AnalysisReport, AttributeFinding
from joongo_notify.pipeline.vision import (
    OllamaVision,
    merge_findings,
    visual_attributes_to_check,
    vl_already_ran,
)


def make_report(findings):
    return AnalysisReport(listing_id=1, category_id="smartphone", extractor="heuristic/v1",
                          findings=findings)


def img_finding(attr_id, value, confidence="med"):
    return AttributeFinding(attr_id, value, evidence="사진#1: 근거", confidence=confidence,
                            source="image")


def test_merge_fills_unmentioned():
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    merged, conflicts = merge_findings(report, [img_finding("burn_in", "없음")])
    f = merged.finding("burn_in")
    assert f.value == "없음" and f.source == "image"
    assert conflicts == 0


def test_merge_flags_conflict_keeps_text_value():
    """본문 '번인 없음' vs 사진 '심함' → 본문 값 유지 + 상충 플래그 (FR-C3 AC)."""
    report = make_report([AttributeFinding("burn_in", "없음", evidence="번인 없음")])
    merged, conflicts = merge_findings(report, [img_finding("burn_in", "심함")])
    f = merged.finding("burn_in")
    assert f.value == "없음"
    assert f.conflict is True
    assert f.confidence == "low"
    assert "사진 판정: 심함" in f.evidence
    assert conflicts == 1


def test_merge_agreement_raises_confidence():
    report = make_report([AttributeFinding("burn_in", "없음", confidence="med")])
    merged, _ = merge_findings(report, [img_finding("burn_in", "없음")])
    assert merged.finding("burn_in").confidence == "high"


def test_merge_ignores_undecidable():
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    merged, _ = merge_findings(report, [img_finding("burn_in", "판정불가")])
    assert merged.finding("burn_in").value == UNMENTIONED


def test_visual_attrs_limited_to_conditions(catalog):
    category = catalog.categories["smartphone"]
    report = make_report([])
    attrs = visual_attributes_to_check(category, report, ["burn_in", "battery"])
    ids = [a.id for a in attrs]
    assert ids == ["burn_in"]  # battery는 visual=false


def test_vl_marker():
    report = make_report([])
    assert not vl_already_ran(report)
    report.extractor = "heuristic/v1+vl/qwen2.5vl"
    assert vl_already_ran(report)


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


def test_conflict_verdict_in_scoring(catalog):
    """상충 상태의 필수 위반은 확정 탈락이 아니라 감점 (사람이 최종 판단)."""
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
    assert passed is (score >= 60)
    assert score > 0  # 확정 탈락 아님
    assert verdicts[0].conflict is True
