"""번개장터 어댑터 (10번 문서 §2 — 실측 확인된 무인증 JSON API).

- 목록: api.bunjang.co.kr/api/1/find_v2.json?q=<검색어>&order=date
- 상세: api.bunjang.co.kr/api/pms/v3/products-detail/<pid>?viewerUid=-1
비공식 API이므로 스키마 변경에 대비해 필드 접근을 방어적으로 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .base import HttpAdapter, RawListing

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
    image_count = product.get("imageCount")
    if image_count is None:
        image_count = 1 if image_url else 0
    if image_url and int(image_count) > 0:  # imageCount=0이면 이미지 URL을 만들지 않음
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


class BunjangAdapter(HttpAdapter):
    platform = "bunjang"

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        # 지역 필터는 러너가 수행 (어댑터 응답 0건 = 플랫폼 이상 신호를 유지하기 위해
        # 필터 전 원본 건수를 그대로 반환한다)
        data = await self._get_json(
            SEARCH_URL, params={"q": query, "order": "date", "n": 50, "page": 0}
        )
        items = data.get("list") or []
        return [r for r in (parse_search_item(i) for i in items) if r]

    async def detail(self, raw: RawListing) -> RawListing:
        data = await self._get_json(
            DETAIL_URL.format(pid=raw.platform_id), params={"viewerUid": "-1"}
        )
        return parse_detail(raw, data)
