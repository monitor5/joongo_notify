"""FR-D6: 자동 채팅 — 템플릿, 큐잉, 발송 상한 (RPA 실행 없이)."""
import pytest

from joongo_notify.autochat.sender import ChatSender, within_send_limits
from joongo_notify.autochat.template import build_chat_message
from joongo_notify.config import AutoChatConfig
from joongo_notify.models import (
    ChatMessage,
    ConditionVerdict,
    MatchResult,
    Watch,
    WatchCondition,
    utcnow_iso,
)


def make_watch(mode="approve", price=850000, threshold=60, chat_threshold=None):
    return Watch(
        id=1, product_id="iphone-14-pro", name="t", price_min=None, price_max=None,
        region=None, interval_minutes=30, threshold=threshold,
        conditions=[WatchCondition("burn_in", ["없음"], required=True)],
        auto_chat_mode=mode, auto_chat_threshold=chat_threshold, auto_chat_price=price,
    )


def make_match(score, verdicts=None):
    return MatchResult(watch_id=1, listing_id=1, score=score, passed=True,
                       verdicts=verdicts or [])


def test_template_includes_price_and_questions():
    watch = make_watch(price=850000)
    verdicts = [
        ConditionVerdict("burn_in", "번인", True, "unmentioned", "언급없음", "", 0),
        ConditionVerdict("battery", "배터리효율", False, "unmentioned", "언급없음", "", 0),
    ]
    msg = build_chat_message(watch, make_match(80, verdicts), "iPhone 14 Pro")
    assert "iPhone 14 Pro" in msg
    assert "850,000원" in msg
    assert "번인" in msg or "잔상" in msg
    assert "배터리" in msg


def test_template_no_questions_when_all_known():
    watch = make_watch()
    verdicts = [ConditionVerdict("burn_in", "번인", True, "satisfied", "없음", "번인없음", 0)]
    msg = build_chat_message(watch, make_match(90, verdicts), "iPhone 14 Pro")
    assert "여쭤" not in msg  # 물어볼 게 없음


@pytest.mark.asyncio
async def test_enqueue_approve_mode(db, config):
    from joongo_notify.catalog import load_catalog
    from joongo_notify.notify.base import ConsoleNotifier
    from joongo_notify.pipeline.runner import run_watch_cycle
    from tests.test_pipeline import GOOD, FakeAdapter

    catalog = load_catalog(config.data_dir)
    watch = make_watch(mode="approve")
    watch.conditions = [WatchCondition("burn_in", ["없음"], required=True)]
    watch.id = db.insert_watch(watch)
    await run_watch_cycle(watch, [FakeAdapter([GOOD])], catalog, db, config, ConsoleNotifier())
    pending = db.chats_by_status("pending")
    assert len(pending) == 1
    assert pending[0].platform == "fake"
    assert db.chats_by_status("queued") == []  # approve는 pending에 머묾


@pytest.mark.asyncio
async def test_enqueue_auto_mode_queues(db, config):
    from joongo_notify.catalog import load_catalog
    from joongo_notify.notify.base import ConsoleNotifier
    from joongo_notify.pipeline.runner import run_watch_cycle
    from tests.test_pipeline import GOOD, FakeAdapter

    catalog = load_catalog(config.data_dir)
    watch = make_watch(mode="auto")
    watch.conditions = [WatchCondition("burn_in", ["없음"], required=True)]
    watch.id = db.insert_watch(watch)
    await run_watch_cycle(watch, [FakeAdapter([GOOD])], catalog, db, config, ConsoleNotifier())
    assert len(db.chats_by_status("queued")) == 1


@pytest.mark.asyncio
async def test_no_chat_below_chat_threshold(db, config):
    from joongo_notify.catalog import load_catalog
    from joongo_notify.notify.base import ConsoleNotifier
    from joongo_notify.pipeline.runner import run_watch_cycle
    from tests.test_pipeline import FakeAdapter

    catalog = load_catalog(config.data_dir)
    # 번인 언급없는 매물 → 필수 언급없음 -10 = 90점. 알림(60)은 통과, 자동문의(95)는 미달
    item = {"id": "500", "title": "아이폰 14 프로 팝니다", "price": 800000,
            "description": "급처합니다 직거래만"}
    watch = make_watch(mode="auto", threshold=60, chat_threshold=95)
    watch.conditions = [WatchCondition("burn_in", ["없음"], required=True)]
    watch.id = db.insert_watch(watch)
    await run_watch_cycle(watch, [FakeAdapter([item])], catalog, db, config, ConsoleNotifier())
    assert db.chats_by_status("queued") == []


def _add_listing(db, pid):
    from joongo_notify.models import Listing

    lid, _ = db.upsert_listing(Listing(
        id=None, platform="fake", platform_id=pid, url="u", title="아이폰 14 프로",
        description="", price=800000, region=None, images=[], posted_at=None))
    return lid


def test_send_limits(db, config):
    cfg = AutoChatConfig(hourly_limit=2, daily_limit=5)
    watch_id = db.insert_watch(make_watch())
    assert within_send_limits(db, cfg)  # 이력 없으면 통과
    # sent 2건 주입 → 시간당 상한 도달
    for pid in ["a", "b"]:
        lid = _add_listing(db, pid)
        cid = db.enqueue_chat(ChatMessage(None, watch_id, lid, "fake", "u", "m"))
        db.set_chat_status(cid, "sent", sent=True)
    assert not within_send_limits(db, cfg)


@pytest.mark.asyncio
async def test_dry_run_moves_to_terminal_state(db, config, monkeypatch):
    """dry-run 발송은 queued로 되돌아가지 않고 터미널 상태로 → 무한 재처리 방지."""
    from joongo_notify.autochat import sender as sender_mod

    cfg = AutoChatConfig(enabled=True, dry_run=True)
    watch_id = db.insert_watch(make_watch())
    lid = _add_listing(db, "dry")
    db.enqueue_chat(ChatMessage(None, watch_id, lid, "fake", "u", "m", status="queued"))

    # send()를 성공으로 스텁 (실제 브라우저 미기동)
    async def fake_send(self, chat):
        return None
    monkeypatch.setattr(ChatSender, "send", fake_send)

    from joongo_notify.autochat.sender import process_chat_queue
    await process_chat_queue(db, cfg)
    assert db.chats_by_status("queued") == []  # 더 이상 재처리 대상 아님
    assert len(db.chats_by_status("dry_run_ok")) == 1

    # 두 번째 실행에서 재처리되지 않음
    await process_chat_queue(db, cfg)
    assert len(db.chats_by_status("dry_run_ok")) == 1


@pytest.mark.asyncio
async def test_send_rejects_undefined_selectors():
    """셀렉터가 없는 플랫폼은 발송 거부 (오발송 방지)."""
    sender = ChatSender(AutoChatConfig(), selectors={})
    chat = ChatMessage(1, 1, 1, "bunjang", "https://x", "안녕하세요")
    with pytest.raises(RuntimeError, match="셀렉터"):
        await sender.send(chat)


def test_enqueue_dedup(db):
    watch_id = db.insert_watch(make_watch())
    lid = _add_listing(db, "z")
    first = db.enqueue_chat(ChatMessage(None, watch_id, lid, "fake", "u", "m"))
    second = db.enqueue_chat(ChatMessage(None, watch_id, lid, "fake", "u", "m2"))
    assert first is not None
    assert second is None  # 같은 (watch, listing) 재큐잉 안 됨
