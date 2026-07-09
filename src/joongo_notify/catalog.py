"""카탈로그: 카테고리/속성 온톨로지 + 제품 별칭 사전 로딩과 별칭 매칭 (FR-B1~B3).

①단계 필터의 실체. 정규화(소문자화·공백/구분자 제거) 후 부분 문자열 매칭으로
"아이폰14프로" == "아이폰 14 Pro" == "iphone14pro"를 동일 취급한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import AttributeValue, Category, ConditionAttribute, Product

_NORMALIZE_RE = re.compile(r"[\s\-_/.,()\[\]+~!·]+")

# 판매글이 아닌 것(구매 희망/매입 업자 글) — 전 제품 공통 제외 (실매물 스모크에서 발견된 오탐)
GLOBAL_EXCLUDE_KEYWORDS = ["삽니다", "구합니다", "구매합니다", "매입", "삽니당", "구해요", "구매원해요"]


def normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text.lower())


class Catalog:
    def __init__(self, categories: list[Category], products: list[Product]):
        self.categories = {c.id: c for c in categories}
        self.products = {p.id: p for p in products}
        # 제품별 정규화 별칭 (정식명 포함)
        self._alias_index: dict[str, list[str]] = {
            p.id: [normalize(a) for a in [p.name, *p.aliases] if a.strip()]
            for p in products
        }

    def category_of(self, product_id: str) -> Category | None:
        product = self.products.get(product_id)
        return self.categories.get(product.category_id) if product else None

    def search_terms(self, product_id: str) -> list[str]:
        """플랫폼 검색어로 쓸 원본 표기 목록 (정식명 + 별칭)."""
        product = self.products[product_id]
        return [product.name, *product.aliases]

    def matches(self, product_id: str, title: str) -> bool:
        """제목이 제품 별칭 사전과 매칭되는가 (FR-B3 AC). 제외 키워드 포함 시 탈락."""
        product = self.products.get(product_id)
        if not product:
            return False
        norm_title = normalize(title)
        if not any(alias in norm_title for alias in self._alias_index[product_id]):
            return False
        excludes = [*product.exclude_keywords, *GLOBAL_EXCLUDE_KEYWORDS]
        return not any(normalize(kw) in norm_title for kw in excludes if kw)


def _parse_attribute(raw: dict) -> ConditionAttribute:
    values = [
        AttributeValue(value=v["value"], patterns=list(v.get("patterns", [])))
        for v in raw.get("values", [])
    ]
    return ConditionAttribute(
        id=raw["id"],
        name=raw["name"],
        type=raw.get("type", "enum"),
        values=values,
        visual=bool(raw.get("visual", False)),
        topics=list(raw.get("topics", [])),
    )


def load_catalog(data_dir: str | Path) -> Catalog:
    data_dir = Path(data_dir)
    cat_raw = yaml.safe_load((data_dir / "categories.yaml").read_text(encoding="utf-8"))
    prod_raw = yaml.safe_load((data_dir / "products.yaml").read_text(encoding="utf-8"))

    categories = [
        Category(
            id=c["id"],
            name=c["name"],
            parent=c.get("parent"),
            attributes=[_parse_attribute(a) for a in c.get("attributes", [])],
        )
        for c in cat_raw.get("categories", [])
    ]
    products = [
        Product(
            id=p["id"],
            name=p["name"],
            category_id=p["category"],
            aliases=list(p.get("aliases", [])),
            exclude_keywords=list(p.get("exclude_keywords", [])),
        )
        for p in prod_raw.get("products", [])
    ]

    category_ids = {c.id for c in categories}
    unknown = [p.id for p in products if p.category_id not in category_ids]
    if unknown:
        raise ValueError(f"products.yaml: 존재하지 않는 카테고리를 참조: {unknown}")
    return Catalog(categories, products)
