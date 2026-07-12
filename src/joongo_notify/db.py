"""SQLite 저장 계층 (Phase 1은 SQLite — 계획서 §4)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import (
    AnalysisReport,
    AttributeFinding,
    ChatMessage,
    ConditionVerdict,
    Listing,
    MatchResult,
    Watch,
    WatchCondition,
    utcnow_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price_min INTEGER,
    price_max INTEGER,
    region TEXT,
    interval_minutes INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    conditions_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_run_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    platform_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price INTEGER,
    region TEXT,
    images_json TEXT NOT NULL DEFAULT '[]',
    posted_at TEXT,
    collected_at TEXT NOT NULL,
    detail_fetched INTEGER NOT NULL DEFAULT 0,
    dup_of INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (platform, platform_id)
);
CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    category_id TEXT NOT NULL,
    extractor TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (listing_id, category_id, extractor)
);
CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    score INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    verdicts_json TEXT NOT NULL,
    notified_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (watch_id, listing_id)
);
CREATE TABLE IF NOT EXISTS adapter_health (
    platform TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_ok_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id),
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    platform TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE (watch_id, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_price_collected
    ON listings (price, collected_at);
"""

# 구버전 DB에 추가된 컬럼 (컬럼명 → ALTER 구문). __init__에서 자동 적용
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "watches": [
        ("auto_chat_mode", "ALTER TABLE watches ADD COLUMN auto_chat_mode TEXT NOT NULL DEFAULT 'off'"),
        ("auto_chat_threshold", "ALTER TABLE watches ADD COLUMN auto_chat_threshold INTEGER"),
        ("auto_chat_price", "ALTER TABLE watches ADD COLUMN auto_chat_price INTEGER"),
    ],
    "match_results": [
        ("feedback", "ALTER TABLE match_results ADD COLUMN feedback TEXT"),
    ],
}


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, columns in _MIGRATIONS.items():
            existing = {
                r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in columns:
                if column not in existing:
                    self.conn.execute(ddl)

    def close(self) -> None:
        self.conn.close()

    # ---- watches -------------------------------------------------------
    def insert_watch(self, watch: Watch) -> int:
        cur = self.conn.execute(
            """INSERT INTO watches (product_id, name, price_min, price_max, region,
                   interval_minutes, threshold, conditions_json, status, last_run_at,
                   created_at, auto_chat_mode, auto_chat_threshold, auto_chat_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                watch.product_id,
                watch.name,
                watch.price_min,
                watch.price_max,
                watch.region,
                watch.interval_minutes,
                watch.threshold,
                json.dumps([c.__dict__ for c in watch.conditions], ensure_ascii=False),
                watch.status,
                watch.last_run_at,
                watch.created_at,
                watch.auto_chat_mode,
                watch.auto_chat_threshold,
                watch.auto_chat_price,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_watch(self, watch: Watch) -> None:
        """Watch 수정 (FR-A3). id 기준 전체 필드 갱신."""
        self.conn.execute(
            """UPDATE watches SET product_id=?, name=?, price_min=?, price_max=?,
                   region=?, interval_minutes=?, threshold=?, conditions_json=?,
                   auto_chat_mode=?, auto_chat_threshold=?, auto_chat_price=?
               WHERE id=?""",
            (
                watch.product_id,
                watch.name,
                watch.price_min,
                watch.price_max,
                watch.region,
                watch.interval_minutes,
                watch.threshold,
                json.dumps([c.__dict__ for c in watch.conditions], ensure_ascii=False),
                watch.auto_chat_mode,
                watch.auto_chat_threshold,
                watch.auto_chat_price,
                watch.id,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_watch(row: sqlite3.Row) -> Watch:
        return Watch(
            id=row["id"],
            product_id=row["product_id"],
            name=row["name"],
            price_min=row["price_min"],
            price_max=row["price_max"],
            region=row["region"],
            interval_minutes=row["interval_minutes"],
            threshold=row["threshold"],
            conditions=[WatchCondition(**c) for c in json.loads(row["conditions_json"])],
            status=row["status"],
            last_run_at=row["last_run_at"],
            created_at=row["created_at"],
            auto_chat_mode=row["auto_chat_mode"],
            auto_chat_threshold=row["auto_chat_threshold"],
            auto_chat_price=row["auto_chat_price"],
        )

    def get_watch(self, watch_id: int) -> Watch | None:
        row = self.conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        return self._row_to_watch(row) if row else None

    def list_watches(self, status: str | None = None) -> list[Watch]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM watches WHERE status=? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM watches ORDER BY id").fetchall()
        return [self._row_to_watch(r) for r in rows]

    def set_watch_status(self, watch_id: int, status: str) -> None:
        self.conn.execute("UPDATE watches SET status=? WHERE id=?", (status, watch_id))
        self.conn.commit()

    def delete_watch(self, watch_id: int) -> None:
        self.conn.execute("DELETE FROM match_results WHERE watch_id=?", (watch_id,))
        self.conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        self.conn.commit()

    def touch_watch_run(self, watch_id: int) -> None:
        self.conn.execute(
            "UPDATE watches SET last_run_at=? WHERE id=?", (utcnow_iso(), watch_id)
        )
        self.conn.commit()

    # ---- listings ------------------------------------------------------
    def upsert_listing(self, listing: Listing) -> tuple[int, bool]:
        """저장하고 (id, 신규 여부) 반환. 기존 매물이면 재분석하지 않도록 신규=False (FR-C1)."""
        row = self.conn.execute(
            "SELECT id FROM listings WHERE platform=? AND platform_id=?",
            (listing.platform, listing.platform_id),
        ).fetchone()
        if row:
            return int(row["id"]), False
        cur = self.conn.execute(
            """INSERT INTO listings (platform, platform_id, url, title, description, price,
                   region, images_json, posted_at, collected_at, detail_fetched, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                listing.platform,
                listing.platform_id,
                listing.url,
                listing.title,
                listing.description,
                listing.price,
                listing.region,
                json.dumps(listing.images, ensure_ascii=False),
                listing.posted_at,
                listing.collected_at,
                int(listing.detail_fetched),
                json.dumps(listing.raw, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid), True

    def update_listing_detail(
        self, listing_id: int, description: str, price: int | None, images: list[str]
    ) -> None:
        """상세 조회 성공분 반영. detail_fetched=1이 되어 이후 재조회하지 않는다."""
        self.conn.execute(
            """UPDATE listings SET description=?, price=COALESCE(?, price),
                   images_json=?, detail_fetched=1 WHERE id=?""",
            (description, price, json.dumps(images, ensure_ascii=False), listing_id),
        )
        self.conn.commit()

    def get_listing(self, listing_id: int) -> Listing | None:
        row = self.conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return None
        return Listing(
            id=row["id"],
            platform=row["platform"],
            platform_id=row["platform_id"],
            url=row["url"],
            title=row["title"],
            description=row["description"],
            price=row["price"],
            region=row["region"],
            images=json.loads(row["images_json"]),
            posted_at=row["posted_at"],
            collected_at=row["collected_at"],
            detail_fetched=bool(row["detail_fetched"]),
            dup_of=row["dup_of"],
            raw=json.loads(row["raw_json"]),
        )

    def set_dup_of(self, listing_id: int, dup_of: int) -> None:
        self.conn.execute("UPDATE listings SET dup_of=? WHERE id=?", (dup_of, listing_id))
        self.conn.commit()

    def match_notified(self, watch_id: int, listing_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM match_results WHERE watch_id=? AND listing_id=? "
            "AND notified_at IS NOT NULL",
            (watch_id, listing_id),
        ).fetchone()
        return row is not None

    def group_notified(
        self, watch_id: int, listing_id: int, dup_of: int | None
    ) -> bool:
        """같은 중복 그룹(원본·형제·자기 자신 제외)의 매물이 이 Watch로 알림됐는가 (FR-C4).

        dup_of는 항상 더 오래된 매물을 가리키므로 그룹 = {원본} ∪ {원본을 가리키는 것들}.
        원본이 늦게 처리되는 순서(상세 지연 등)에서도 양방향으로 억제된다.
        """
        root = dup_of or listing_id
        row = self.conn.execute(
            """SELECT 1 FROM match_results m
               JOIN listings l ON l.id = m.listing_id
               WHERE m.watch_id = ? AND m.notified_at IS NOT NULL
                 AND m.listing_id != ?
                 AND (l.id = ? OR l.dup_of = ?)
               LIMIT 1""",
            (watch_id, listing_id, root, root),
        ).fetchone()
        return row is not None

    # ---- analysis ------------------------------------------------------
    def save_report(self, report: AnalysisReport) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO analysis_reports
                   (listing_id, category_id, extractor, findings_json, created_at)
               VALUES (?,?,?,?,?)""",
            (
                report.listing_id,
                report.category_id,
                report.extractor,
                json.dumps([f.__dict__ for f in report.findings], ensure_ascii=False),
                report.created_at,
            ),
        )
        self.conn.commit()

    def get_report(self, listing_id: int, category_id: str) -> AnalysisReport | None:
        row = self.conn.execute(
            """SELECT * FROM analysis_reports WHERE listing_id=? AND category_id=?
               ORDER BY id DESC LIMIT 1""",
            (listing_id, category_id),
        ).fetchone()
        if not row:
            return None
        return AnalysisReport(
            listing_id=row["listing_id"],
            category_id=row["category_id"],
            extractor=row["extractor"],
            findings=[AttributeFinding(**f) for f in json.loads(row["findings_json"])],
            created_at=row["created_at"],
        )

    # ---- matches -------------------------------------------------------
    def save_match(self, match: MatchResult) -> bool:
        """저장하고 신규 여부 반환. 이미 있으면 저장하지 않음 (FR-D1 중복 알림 방지)."""
        exists = self.conn.execute(
            "SELECT 1 FROM match_results WHERE watch_id=? AND listing_id=?",
            (match.watch_id, match.listing_id),
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            """INSERT INTO match_results
                   (watch_id, listing_id, score, passed, verdicts_json, notified_at, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                match.watch_id,
                match.listing_id,
                match.score,
                int(match.passed),
                json.dumps([v.__dict__ for v in match.verdicts], ensure_ascii=False),
                match.notified_at,
                match.created_at,
            ),
        )
        self.conn.commit()
        return True

    def unnotified_matches(self, watch_id: int) -> list[MatchResult]:
        """통과했지만 발송되지 않은 매칭 (발송 실패 재시도용 — FR-D1)."""
        rows = self.conn.execute(
            """SELECT * FROM match_results
               WHERE watch_id=? AND passed=1 AND notified_at IS NULL""",
            (watch_id,),
        ).fetchall()
        return [
            MatchResult(
                watch_id=r["watch_id"],
                listing_id=r["listing_id"],
                score=r["score"],
                passed=bool(r["passed"]),
                verdicts=[ConditionVerdict(**v) for v in json.loads(r["verdicts_json"])],
                created_at=r["created_at"],
                notified_at=r["notified_at"],
            )
            for r in rows
        ]

    def mark_notified(self, watch_id: int, listing_id: int) -> None:
        self.conn.execute(
            "UPDATE match_results SET notified_at=? WHERE watch_id=? AND listing_id=?",
            (utcnow_iso(), watch_id, listing_id),
        )
        self.conn.commit()

    def recent_matches(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT m.*, l.title, l.url, l.price, l.region, l.platform, w.name AS watch_name
               FROM match_results m
               JOIN listings l ON l.id = m.listing_id
               JOIN watches w ON w.id = m.watch_id
               ORDER BY m.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["verdicts"] = [ConditionVerdict(**v) for v in json.loads(r["verdicts_json"])]
            result.append(d)
        return result

    def set_match_feedback(self, match_id: int, feedback: str) -> None:
        """알림 피드백 기록 (FR-D2): good | bad | ignore."""
        self.conn.execute(
            "UPDATE match_results SET feedback=? WHERE id=?", (feedback, match_id)
        )
        self.conn.commit()

    def get_match(self, match_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT m.*, l.title, l.url, l.price, l.region, l.platform,
                      l.description, l.images_json, w.name AS watch_name
               FROM match_results m
               JOIN listings l ON l.id = m.listing_id
               JOIN watches w ON w.id = m.watch_id
               WHERE m.id=?""",
            (match_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["verdicts"] = [ConditionVerdict(**v) for v in json.loads(row["verdicts_json"])]
        d["images"] = json.loads(row["images_json"])
        return d

    def recent_listings(self, platform: str | None = None, limit: int = 100) -> list[dict]:
        """매물 이력 (웹 UI)."""
        if platform:
            rows = self.conn.execute(
                "SELECT * FROM listings WHERE platform=? ORDER BY id DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM listings ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def price_series(self, product_id: str) -> list[dict]:
        """제품별 시세 시계열 — 별칭 매칭된(=매칭 판정이 있는) 매물의 (일자, 가격).

        판정 통과 여부와 무관하게 제품이 맞다고 판정된 매물 전체를 사용.
        """
        rows = self.conn.execute(
            """SELECT DISTINCT l.id, substr(l.collected_at, 1, 10) AS day, l.price
               FROM listings l
               JOIN match_results m ON m.listing_id = l.id
               JOIN watches w ON w.id = m.watch_id
               WHERE w.product_id = ? AND l.price IS NOT NULL AND l.price > 0
               ORDER BY l.collected_at""",
            (product_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- chat queue (FR-D6) ---------------------------------------------
    def enqueue_chat(self, chat: ChatMessage) -> int | None:
        """문의 저장. 같은 (watch, listing) 조합이 이미 있으면 None (중복 문의 방지)."""
        exists = self.conn.execute(
            "SELECT 1 FROM chat_messages WHERE watch_id=? AND listing_id=?",
            (chat.watch_id, chat.listing_id),
        ).fetchone()
        if exists:
            return None
        cur = self.conn.execute(
            """INSERT INTO chat_messages
                   (watch_id, listing_id, platform, listing_url, message, status,
                    error, created_at, sent_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                chat.watch_id, chat.listing_id, chat.platform, chat.listing_url,
                chat.message, chat.status, chat.error, chat.created_at, chat.sent_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _row_to_chat(row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            id=row["id"], watch_id=row["watch_id"], listing_id=row["listing_id"],
            platform=row["platform"], listing_url=row["listing_url"],
            message=row["message"], status=row["status"], error=row["error"],
            created_at=row["created_at"], sent_at=row["sent_at"],
        )

    def get_chat(self, chat_id: int) -> ChatMessage | None:
        row = self.conn.execute(
            "SELECT * FROM chat_messages WHERE id=?", (chat_id,)
        ).fetchone()
        return self._row_to_chat(row) if row else None

    def chats_by_status(self, status: str, limit: int = 50) -> list[ChatMessage]:
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE status=? ORDER BY id LIMIT ?",
            (status, limit),
        ).fetchall()
        return [self._row_to_chat(r) for r in rows]

    def recent_chats(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.*, l.title, w.name AS watch_name
               FROM chat_messages c
               JOIN listings l ON l.id = c.listing_id
               JOIN watches w ON w.id = c.watch_id
               ORDER BY c.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_chat_status(
        self, chat_id: int, status: str, error: str = "", sent: bool = False
    ) -> None:
        self.conn.execute(
            "UPDATE chat_messages SET status=?, error=?, sent_at=? WHERE id=?",
            (status, error, utcnow_iso() if sent else None, chat_id),
        )
        self.conn.commit()

    def chats_sent_since(self, since_iso: str) -> int:
        """발송 상한 집계 (FR-D6 AC). 실패·취소는 제외, 실제 발송분만."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE status='sent' AND sent_at >= ?",
            (since_iso,),
        ).fetchone()[0]

    # ---- adapter health (FR-D4) ----------------------------------------
    def record_adapter_result(self, platform: str, ok: bool, error: str = "") -> int:
        """결과를 기록하고 현재 연속 실패 횟수를 반환."""
        row = self.conn.execute(
            "SELECT consecutive_failures FROM adapter_health WHERE platform=?", (platform,)
        ).fetchone()
        failures = 0 if ok else (row["consecutive_failures"] + 1 if row else 1)
        self.conn.execute(
            """INSERT INTO adapter_health (platform, consecutive_failures, last_ok_at, last_error)
               VALUES (?,?,?,?)
               ON CONFLICT(platform) DO UPDATE SET
                   consecutive_failures=excluded.consecutive_failures,
                   last_ok_at=CASE WHEN excluded.last_ok_at IS NOT NULL
                       THEN excluded.last_ok_at ELSE adapter_health.last_ok_at END,
                   last_error=excluded.last_error""",
            (platform, failures, utcnow_iso() if ok else None, error),
        )
        self.conn.commit()
        return failures

    def adapter_health(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM adapter_health").fetchall()]

    # ---- settings ------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        # 빈 값은 미설정으로 취급 — 빈 값이 config/env 기본값을 영구히 가리는 사고 방지
        return row["value"] if row and row["value"] else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---- stats (FR-D5 일부) ---------------------------------------------
    def stats(self) -> dict:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "watches": q("SELECT COUNT(*) FROM watches"),
            "listings": q("SELECT COUNT(*) FROM listings"),
            "reports": q("SELECT COUNT(*) FROM analysis_reports"),
            "matches": q("SELECT COUNT(*) FROM match_results"),
            "notified": q("SELECT COUNT(*) FROM match_results WHERE notified_at IS NOT NULL"),
        }
