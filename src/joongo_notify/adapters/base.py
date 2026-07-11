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

    def can_collect(self, region: str | None) -> bool:
        """이 Watch 지역 설정으로 수집 가능한가. False면 러너가 조용히 건너뛴다
        (실패 집계 아님). 예: 당근은 지역 슬러그 없이는 수집 불가."""
        ...

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        """검색어(+지역)로 최신 매물 목록을 반환한다. 본문은 비어 있을 수 있다.
        지역/가격 필터링은 러너가 수행하므로 원본 결과를 그대로 반환할 것
        (응답 0건 = 플랫폼 이상 신호를 보존하기 위해)."""
        ...

    async def detail(self, raw: RawListing) -> RawListing:
        """목록 항목에 본문·이미지 등 상세를 채워 반환한다."""
        ...


def iter_ldjson(html: str):
    """페이지에 임베드된 schema.org ld+json 블록들을 파싱해 순회 (당근·중고나라 공용)."""
    import json
    import re

    for m in re.finditer(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    ):
        try:
            yield json.loads(m.group(1))
        except json.JSONDecodeError:
            continue


def region_name_of(region: str) -> str:
    """Watch.region에서 지역명 추출. 당근 슬러그("역삼동-6035")면 이름 부분만."""
    name, _, suffix = region.rpartition("-")
    if name and suffix.isdigit():
        return name
    return region


def is_daangn_region_slug(region: str | None) -> bool:
    """당근 지역 파라미터 형식("역삼동-6035")인지."""
    if not region:
        return False
    name, _, suffix = region.rpartition("-")
    return bool(name) and suffix.isdigit()


BACKOFF_DELAYS = [2.0, 4.0, 8.0, 16.0]  # NFR-3 지수 백오프 (10번 문서 §8)


def _is_retryable(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, httpx.TransportError)


class HttpAdapter:
    """레이트리밋 + 지수 백오프 + 연결 풀 재사용을 제공하는 어댑터 공통 기반."""

    platform = "base"

    def can_collect(self, region: str | None) -> bool:
        return True  # 기본: 지역 설정과 무관하게 수집 가능

    def __init__(self, config: CollectConfig, client=None):
        import httpx

        self.config = config
        self.limiter = RateLimiter(config)
        # 프로세스 수명 동안 연결 풀 재사용 (요청마다 TLS 핸드셰이크 방지)
        self._client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    def _headers(self) -> dict:
        return {"User-Agent": self.config.user_agent}

    async def _get(self, url: str, params: dict | None = None):
        """레이트리밋 + 429/5xx/네트워크 오류 시 지수 백오프 재시도 (NFR-3)."""
        last_exc: Exception | None = None
        for attempt in range(len(BACKOFF_DELAYS) + 1):
            if attempt > 0:
                await asyncio.sleep(BACKOFF_DELAYS[attempt - 1])
            await self.limiter.wait()
            try:
                resp = await self._client.get(url, params=params, headers=self._headers())
                resp.raise_for_status()
                return resp
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                last_exc = exc
        raise last_exc

    async def _get_json(self, url: str, params: dict | None = None) -> dict:
        return (await self._get(url, params=params)).json()

    async def _get_text(self, url: str, params: dict | None = None) -> str:
        return (await self._get(url, params=params)).text
