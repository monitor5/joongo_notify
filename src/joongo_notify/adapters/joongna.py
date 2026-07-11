"""중고나라 어댑터 (10번 문서 §3 — 자체 웹만 사용, 네이버 카페 불가 확정).

- 목록: web.joongna.com/search/<검색어> — Next.js RSC flight 스트림에 이스케이프된
  JSON으로 `"items":[{"seq":..,"title":..,"price":..,"sortDate":..}]`이 실려 온다 (실측).
- 상세: web.joongna.com/product/<seq> — schema.org ld+json Product에 본문·이미지 (실측).

flight 파싱은 이스케이프 해제 후 json.JSONDecoder.raw_decode로 배열을 통째로
읽는다 (정규식으로 객체를 자르는 것보다 중첩 구조에 안전).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from .base import HttpAdapter, RawListing

logger = logging.getLogger("joongo_notify")

SEARCH_URL = "https://web.joongna.com/search/{query}"
PRODUCT_URL = "https://web.joongna.com/product/{seq}"
FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', re.S)
LDJSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
KST = timezone(timedelta(hours=9))


def _kst_to_iso(value: object) -> str | None:
    try:
        return (
            datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=KST)
            .astimezone(timezone.utc)
            .isoformat(timespec="seconds")
        )
    except (TypeError, ValueError):
        return None


def _iter_flight_payloads(html: str):
    for m in FLIGHT_RE.finditer(html):
        try:
            # RSC 페이로드는 JS 문자열 리터럴 — JSON 문자열로 감싸 이스케이프 해제
            yield json.loads(f'"{m.group(1)}"')
        except json.JSONDecodeError:
            continue


def parse_search_items(html: str) -> list[RawListing]:
    decoder = json.JSONDecoder()
    results: dict[str, RawListing] = {}
    for payload in _iter_flight_payloads(html):
        for m in re.finditer(r'"items"\s*:\s*\[', payload):
            try:
                items, _ = decoder.raw_decode(payload, m.end() - 1)
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or "seq" not in item or "title" not in item:
                    continue
                seq = str(item["seq"])
                price = item.get("price")
                results[seq] = RawListing(
                    platform="joongna",
                    platform_id=seq,
                    title=str(item.get("title", "")),
                    url=PRODUCT_URL.format(seq=seq),
                    price=int(price) if isinstance(price, (int, float)) else None,
                    region=str(item.get("mainLocationName") or "") or None,
                    posted_at=_kst_to_iso(item.get("sortDate")),
                    images=[str(item["url"])] if item.get("url") else [],
                    extra={"state": item.get("state")},
                )
    return list(results.values())


def parse_detail(raw: RawListing, html: str) -> RawListing:
    for m in LDJSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "Product":
            continue
        raw.description = str(data.get("description") or "")
        images = data.get("image")
        if isinstance(images, list) and images:
            raw.images = [str(i) for i in images[:5]]
        offers = data.get("offers") or {}
        if isinstance(offers.get("price"), (int, float)):
            raw.price = int(offers["price"])
        return raw
    logger.warning("joongna %s: 상세 ld+json 없음 — 본문 없이 진행", raw.platform_id)
    raw.description = raw.description or ""
    return raw


class JoongnaAdapter(HttpAdapter):
    platform = "joongna"

    async def search(self, query: str, region: str | None = None) -> list[RawListing]:
        # 지역 필터는 러너가 수행 (0건=플랫폼 이상 신호 유지)
        html = await self._get_text(SEARCH_URL.format(query=quote(query)))
        return parse_search_items(html)

    async def detail(self, raw: RawListing) -> RawListing:
        html = await self._get_text(PRODUCT_URL.format(seq=raw.platform_id))
        return parse_detail(raw, html)
