"""번개장터 어댑터 파싱 (실측 응답 형태 기반 픽스처 — 실 네트워크 호출 없음)."""
from joongo_notify.adapters.bunjang import parse_detail, parse_search_item
from joongo_notify.adapters.base import RawListing

SEARCH_ITEM = {
    "pid": "418841254",
    "name": "아이폰8플러스 블랙 64기가 (밧데리효율 100%)",
    "price": "155000",
    "product_image": "https://media.bunjang.co.kr/product/418841254_1_1783565912_w{res}.jpg",
    "status": "0",
    "ad": False,
    "location": "서울특별시 구로구 신도림동",
    "used": 1,
    "update_time": 1783565912,
}

DETAIL_RESPONSE = {
    "data": {
        "product": {
            "pid": 418841254,
            "name": "아이폰8플러스",
            "description": "번인 없고 배터리 효율 100%입니다",
            "price": 155000,
            "condition": "LIGHTLY_USED",
            "saleStatus": "SELLING",
            "imageUrl": "https://media.bunjang.co.kr/product/418841254_{cnt}_1783565912_w{res}.jpg",
            "imageCount": 3,
        }
    }
}


def test_parse_search_item():
    raw = parse_search_item(SEARCH_ITEM)
    assert raw is not None
    assert raw.platform == "bunjang"
    assert raw.platform_id == "418841254"
    assert raw.price == 155000
    assert raw.region == "서울특별시 구로구 신도림동"
    assert raw.posted_at is not None
    assert "{res}" not in raw.images[0]
    assert raw.url == "https://m.bunjang.co.kr/products/418841254"


def test_parse_search_item_skips_ads():
    assert parse_search_item({**SEARCH_ITEM, "ad": True}) is None


def test_parse_search_item_missing_pid():
    assert parse_search_item({"name": "x"}) is None


def test_parse_detail_merges_description_and_images():
    raw = parse_search_item(SEARCH_ITEM)
    merged = parse_detail(raw, DETAIL_RESPONSE)
    assert "번인 없고" in merged.description
    assert len(merged.images) == 3
    assert "{cnt}" not in merged.images[0] and "{res}" not in merged.images[0]
    assert merged.extra["condition"] == "LIGHTLY_USED"


def test_parse_detail_defensive_on_empty():
    raw = RawListing(platform="bunjang", platform_id="1", title="t", url="u")
    merged = parse_detail(raw, {})
    assert merged.description == ""
