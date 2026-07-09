"""플랫폼 어댑터 공통 인터페이스 (FR-C0).

새 플랫폼 추가 = 이 인터페이스 구현 1개. 파이프라인은 어댑터 내부를 모른다.
레이트리밋(NFR-3)은 어댑터가 아니라 여기 RateLimiter가 강제한다.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..config import CollectConfig


@dataclass
class RawListing:
    """어댑터가 반환하는 매물 원시 데이터 (정규화 전)."""

    platform: str
    platform_id: str
    title: str
    url: str
    price: int | None = None
    region: str | None = None
    posted_at: str | None = None
    description: str = ""
    images: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class RateLimiter:
    """요청 간 랜덤 지연 + 분당 상한 (10번 문서 §8 확정안)."""

    def __init__(self, config: CollectConfig):
        self.config = config
        self._timestamps: list[float] = []

    async def wait(self) -> None:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < 60.0]
        if len(self._timestamps) >= self.config.max_requests_per_minute:
            await asyncio.sleep(60.0 - (now - self._timestamps[0]) + 0.1)
        await asyncio.sleep(
            random.uniform(
                self.config.request_delay_min_seconds, self.config.request_delay_max_seconds
            )
        )
        self._timestamps.append(time.monotonic())


class CollectorAdapter(Protocol):
    platform: str

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        """검색어(+지역)로 최신 매물 목록을 반환한다. 본문은 비어 있을 수 있다."""
        ...

    async def detail(self, raw: RawListing) -> RawListing:
        """목록 항목에 본문·이미지 등 상세를 채워 반환한다."""
        ...
