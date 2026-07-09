"""번개장터 어댑터 (10번 문서 §2 — 실측 확인된 무인증 JSON API).

- 목록: api.bunjang.co.kr/api/1/find_v2.json?q=<검색어>&order=date
- 상세: api.bunjang.co.kr/api/pms/v3/products-detail/<pid>?viewerUid=-1
비공식 API이므로 스키마 변경에 대비해 필드 접근을 방어적으로 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..config import CollectConfig
from .base import RateLimiter, RawListing

SEARCH_URL = "https://api.bunjang.co.kr/api/1/find_v2.json"
DETAIL_URL = "https://api.bunjang.co.kr/api/pms/v3/products-detail/{pid}"
PRODUCT_URL = "https://m.bunjang.co.kr/products/{pid}"
IMAGE_RES = "600"


def _epoch_to_iso(epoch: object) -> str | None:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_search_item(item: dict) -> RawListing | None:
    """find_v2 목록 항목 1건 파싱. 광고/이상 항목은 None."""
    pid = item.get("pid")
    if not pid or item.get("ad"):
        return None
    return RawListing(
        platform="bunjang",
        platform_id=str(pid),
        title=str(item.get("name", "")),
        url=PRODUCT_URL.format(pid=pid),
        price=_to_int(item.get("price")),
        region=item.get("location") or None,
        posted_at=_epoch_to_iso(item.get("update_time")),
        images=[
            str(item["product_image"]).replace("{res}", IMAGE_RES)
        ]
        if item.get("product_image")
        else [],
        extra={"status": item.get("status"), "used": item.get("used")},
    )


def parse_detail(raw: RawListing, data: dict) -> RawListing:
    """products-detail 응답을 RawListing에 병합."""
    product = (data.get("data") or {}).get("product") or {}
    raw.description = str(product.get("description") or "")
    price = _to_int(product.get("price"))
    if price is not None:
        raw.price = price
    image_url = product.get("imageUrl")
    image_count = product.get("imageCount") or 1
    if image_url:
        raw.images = [
            str(image_url).replace("{cnt}", str(i)).replace("{res}", IMAGE_RES)
            for i in range(1, min(int(image_count), 5) + 1)
        ]
    raw.extra.update(
        {
            "condition": product.get("condition"),
            "saleStatus": product.get("saleStatus"),
        }
    )
    return raw


class BunjangAdapter:
    platform = "bunjang"

    def __init__(self, config: CollectConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.limiter = RateLimiter(config)
        self._client = client

    def _headers(self) -> dict:
        return {"User-Agent": self.config.user_agent}

    async def _get_json(self, url: str, params: dict | None = None) -> dict:
        await self.limiter.wait()
        if self._client is not None:
            resp = await self._client.get(url, params=params, headers=self._headers())
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        data = await self._get_json(
            SEARCH_URL, params={"q": query, "order": "date", "n": 50, "page": 0}
        )
        items = data.get("list") or []
        results = [r for r in (parse_search_item(i) for i in items) if r]
        # 번개장터는 지역 파라미터가 없어 location 텍스트로 후처리 필터 (FR-A5)
        if region:
            results = [r for r in results if r.region and region in r.region]
        return results

    async def detail(self, raw: RawListing) -> RawListing:
        data = await self._get_json(
            DETAIL_URL.format(pid=raw.platform_id), params={"viewerUid": "-1"}
        )
        return parse_detail(raw, data)
