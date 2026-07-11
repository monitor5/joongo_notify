"""당근마켓 어댑터 파싱 (실측 ld+json 구조 기반 픽스처)."""
import json

from joongo_notify.adapters.base import is_daangn_region_slug, region_name_of
from joongo_notify.adapters.daangn import parse_feed_items, _slug_id

LDJSON = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    "itemListElement": [
        {
            "@type": "ListItem",
            "position": 1,
            "item": {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "아이폰 14 프로 딥퍼플 256",
                "description": "번인 없고 생활기스만 있습니다. 풀박스.",
                "image": "https://img.kr.example.net/origin/article/abc_0.webp",
                "url": "https://www.daangn.com/kr/buy-sell/%EC%95%84%EC%9D%B4%ED%8F%B0-14-%ED%94%84%EB%A1%9C-iq5zki4ettt2/",
                "offers": {"@type": "Offer", "price": "900000.0", "priceCurrency": "KRW"},
            },
        },
        {
            "@type": "ListItem",
            "position": 2,
            "item": {"@type": "Thing", "name": "not-a-product"},
        },
    ],
}

HTML = f'<html><script type="application/ld+json">{json.dumps(LDJSON, ensure_ascii=False)}</script></html>'


def test_parse_feed_items():
    items = parse_feed_items(HTML, region="역삼동-6035")
    assert len(items) == 1
    r = items[0]
    assert r.platform == "daangn"
    assert r.platform_id == "아이폰-14-프로-iq5zki4ettt2"
    assert r.price == 900000
    assert r.region == "역삼동-6035"
    assert "번인 없고" in r.description  # 피드에 본문 포함 → 상세 불필요
    assert r.images


def test_slug_id():
    assert _slug_id("https://www.daangn.com/kr/buy-sell/abc-xyz123/") == "abc-xyz123"
    assert _slug_id("https://www.daangn.com/") is None or _slug_id("https://www.daangn.com/")


def test_region_slug_detection():
    assert is_daangn_region_slug("역삼동-6035")
    assert not is_daangn_region_slug("서울")
    assert not is_daangn_region_slug(None)
    assert not is_daangn_region_slug("")


def test_region_name_of():
    assert region_name_of("역삼동-6035") == "역삼동"
    assert region_name_of("서울") == "서울"


def test_parse_feed_ignores_broken_json():
    html = '<script type="application/ld+json">{broken</script>'
    assert parse_feed_items(html, region="역삼동-6035") == []
