"""FR-B1~B3: 온톨로지 로딩과 별칭 매칭."""
from joongo_notify.catalog import normalize


def test_loads_categories_and_products(catalog):
    assert "smartphone" in catalog.categories
    assert "iphone-14-pro" in catalog.products
    smartphone = catalog.categories["smartphone"]
    burn_in = smartphone.attribute("burn_in")
    assert burn_in is not None and burn_in.visual is True


def test_normalize():
    assert normalize("아이폰 14 Pro") == normalize("아이폰14pro")
    assert normalize("iPhone-14-Pro") == "iphone14pro"


def test_alias_matching_variants(catalog):
    # FR-B3 AC: 어떤 별칭 표기든 매칭
    for title in [
        "아이폰 14 프로 256기가 팝니다",
        "아이폰14프로 딥퍼플",
        "iPhone 14 Pro 미개봉",
        "급처) 아이폰 14pro 데이지",
    ]:
        assert catalog.matches("iphone-14-pro", title), title


def test_alias_no_false_positive(catalog):
    assert not catalog.matches("iphone-14-pro", "아이폰 15 프로 팝니다")
    assert not catalog.matches("iphone-14-pro", "갤럭시 S24 울트라")


def test_exclude_keywords_reject_accessories(catalog):
    # 액세서리 오탐 방지
    assert not catalog.matches("iphone-14-pro", "아이폰14프로 케이스 팝니다")
    assert not catalog.matches("iphone-14-pro", "아이폰 14 프로 강화 필름")


def test_global_excludes_reject_buy_requests(catalog):
    # 실매물 스모크에서 발견: 구매 희망 글이 매물로 알림되던 오탐
    assert not catalog.matches("iphone-14-pro", "아이폰 14 Pro iOS 16 삽니다")
    assert not catalog.matches("iphone-14-pro", "아이폰14프로 구합니다")
    assert not catalog.matches("iphone-14-pro", "아이폰 14 프로 매입합니다")


def test_search_terms_start_with_official_name(catalog):
    assert catalog.search_terms("iphone-14-pro")[0] == "iPhone 14 Pro"
