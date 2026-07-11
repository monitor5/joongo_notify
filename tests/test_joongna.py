"""중고나라 어댑터 파싱 (실측 RSC flight/ld+json 구조 기반 픽스처)."""
import json

from joongo_notify.adapters.base import RawListing
from joongo_notify.adapters.joongna import parse_detail, parse_search_items

ITEMS = [
    {
        "seq": 223205574,
        "productPositionNo": 1,
        "platformType": 1,
        "price": 350000,
        "parcelFee": 0,
        "url": "https://img2.joongna.com/media/original/x.jpg?impolicy=thumb",
        "title": "아이폰 14 프로 256기가",
        "state": 0,
        "sortDate": "2026-07-11 09:26:46",
        "mainLocationName": "서울 강남구",
        "articleSeq": 0,
        "wishCount": 0,
        "label": {"text": "인증", "type": {"nested": True}},
    },
    {"seq": 214716281, "price": 120000, "title": "갤럭시 S24 울트라", "sortDate": "2026-07-10 08:00:00", "mainLocationName": ""},
]


def _flight_html(items) -> str:
    # RSC 스트림처럼 이스케이프된 JS 문자열로 포장
    inner = json.dumps({"searchResult": {"items": items}}, ensure_ascii=False)
    payload = json.dumps(inner, ensure_ascii=False)  # 문자열 리터럴로 이스케이프
    return f"<script>self.__next_f.push([1,{payload}])</script>"


def test_parse_search_items():
    results = parse_search_items(_flight_html(ITEMS))
    assert len(results) == 2
    first = next(r for r in results if r.platform_id == "223205574")
    assert first.platform == "joongna"
    assert first.title == "아이폰 14 프로 256기가"
    assert first.price == 350000
    assert first.region == "서울 강남구"
    assert first.url == "https://web.joongna.com/product/223205574"
    assert first.posted_at == "2026-07-11T00:26:46+00:00"  # KST → UTC
    second = next(r for r in results if r.platform_id == "214716281")
    assert second.region is None  # 빈 지역명은 None


def test_parse_search_dedups_repeated_payloads():
    html = _flight_html(ITEMS) + _flight_html(ITEMS)
    assert len(parse_search_items(html)) == 2


def test_parse_search_items_empty_html():
    assert parse_search_items("<html>nothing</html>") == []


DETAIL_LDJSON = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "아이폰 14 프로",
    "image": ["https://img2.joongna.com/a.jpg", "https://img2.joongna.com/b.jpg"],
    "description": "번인 없음, 배터리 효율 91%",
    "sku": "223205574",
    "offers": {"price": 350000, "priceCurrency": "KRW"},
}


def test_parse_detail():
    raw = RawListing(platform="joongna", platform_id="223205574", title="t",
                     url="https://web.joongna.com/product/223205574")
    html = f'<script type="application/ld+json">{json.dumps(DETAIL_LDJSON, ensure_ascii=False)}</script>'
    merged = parse_detail(raw, html)
    assert "번인 없음" in merged.description
    assert len(merged.images) == 2
    assert merged.price == 350000


def test_parse_detail_without_ldjson():
    raw = RawListing(platform="joongna", platform_id="1", title="t", url="u")
    merged = parse_detail(raw, "<html></html>")
    assert merged.description == ""
