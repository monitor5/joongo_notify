"""당근마켓 어댑터 (10번 문서 §4 — robots 허용 경로만 사용).

검색 경로(/kr/buy-sell/s/*)는 robots.txt 금지라 사용하지 않는다. 대신
지역 브라우즈 피드(/kr/buy-sell/?in=<동네>-<지역ID>)를 수집하고, 키워드
매칭은 파이프라인의 ①별칭 필터가 로컬에서 수행한다 (query 파라미터는 무시).

파싱은 페이지에 임베드된 schema.org ld+json ItemList를 사용한다 (실측:
272건/페이지, 제목·본문·가격·이미지·URL 포함 → 상세 조회 자체가 불필요).
SEO 마크업이라 내부 상태(JS 컨텍스트)보다 구조 변경에 강하다.

같은 지역을 감시하는 Watch가 여러 개면 피드를 매번 다시 받지 않도록
지역별 TTL 캐시를 둔다 (NFR-3 저빈도 원칙 — 요청 수를 Watch 수와 분리).
"""
from __future__ import annotations

import logging
import time
from urllib.parse import unquote

from .base import HttpAdapter, RawListing, is_daangn_region_slug, iter_ldjson, region_name_of

logger = logging.getLogger("joongo_notify")

FEED_URL = "https://www.daangn.com/kr/buy-sell/"
FEED_CACHE_TTL_SECONDS = 300.0


def _slug_id(url: str) -> str | None:
    """매물 URL 마지막 경로 조각을 ID로 사용.

    예: .../kr/buy-sell/여성구두-스페인제품-iq5zki4ettt2/ → 여성구두-스페인제품-iq5zki4ettt2
    (해시 접미사가 포함되어 있어 전체 조각이 안정적인 고유 키다)
    """
    path = unquote(url).rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return slug or None


def parse_feed_items(html: str, region: str) -> list[RawListing]:
    region_display = region_name_of(region)  # 알림에는 슬러그가 아닌 동네 이름 표기
    results: list[RawListing] = []
    for data in iter_ldjson(html):
        if not isinstance(data, dict) or data.get("@type") != "ItemList":
            continue
        for element in data.get("itemListElement", []):
            item = (element or {}).get("item") or {}
            if item.get("@type") != "Product":
                continue
            url = str(item.get("url", ""))
            platform_id = _slug_id(url)
            if not platform_id:
                continue
            price = None
            try:
                price = int(float((item.get("offers") or {}).get("price")))
            except (TypeError, ValueError):
                pass
            image = item.get("image")
            results.append(
                RawListing(
                    platform="daangn",
                    platform_id=platform_id,
                    title=str(item.get("name", "")),
                    url=url,
                    price=price,
                    region=region_display,
                    posted_at=None,  # 피드에 게시시각 없음
                    description=str(item.get("description") or ""),
                    images=[str(image)] if image else [],
                )
            )
    return results


class DaangnAdapter(HttpAdapter):
    platform = "daangn"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._feed_cache: dict[str, tuple[float, list[RawListing]]] = {}

    def can_collect(self, region: str | None) -> bool:
        # "동이름-지역ID" 슬러그 없이는 피드 접근 불가 → 러너가 건너뜀
        return is_daangn_region_slug(region)

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        # query는 사용하지 않는다 (검색 경로는 robots 금지 — 별칭 필터가 로컬 수행)
        if not self.can_collect(region):
            logger.warning('daangn: 지역이 "동이름-지역ID" 형식이 아님 (%r) — 수집 생략', region)
            return []
        cached = self._feed_cache.get(region)
        if cached and time.monotonic() - cached[0] < FEED_CACHE_TTL_SECONDS:
            return cached[1]
        html = await self._get_text(FEED_URL, params={"in": region})  # httpx가 인코딩
        results = parse_feed_items(html, region)
        self._feed_cache[region] = (time.monotonic(), results)
        return results

    async def detail(self, raw: RawListing) -> RawListing:
        return raw  # 피드에 본문이 이미 포함됨
