"""Phase 3 웹 UI 고도화: Watch 수정, 매물 이력, 매칭 상세, 시세, 문의 큐, 피드백."""
import pytest
from httpx import ASGITransport, AsyncClient

from joongo_notify.catalog import load_catalog
from joongo_notify.models import Listing, MatchResult, Watch, WatchCondition
from joongo_notify.web.app import build_price_chart, create_app
from joongo_notify.web.auth import hash_password


@pytest.fixture()
def app(config, db):
    config.auth.password_hash = hash_password("pw", iterations=1000)
    config.auth.session_secret = "s"
    return create_app(config=config, db=db, catalog=load_catalog(config.data_dir),
                      run_scheduler=False)


@pytest.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/login", data={"username": "admin", "password": "pw"})
        yield c


def _watch(db, **kw):
    defaults = dict(id=None, product_id="iphone-14-pro", name="w", price_min=None,
                    price_max=900000, region=None, interval_minutes=30, threshold=60,
                    conditions=[WatchCondition("burn_in", ["없음"], required=True)])
    defaults.update(kw)
    return db.insert_watch(Watch(**defaults))


def _listing(db, **kw):
    defaults = dict(id=None, platform="bunjang", platform_id="1", url="https://x/1",
                    title="아이폰 14 프로", description="번인 없음", price=800000,
                    region="서울", images=[])
    defaults.update(kw)
    lid, _ = db.upsert_listing(Listing(posted_at=None, **defaults))
    return lid


async def test_edit_watch(client, db):
    wid = _watch(db, name="원래이름")
    resp = await client.post(f"/watches/{wid}/edit", data={
        "product_id": "iphone-14-pro", "name": "바뀐이름", "threshold": "70",
        "cond_burn_in": ["없음"], "req_burn_in": "on",
        "auto_chat_mode": "approve", "auto_chat_price": "850000",
    })
    assert resp.status_code == 303
    w = db.get_watch(wid)
    assert w.name == "바뀐이름"
    assert w.threshold == 70
    assert w.auto_chat_mode == "approve"
    assert w.auto_chat_price == 850000


async def test_edit_preserves_status_and_created(client, db):
    wid = _watch(db)
    db.set_watch_status(wid, "paused")
    await client.post(f"/watches/{wid}/edit", data={
        "product_id": "iphone-14-pro", "name": "x", "cond_burn_in": ["없음"]})
    assert db.get_watch(wid).status == "paused"  # 수정이 상태를 되돌리지 않음


async def test_listings_page(client, db):
    _listing(db)
    resp = await client.get("/listings")
    assert resp.status_code == 200 and "아이폰 14 프로" in resp.text
    resp2 = await client.get("/listings?platform=daangn")
    assert "아이폰 14 프로" not in resp2.text  # 다른 플랫폼 필터


async def test_match_detail_and_feedback(client, db):
    wid = _watch(db)
    lid = _listing(db)
    db.save_match(MatchResult(watch_id=wid, listing_id=lid, score=85, passed=True,
                              verdicts=[]))
    match_id = db.recent_matches()[0]["id"]
    resp = await client.get(f"/matches/{match_id}")
    assert resp.status_code == 200 and "아이폰 14 프로" in resp.text
    # 피드백
    resp = await client.post(f"/matches/{match_id}/feedback", data={"feedback": "good"})
    assert resp.status_code == 303
    assert db.get_match(match_id)["feedback"] == "good"


async def test_prices_page(client, db):
    wid = _watch(db)
    for i, price in enumerate([800000, 850000, 780000]):
        lid = _listing(db, platform_id=str(i), url=f"https://x/{i}", price=price)
        db.save_match(MatchResult(watch_id=wid, listing_id=lid, score=80, passed=True, verdicts=[]))
    resp = await client.get("/prices?product=iphone-14-pro")
    assert resp.status_code == 200
    assert "svg" in resp.text.lower()


async def test_chat_approve_flow(client, db):
    from joongo_notify.models import ChatMessage

    wid = _watch(db)
    lid = _listing(db)
    cid = db.enqueue_chat(ChatMessage(None, wid, lid, "bunjang", "https://x/1", "안녕하세요", status="pending"))
    resp = await client.post(f"/chats/{cid}/approve")
    assert resp.status_code == 303
    assert db.get_chat(cid).status == "queued"
    # 취소
    cid2_lid = _listing(db, platform_id="2", url="https://x/2")
    cid2 = db.enqueue_chat(ChatMessage(None, wid, cid2_lid, "bunjang", "https://x/2", "m", status="pending"))
    await client.post(f"/chats/{cid2}/cancel")
    assert db.get_chat(cid2).status == "cancelled"


async def test_negative_values_rejected(client, db):
    """음수 threshold/price는 서버에서 거부되고 기본값으로 폴백 (raw POST 방어)."""
    wid = _watch(db, threshold=60)
    await client.post(f"/watches/{wid}/edit", data={
        "product_id": "iphone-14-pro", "name": "x", "threshold": "-50",
        "price_min": "--5", "auto_chat_price": "-1000", "cond_burn_in": ["없음"]})
    w = db.get_watch(wid)
    assert w.threshold == 60  # -50 거부 → 기본값
    assert w.price_min is None  # '--5' 거부
    assert w.auto_chat_price is None  # 음수 거부 (판매자에 음수가 발송되지 않음)


async def test_threshold_clamped_to_100(client, db):
    wid = _watch(db)
    await client.post(f"/watches/{wid}/edit", data={
        "product_id": "iphone-14-pro", "name": "x", "threshold": "9999", "cond_burn_in": ["없음"]})
    assert db.get_watch(wid).threshold == 100


async def test_feedback_open_redirect_blocked(client, db):
    """referer가 외부 절대 URL이면 무시하고 안전한 경로로 리다이렉트."""
    wid = _watch(db)
    lid = _listing(db)
    db.save_match(MatchResult(watch_id=wid, listing_id=lid, score=85, passed=True, verdicts=[]))
    mid = db.recent_matches()[0]["id"]
    resp = await client.post(f"/matches/{mid}/feedback", data={"feedback": "good"},
                             headers={"referer": "https://evil.example.com/phish"},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert "evil.example.com" not in resp.headers["location"]


def test_build_price_chart():
    assert build_price_chart([]) is None
    assert build_price_chart([{"day": "2026-07-01", "price": 800000}]) is None
    chart = build_price_chart([
        {"day": "2026-07-01", "price": 800000},
        {"day": "2026-07-02", "price": 850000},
        {"day": "2026-07-02", "price": 780000},
    ])
    assert chart is not None
    assert len(chart["dots"]) == 3
    assert chart["lo"] == 780000 and chart["hi"] == 850000
