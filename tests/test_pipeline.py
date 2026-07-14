"""파이프라인 통합: 가짜 어댑터 → 필터 → 분석 → 스코어링 → 알림 (실 네트워크 없음)."""
import pytest

from joongo_notify.adapters.base import RawListing
from joongo_notify.models import Watch, WatchCondition
from joongo_notify.notify.base import LogNotifier
from joongo_notify.pipeline.runner import run_watch_cycle


class FakeAdapter:
    platform = "fake"

    def __init__(self, items, fail=False):
        self.items = items
        self.fail = fail
        self.detail_calls = 0

    async def search(self, query, region=None):
        if self.fail:
            raise RuntimeError("boom")
        return [
            RawListing(
                platform=self.platform, platform_id=i["id"], title=i["title"],
                url=f"https://{self.platform}/{i['id']}", price=i.get("price"),
                region=i.get("region"), description="",
            )
            for i in self.items
        ]

    async def detail(self, raw):
        self.detail_calls += 1
        for i in self.items:
            if i["id"] == raw.platform_id:
                raw.description = i.get("description", "")
        return raw


def make_watch(**kwargs):
    defaults = dict(
        id=1, product_id="iphone-14-pro", name="아이폰14프로", price_min=None,
        price_max=1_000_000, region=None, interval_minutes=30, threshold=60,
        conditions=[
            WatchCondition("burn_in", ["없음"], required=True),
            WatchCondition("full_box", ["풀박스"], required=False),
        ],
    )
    defaults.update(kwargs)
    return Watch(**defaults)


GOOD = {
    "id": "100", "title": "아이폰 14 프로 256 팝니다", "price": 900_000,
    "description": "번인 없습니다. 풀박스 구성입니다.",
}
BURNED = {
    "id": "101", "title": "아이폰14프로 급처", "price": 800_000,
    "description": "번인 있음 감안하세요",
}
OTHER_PRODUCT = {"id": "102", "title": "갤럭시 S24 울트라", "price": 700_000}
ACCESSORY = {"id": "103", "title": "아이폰 14 프로 케이스", "price": 10_000}
TOO_EXPENSIVE = {
    "id": "104", "title": "아이폰 14 프로 미개봉", "price": 1_500_000,
    "description": "번인 없음",
}


async def _run(watch, items, db, config, adapter=None):
    from joongo_notify.catalog import load_catalog

    watch_id = db.insert_watch(watch)
    watch.id = watch_id
    adapter = adapter or FakeAdapter(items)
    notifier = LogNotifier()
    catalog = load_catalog(config.data_dir)
    stats = await run_watch_cycle(watch, [adapter], catalog, db, config, notifier)
    return stats, notifier, adapter


@pytest.mark.asyncio
async def test_end_to_end_match_and_notify(db, config):
    stats, notifier, _ = await _run(
        make_watch(), [GOOD, BURNED, OTHER_PRODUCT, ACCESSORY, TOO_EXPENSIVE], db, config
    )
    # 별칭 필터: GOOD, BURNED, TOO_EXPENSIVE (액세서리·타제품 탈락)
    assert stats.alias_matched == 3
    # 가격 필터로 TOO_EXPENSIVE 탈락 → 신규 2
    assert stats.new_listings == 2
    # 필수 조건(번인 없음) 위반 BURNED는 판정은 되지만 알림 안 됨
    assert stats.notified == 1
    assert "아이폰 14 프로 256" in notifier.sent[0]
    assert "✅" in notifier.sent[0] and "번인" in notifier.sent[0]


