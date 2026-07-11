"""교차 플랫폼 중복 제거 (FR-C4).

동일 판매자가 여러 플랫폼에 올린 같은 매물을 묶는다. Phase 2 구현은
정규화 제목 토큰 자카드 유사도 + 동일 가격 (기간 14일 이내, 다른 플랫폼).
이미지 지각해시(pHash)는 후속 고도화 항목 (10번 문서 §8 저빈도 원칙상
이미지 추가 다운로드 비용도 고려).

알림 규칙: 중복 그룹에서 같은 Watch로 이미 알림이 나갔으면 재알림하지 않는다.
"""
from __future__ import annotations

import logging

from ..catalog import SEPARATOR_RE
from ..db import Database
from ..models import Listing

logger = logging.getLogger("joongo_notify")

TITLE_SIMILARITY_THRESHOLD = 0.6
RECENT_DAYS = 14


def title_tokens(title: str) -> set[str]:
    # 별칭 매칭과 동일한 구분자 정의 사용 (catalog와 어긋나지 않게)
    return {t.lower() for t in SEPARATOR_RE.split(title) if len(t) >= 2}


def title_similarity(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def find_duplicate(db: Database, listing: Listing) -> int | None:
    """다른 플랫폼의 동일 매물(원본) id를 반환. 없으면 None.

    후보 축소는 SQL(동일 가격, 최근 N일, 타 플랫폼), 판정은 제목 유사도.
    """
    if listing.price is None:
        return None  # 가격 없이 제목만으로 묶는 건 오탐 위험이 큼
    # collected_at은 ISO-8601('T' 구분자)이므로 비교 기준도 같은 포맷으로 생성
    # (기본 datetime()은 공백 구분자라 문자열 비교가 경계일에서 어긋난다)
    rows = db.conn.execute(
        """SELECT id, title FROM listings
           WHERE price = ? AND platform != ? AND id != ?
             AND collected_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
           ORDER BY id ASC""",
        (listing.price, listing.platform, listing.id or 0, f"-{RECENT_DAYS} days"),
    ).fetchall()
    for row in rows:
        score = title_similarity(listing.title, row["title"])
        if score >= TITLE_SIMILARITY_THRESHOLD:
            logger.info(
                "중복 매물 감지: listing %s ≒ %s (유사도 %.2f, 동일가)",
                listing.id, row["id"], score,
            )
            return int(row["id"])
    return None
