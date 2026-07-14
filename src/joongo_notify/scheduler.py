"""수집 스케줄러 — 60초마다 기한 도래한 active Watch를 실행 (FR-C1)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .adapters import BunjangAdapter, DaangnAdapter, JoongnaAdapter
from .adapters.base import CollectorAdapter
from .catalog import Catalog
from .config import Config
from .db import Database
from .models import Watch
from .notify.base import LogNotifier, Notifier
from .pipeline.runner import run_watch_cycle

logger = logging.getLogger("joongo_notify")

TICK_SECONDS = 60


ADAPTER_REGISTRY = {
    "bunjang": BunjangAdapter,
    "daangn": DaangnAdapter,
    "joongna": JoongnaAdapter,
}


def build_adapters(config: Config) -> list[CollectorAdapter]:
    adapters: list[CollectorAdapter] = []
    for name in config.collect.platforms:
        cls = ADAPTER_REGISTRY.get(name)
        if cls is None:
            logger.warning("알 수 없는 플랫폼 설정 무시: %s", name)
            continue
        adapters.append(cls(config.collect))
    return adapters


def build_notifier(config: Config, db: Database) -> Notifier:
    # 웹 대시보드가 알림 표면 — Notifier는 로그로 남긴다 (푸시 채널 없음)
    return LogNotifier(web_base=config.web.base_url or "")


def is_due(watch: Watch, now: datetime, min_interval: int) -> bool:
    if watch.status != "active":
        return False
    if not watch.last_run_at:
        return True
    interval = max(watch.interval_minutes, min_interval)  # 최소 주기 강제 (FR-C1 AC)
    last = datetime.fromisoformat(watch.last_run_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now >= last + timedelta(minutes=interval)


async def scheduler_loop(config: Config, db: Database, catalog: Catalog) -> None:
    adapters = build_adapters(config)
    notifier = build_notifier(config, db)
    logger.info("스케줄러 시작 (tick=%ds, 기본 주기 %d분)", TICK_SECONDS, config.collect.default_interval_minutes)
    while True:
        try:
            now = datetime.now(timezone.utc)
            for watch in db.list_watches(status="active"):
                if is_due(watch, now, config.collect.min_interval_minutes):
                    await run_watch_cycle(watch, adapters, catalog, db, config, notifier)
            # 자동 채팅 큐 처리 (FR-D6) — auto 모드로 큐잉된 문의를 상한 내 발송
            if config.autochat.enabled:
                from .autochat.sender import process_chat_queue

                await process_chat_queue(db, config.autochat)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("스케줄러 tick 실패")  # 한 tick 실패가 루프를 죽이지 않게 (NFR-6)
        await asyncio.sleep(TICK_SECONDS)


async def run_once(config: Config, db: Database, catalog: Catalog, watch_id: int | None = None) -> None:
    """CLI: 모든(또는 지정) Watch를 즉시 1회 실행."""
    adapters = build_adapters(config)
    notifier = build_notifier(config, db)
    watches = [db.get_watch(watch_id)] if watch_id else db.list_watches(status="active")
    for watch in watches:
        if watch:
            await run_watch_cycle(watch, adapters, catalog, db, config, notifier)