@pytest.mark.asyncio
async def test_region_text_filter_in_runner(db, config):
    """지역 필터는 러너가 수행 — 지역 불일치 탈락, 지역 미상 통과 (FR-A5)."""
    seoul = {**GOOD, "id": "300", "region": "서울특별시 강남구"}
    busan = {**GOOD, "id": "301", "region": "부산광역시 해운대구"}
    unknown = {**GOOD, "id": "302"}  # 지역 정보 없음 → 통과 (미탐 회피)
    stats, notifier, _ = await _run(
        make_watch(region="서울"), [seoul, busan, unknown], db, config
    )
    assert stats.new_listings == 2
    assert stats.notified == 2
    assert not any("301" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_no_duplicate_notification_on_second_run(db, config):
    """FR-D1 AC: 동일 매물 재수집 시 중복 알림 없음."""
    watch = make_watch()
    stats1, notifier1, _ = await _run(watch, [GOOD], db, config)
    assert stats1.notified == 1

    # 같은 watch로 다시 실행 (watch는 이미 DB에 있음 → 직접 사이클만 재실행)
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    notifier2 = LogNotifier()
    stats2 = await run_watch_cycle(
        watch, [FakeAdapter([GOOD])], catalog, db, config, notifier2
    )
    assert stats2.new_listings == 0  # FR-C1 AC: 재수집해도 신규 레코드 없음
    assert stats2.notified == 0
    assert notifier2.sent == []


@pytest.mark.asyncio
async def test_adapter_failure_isolated_and_alerted(db, config):
    """NFR-6 + FR-D4: 어댑터 실패가 격리되고, N회 연속 실패 시 운영자 알림."""
    config.collect.adapter_failure_threshold = 2
    watch = make_watch()
    watch.id = db.insert_watch(watch)
    failing = FakeAdapter([], fail=True)
    healthy = FakeAdapter([GOOD])
    healthy.platform = "fake-healthy"  # 실패 카운터가 플랫폼별임을 반영
    notifier = LogNotifier()
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    stats = await run_watch_cycle(watch, [failing, healthy], catalog, db, config, notifier)
    assert stats.notified == 1  # 건강한 어댑터는 정상 동작

    await run_watch_cycle(watch, [failing], catalog, db, config, notifier)
    # 2회째 연속 실패 → 운영자 경고 발송
    assert any("fake" in m and "연속 실패" in m for m in notifier.sent)


class FlakyNotifier(LogNotifier):
    """처음 N회 발송 실패 후 성공 — 알림 재시도 검증용."""

    def __init__(self, fail_times=1):
        super().__init__()
        self.fail_times = fail_times

    async def send_match(self, watch, listing, match):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("push channel down")
        await super().send_match(watch, listing, match)


@pytest.mark.asyncio
async def test_notification_retried_after_send_failure(db, config):
    """알림 발송 실패 시 매칭이 유실되지 않고 다음 사이클에 재발송된다."""
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    watch = make_watch()
    watch.id = db.insert_watch(watch)
    notifier = FlakyNotifier(fail_times=1)

    stats1 = await run_watch_cycle(watch, [FakeAdapter([GOOD])], catalog, db, config, notifier)
    assert stats1.notified == 0  # 발송 실패
    assert db.unnotified_matches(watch.id)  # notified_at=NULL로 남음

    stats2 = await run_watch_cycle(watch, [FakeAdapter([GOOD])], catalog, db, config, notifier)
    assert stats2.notified == 1  # 재시도 성공
    assert not db.unnotified_matches(watch.id)
    assert len(notifier.sent) == 1  # 중복 발송 없음


@pytest.mark.asyncio
async def test_empty_search_counts_as_adapter_failure(db, config):
    """200+0건 응답도 실패로 집계 (FR-D4 소프트 차단 감지)."""
    config.collect.adapter_failure_threshold = 2
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    watch = make_watch()
    watch.id = db.insert_watch(watch)
    notifier = LogNotifier()
    empty = FakeAdapter([])
    await run_watch_cycle(watch, [empty], catalog, db, config, notifier)
    await run_watch_cycle(watch, [empty], catalog, db, config, notifier)
    assert any("0건" in m for m in notifier.sent)  # 임계 도달 시 운영자 경고


class FlakyDetailAdapter(FakeAdapter):
    """상세 조회가 처음 N회 실패."""

    def __init__(self, items, detail_fail_times=1):
        super().__init__(items)
        self.detail_fail_times = detail_fail_times

    async def detail(self, raw):
        if self.detail_fail_times > 0:
            self.detail_fail_times -= 1
            raise RuntimeError("detail api down")
        return await super().detail(raw)


@pytest.mark.asyncio
async def test_detail_failure_defers_analysis_until_fetched(db, config):
    """상세 실패 시 제목만으로 분석·캐시하지 않고, 다음 사이클에 상세 재시도 후 분석한다."""
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    watch = make_watch()
    watch.id = db.insert_watch(watch)
    adapter = FlakyDetailAdapter([GOOD], detail_fail_times=1)
    notifier = LogNotifier()

    stats1 = await run_watch_cycle(watch, [adapter], catalog, db, config, notifier)
    assert stats1.analyzed == 0  # 상세 실패 → 분석 보류 (제목만 리포트 금지)
    assert stats1.notified == 0

    stats2 = await run_watch_cycle(watch, [adapter], catalog, db, config, notifier)
    assert stats2.analyzed == 1  # 상세 확보 후 본문 기반 분석
    assert stats2.notified == 1
    assert "번인" in notifier.sent[0]


@pytest.mark.asyncio
async def test_detail_budget_shared_across_adapters(db, config):
    """상세 조회 예산이 어댑터별이 아니라 사이클 전체에서 공유된다 (NFR-3)."""
    config.collect.max_new_details_per_cycle = 1
    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    watch = make_watch()
    watch.id = db.insert_watch(watch)
    a1 = FakeAdapter([GOOD])
    a2 = FakeAdapter([{**GOOD, "id": "200"}])
    a2.platform = "fake2"
    await run_watch_cycle(watch, [a1, a2], catalog, db, config, LogNotifier())
    assert a1.detail_calls + a2.detail_calls == 1


@pytest.mark.asyncio
async def test_detail_fetched_only_for_new(db, config):
    """상세 조회는 신규 매물에만 (수집 예절)."""
    watch = make_watch()
    _, _, adapter1 = await _run(watch, [GOOD], db, config)
    assert adapter1.detail_calls == 1

    from joongo_notify.catalog import load_catalog

    catalog = load_catalog(config.data_dir)
    adapter2 = FakeAdapter([GOOD])
    await run_watch_cycle(watch, [adapter2], catalog, db, config, LogNotifier())
    assert adapter2.detail_calls == 0
