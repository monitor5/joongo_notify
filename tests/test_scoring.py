"""FR-C5: 스코어링 규칙별 단위 테스트 (순수 함수)."""
from joongo_notify.config import ScoringConfig
from joongo_notify.models import (
    UNMENTIONED,
    AnalysisReport,
    AttributeFinding,
    Watch,
    WatchCondition,
)
from joongo_notify.pipeline.scoring import score_listing

CFG = ScoringConfig()


def make_watch(conditions, threshold=60):
    return Watch(
        id=1, product_id="iphone-14-pro", name="t", price_min=None, price_max=None,
        region=None, interval_minutes=30, threshold=threshold, conditions=conditions,
    )


def make_report(findings):
    return AnalysisReport(listing_id=1, category_id="smartphone", extractor="test", findings=findings)


def test_required_violation_fails(catalog):
    """필수 위반 → 탈락 (FR-A4 AC)."""
    watch = make_watch([WatchCondition("burn_in", ["없음"], required=True)])
    report = make_report([AttributeFinding("burn_in", "심함", evidence="번인 심함")])
    score, passed, verdicts = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is False and score == 0
    assert verdicts[0].outcome == "violated"


def test_required_unmentioned_penalized_not_failed(catalog):
    """필수인데 언급없음 → 탈락 아님, 감점만 (정보 없음 ≠ 위반)."""
    watch = make_watch([WatchCondition("burn_in", ["없음"], required=True)])
    report = make_report([AttributeFinding("burn_in", UNMENTIONED)])
    score, passed, _ = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is True
    assert score == CFG.base_score - CFG.required_unmentioned_penalty


def test_soft_satisfied_bonus(catalog):
    watch = make_watch([WatchCondition("full_box", ["풀박스"], required=False)])
    report = make_report([AttributeFinding("full_box", "풀박스", evidence="풀박스입니다")])
    score, passed, _ = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is True and score == 100  # 클램프 (base 100 + 5 → 100)


def test_soft_violated_penalty(catalog):
    """선호 위반 → 감점된 채 통과 (FR-A4 AC: 박스 없는 매물은 감점된 채 알림)."""
    watch = make_watch([WatchCondition("full_box", ["풀박스"], required=False)])
    report = make_report([AttributeFinding("full_box", "없음")])
    score, passed, _ = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is True
    assert score == CFG.base_score - CFG.soft_violated_penalty


def test_low_confidence_reduces_weight(catalog):
    watch = make_watch([WatchCondition("scratch", ["없음"], required=False)])
    report = make_report([AttributeFinding("scratch", "파손", confidence="low")])
    score, _, _ = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    expected = CFG.base_score - round(CFG.soft_violated_penalty * CFG.low_confidence_factor)
    assert score == expected


def test_numeric_condition(catalog):
    watch = make_watch([WatchCondition("battery", [">=85"], required=True)])
    good = make_report([AttributeFinding("battery", "90")])
    bad = make_report([AttributeFinding("battery", "78")])
    cat = catalog.categories["smartphone"]
    assert score_listing(watch, good, cat, CFG)[1] is True
    assert score_listing(watch, bad, cat, CFG)[1] is False


def test_threshold_gates_pass(catalog):
    """점수가 임계 미만이면 passed=False (FR-D1)."""
    watch = make_watch(
        [
            WatchCondition("burn_in", ["없음"], required=False),
            WatchCondition("scratch", ["없음"], required=False),
            WatchCondition("full_box", ["풀박스"], required=False),
        ],
        threshold=95,
    )
    report = make_report(
        [
            AttributeFinding("burn_in", "있음"),
            AttributeFinding("scratch", "파손"),
            AttributeFinding("full_box", UNMENTIONED),
        ]
    )
    score, passed, _ = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is False and score < 95


def test_missing_finding_treated_as_unmentioned(catalog):
    watch = make_watch([WatchCondition("water_damage", ["없음"], required=True)])
    report = make_report([])  # 리포트에 해당 속성 자체가 없음
    score, passed, verdicts = score_listing(watch, report, catalog.categories["smartphone"], CFG)
    assert passed is True
    assert verdicts[0].outcome == "unmentioned"
