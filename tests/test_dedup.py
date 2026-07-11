"""FR-C4: 교차 플랫폼 중복 제거."""
import pytest

from joongo_notify.db import Database
from joongo_notify.models import Listing
from joongo_notify.pipeline.dedup import find_duplicate, title_similarity


def make_listing(platform, platform_id, title, price):
    return Listing(id=None, platform=platform, platform_id=platform_id,
                   url=f"https://{platform}/{platform_id}", title=title,
                   description="", price=price, region=None, images=[], posted_at=None)


def test_title_similarity():
    assert title_similarity("아이폰 14 프로 256기가 딥퍼플", "아이폰 14 프로 딥퍼플 256기가") == 1.0
    assert title_similarity("아이폰 14 프로 256기가", "갤럭시 S24 울트라") < 0.2


def test_find_duplicate_cross_platform(db):
    a = make_listing("bunjang", "1", "아이폰 14 프로 256기가 딥퍼플 팝니다", 900000)
    a_id, _ = db.upsert_listing(a)
    b = make_listing("joongna", "2", "아이폰 14 프로 딥퍼플 256기가 판매", 900000)
    b_id, _ = db.upsert_listing(b)
    b.id = b_id
    assert find_duplicate(db, b) == a_id


def test_no_duplicate_when_price_differs(db):
    a = make_listing("bunjang", "1", "아이폰 14 프로 256기가 딥퍼플", 900000)
    db.upsert_listing(a)
    b = make_listing("joongna", "2", "아이폰 14 프로 딥퍼플 256기가", 850000)
    b.id, _ = db.upsert_listing(b)
    assert find_duplicate(db, b) is None


def test_no_duplicate_same_platform(db):
    a = make_listing("bunjang", "1", "아이폰 14 프로 256기가", 900000)
    db.upsert_listing(a)
    b = make_listing("bunjang", "2", "아이폰 14 프로 256기가", 900000)
    b.id, _ = db.upsert_listing(b)
    assert find_duplicate(db, b) is None  # 같은 플랫폼은 platform_id 유니크가 담당


def test_no_duplicate_without_price(db):
    a = make_listing("bunjang", "1", "아이폰 14 프로", 900000)
    db.upsert_listing(a)
    b = make_listing("joongna", "2", "아이폰 14 프로", None)
    b.id, _ = db.upsert_listing(b)
    assert find_duplicate(db, b) is None


@pytest.mark.asyncio
async def test_duplicate_notification_suppressed(db, config):
    """중복 매물은 같은 Watch로 두 번 알림되지 않는다 (FR-C4 AC)."""
    from joongo_notify.catalog import load_catalog
    from joongo_notify.notify.base import ConsoleNotifier
    from joongo_notify.pipeline.runner import run_watch_cycle
    from tests.test_pipeline import FakeAdapter, make_watch

    catalog = load_catalog(config.data_dir)
    watch = make_watch()
    watch.id = db.insert_watch(watch)

    item = {"id": "100", "title": "아이폰 14 프로 256 팝니다", "price": 900000,
            "description": "번인 없습니다. 풀박스 구성입니다."}
    notifier = ConsoleNotifier()
    await run_watch_cycle(watch, [FakeAdapter([item])], catalog, db, config, notifier)
    assert len(notifier.sent) == 1

    dup = FakeAdapter([{**item, "id": "200"}])
    dup.platform = "fake2"
    await run_watch_cycle(watch, [dup], catalog, db, config, notifier)
    assert len(notifier.sent) == 1  # 중복 알림 생략
