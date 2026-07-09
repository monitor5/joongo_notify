"""Watch 1건의 수집→필터→분석→스코어링→알림 사이클 (계획서 §6 깔때기).

코드 리뷰 반영 사항:
- 알림 발송 실패 시 매칭은 notified_at=NULL로 남고 다음 사이클에 재시도된다 (알림 유실 방지).
- 상세 미확보(detail_fetched=0) 매물은 신규 여부와 무관하게 다음 사이클에 상세를 재시도하고,
  상세 확보 전에는 분석하지 않는다 (제목만으로 분석한 리포트가 영구 캐시되는 문제 방지).
- 상세 조회 예산은 사이클 전체에서 공유한다 (어댑터 수 × 예산이 되지 않도록).
- 검색이 0건이면 어댑터 실패로 집계한다 (FR-D4: 200+빈 응답 소프트 차단 감지).
- 운영자 알림 발송 자체의 실패가 사이클을 중단시키지 않는다 (NFR-6).
"""
from __future__ import annotations

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
    judged: int = 0
    notified: int = 0


@dataclass
class DetailBudget:
    """사이클 전체에서 공유하는 상세 조회 예산 (NFR-3)."""

    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


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
        detail_fetched=bool(raw.description),  # 목록 응답에 본문이 있으면 상세 불필요
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


async def _alert_operator(notifier: Notifier, text: str) -> None:
    """운영자 알림 실패가 사이클을 죽이지 않게 격리."""
    try:
        await notifier.send_operator_alert(text)
    except Exception as exc:
        logger.error("운영자 알림 발송 실패: %s", exc)


async def _send_match(
    watch: Watch, listing: Listing, match: MatchResult, db: Database,
    notifier: Notifier, stats: CycleStats,
) -> None:
    """발송 성공 시에만 notified_at 마킹 — 실패하면 다음 사이클에 재시도된다."""
    try:
        await notifier.send_match(watch, listing, match)
        db.mark_notified(watch.id, listing.id)
        stats.notified += 1
    except Exception as exc:
        logger.error("알림 발송 실패 (watch=%s listing=%s), 다음 사이클 재시도: %s",
                     watch.id, listing.id, exc)


async def _retry_unnotified(
    watch: Watch, db: Database, notifier: Notifier, stats: CycleStats
) -> None:
    """이전 사이클에서 발송 실패한 매칭 재시도 (FR-D1 알림 유실 방지)."""
    for match in db.unnotified_matches(watch.id):
        listing = db.get_listing(match.listing_id)
        if listing:
            await _send_match(watch, listing, match, db, notifier, stats)


async def _process_listing(
    watch: Watch, raw: RawListing, adapter: CollectorAdapter, category,
    catalog: Catalog, db: Database, config: Config, notifier: Notifier,
    stats: CycleStats, budget: DetailBudget,
) -> None:
    # ① 별칭 필터 + 가격 필터 (목록 단계)
    if not catalog.matches(watch.product_id, raw.title):
        return
    stats.alias_matched += 1
    if not price_in_range(watch, raw.price):
        return

    listing_id, is_new = db.upsert_listing(normalize_raw(raw))
    if is_new:
        stats.new_listings += 1
    listing = db.get_listing(listing_id)

    # 상세 미확보 매물은 (신규 여부와 무관하게) 상세 시도 — 실패 시 다음 사이클 재시도
    if not listing.detail_fetched:
        if not budget.take():
            return  # 예산 소진 — 분석은 상세 확보 후에
        try:
            raw = await adapter.detail(raw)
        except Exception as exc:
            logger.warning("%s %s 상세 조회 실패 (다음 사이클 재시도): %s",
                           adapter.platform, raw.platform_id, exc)
            return
        db.update_listing_detail(listing_id, raw.description, raw.price, raw.images)
        listing = db.get_listing(listing_id)

    if not price_in_range(watch, listing.price):
        return

    # ② 본문 분석 — 기존 리포트 재사용 (상세 확보 후에만 생성되므로 안전)
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

    # ③(VL)은 Phase 2 — 스코어링으로 직행
    score, passed, verdicts = score_listing(watch, report, category, config.scoring)
    match = MatchResult(
        watch_id=watch.id, listing_id=listing_id, score=score,
        passed=passed, verdicts=verdicts,
    )
    if not db.save_match(match):
        return  # 이미 판정한 조합 (미발송분 재시도는 _retry_unnotified가 담당)
    stats.judged += 1

    if passed:
        await _send_match(watch, listing, match, db, notifier, stats)


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

    # 0) 이전 사이클에서 발송 실패한 알림부터 재시도
    await _retry_unnotified(watch, db, notifier, stats)

    # 검색어는 정식명 1개로 시작 (요청 수 절약). 별칭 매칭은 로컬 ①단계가 담당.
    query = catalog.search_terms(watch.product_id)[0]
    budget = DetailBudget(config.collect.max_new_details_per_cycle)

    for adapter in adapters:
        try:
            raws = await adapter.search(query, region=watch.region)
        except Exception as exc:  # 어댑터 장애 격리 (NFR-6)
            failures = db.record_adapter_result(adapter.platform, ok=False, error=str(exc))
            logger.warning("%s 수집 실패 (%d연속): %s", adapter.platform, failures, exc)
            if failures == config.collect.adapter_failure_threshold:  # FR-D4
                await _alert_operator(
                    notifier, f"⚠️ {adapter.platform} 어댑터 {failures}회 연속 실패: {exc}"
                )
            continue

        # 200 + 0건도 실패로 집계 (소프트 차단/구조 변경 감지 — FR-D4 AC "0건/오류")
        ok = len(raws) > 0
        failures = db.record_adapter_result(
            adapter.platform, ok=ok, error="" if ok else "검색 결과 0건"
        )
        if not ok:
            logger.warning("%s 검색 결과 0건 (%d연속)", adapter.platform, failures)
            if failures == config.collect.adapter_failure_threshold:
                await _alert_operator(
                    notifier, f"⚠️ {adapter.platform} 어댑터 {failures}회 연속 0건 — 차단/구조 변경 의심"
                )
            continue

        stats.searched += len(raws)
        for raw in raws:
            await _process_listing(
                watch, raw, adapter, category, catalog, db, config, notifier, stats, budget
            )

    db.touch_watch_run(watch.id)
    logger.info(
        "watch %s '%s': 검색 %d → 별칭매칭 %d → 신규 %d → 분석 %d → 판정 %d → 알림 %d",
        watch.id, watch.name, stats.searched, stats.alias_matched,
        stats.new_listings, stats.analyzed, stats.judged, stats.notified,
    )
    return stats
