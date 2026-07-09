"""Watch 1건의 수집→필터→분석→스코어링→알림 사이클 (계획서 §6 깔때기)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..adapters.base import CollectorAdapter, RawListing
from ..catalog import Catalog
from ..config import Config
from ..db import Database
from ..models import AnalysisReport, Listing, MatchResult, Watch
from ..notify.base import Notifier
from .extract import run_extractor
from .scoring import score_listing

logger = logging.getLogger("joongo_notify")


@dataclass
class CycleStats:
    searched: int = 0
    alias_matched: int = 0
    new_listings: int = 0
    analyzed: int = 0
    matched: int = 0
    notified: int = 0


def normalize_raw(raw: RawListing) -> Listing:
    return Listing(
        id=None,
        platform=raw.platform,
        platform_id=raw.platform_id,
        url=raw.url,
        title=raw.title,
        description=raw.description,
        price=raw.price,
        region=raw.region,
        images=raw.images,
        posted_at=raw.posted_at,
        raw=raw.extra,
    )


def price_in_range(watch: Watch, price: int | None) -> bool:
    if price is None:
        return True  # 가격 미상은 통과시켜 사람이 판단 (미탐 회피 — 계획서 §6)
    if watch.price_min is not None and price < watch.price_min:
        return False
    if watch.price_max is not None and price > watch.price_max:
        return False
    return True


async def run_watch_cycle(
    watch: Watch,
    adapters: list[CollectorAdapter],
    catalog: Catalog,
    db: Database,
    config: Config,
    notifier: Notifier,
) -> CycleStats:
    stats = CycleStats()
    category = catalog.category_of(watch.product_id)
    if category is None:
        logger.error("watch %s: 알 수 없는 제품 %s", watch.id, watch.product_id)
        return stats

    # 검색어는 정식명 1개로 시작 (요청 수 절약). 별칭 매칭은 로컬 ①단계가 담당.
    query = catalog.search_terms(watch.product_id)[0]

    for adapter in adapters:
        try:
            raws = await adapter.search(query, region=watch.region)
        except Exception as exc:  # 어댑터 장애 격리 (NFR-6)
            failures = db.record_adapter_result(adapter.platform, ok=False, error=str(exc))
            logger.warning("%s 수집 실패 (%d연속): %s", adapter.platform, failures, exc)
            if failures == config.collect.adapter_failure_threshold:  # FR-D4
                await notifier.send_operator_alert(
                    f"⚠️ {adapter.platform} 어댑터 {failures}회 연속 실패: {exc}"
                )
            continue
        db.record_adapter_result(adapter.platform, ok=True)
        stats.searched += len(raws)

        detail_budget = config.collect.max_new_details_per_cycle
        for raw in raws:
            # ① 별칭 필터 + 가격/지역 필터
            if not catalog.matches(watch.product_id, raw.title):
                continue
            stats.alias_matched += 1
            if not price_in_range(watch, raw.price):
                continue

            # 신규 매물만 상세 조회·분석 (FR-C1: 재분석 금지)
            listing = normalize_raw(raw)
            listing_id, is_new = db.upsert_listing(listing)
            listing.id = listing_id

            if is_new:
                stats.new_listings += 1
                if detail_budget > 0 and not raw.description:
                    try:
                        raw = await adapter.detail(raw)
                        detail_budget -= 1
                        listing = normalize_raw(raw)
                        listing.id = listing_id
                        db.conn.execute(
                            "UPDATE listings SET description=?, price=?, images_json=? WHERE id=?",
                            (
                                listing.description,
                                listing.price,
                                json.dumps(listing.images, ensure_ascii=False),
                                listing_id,
                            ),
                        )
                        db.conn.commit()
                    except Exception as exc:
                        logger.warning(
                            "%s %s 상세 조회 실패: %s", adapter.platform, raw.platform_id, exc
                        )
                if not price_in_range(watch, listing.price):
                    continue

            # ② 본문 분석 — 기존 리포트 재사용
            report = db.get_report(listing_id, category.id)
            if report is None:
                text = f"{listing.title}\n{listing.description}"
                findings, extractor_name = await run_extractor(config.llm, category, text)
                report = AnalysisReport(
                    listing_id=listing_id,
                    category_id=category.id,
                    extractor=extractor_name,
                    findings=findings,
                )
                db.save_report(report)
                stats.analyzed += 1

            # ③(VL)은 Phase 2 — 여기서는 스코어링으로 직행
            score, passed, verdicts = score_listing(
                watch, report, category, config.scoring
            )
            match = MatchResult(
                watch_id=watch.id,
                listing_id=listing_id,
                score=score,
                passed=passed,
                verdicts=verdicts,
            )
            if not db.save_match(match):
                continue  # 이미 판정한 조합 → 중복 알림 방지 (FR-D1)
            stats.matched += 1

            if passed:
                try:
                    await notifier.send_match(watch, listing, match)
                    db.mark_notified(watch.id, listing_id)
                    stats.notified += 1
                except Exception as exc:
                    logger.error("알림 발송 실패 (watch=%s listing=%s): %s", watch.id, listing_id, exc)

    db.touch_watch_run(watch.id)
    logger.info(
        "watch %s '%s': 검색 %d → 별칭매칭 %d → 신규 %d → 분석 %d → 판정 %d → 알림 %d",
        watch.id, watch.name, stats.searched, stats.alias_matched,
        stats.new_listings, stats.analyzed, stats.matched, stats.notified,
    )
    return stats
