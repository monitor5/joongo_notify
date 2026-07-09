"""CLI 진입점: serve(웹+스케줄러) / once(1회 수집) / hash-password / stats."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

from .catalog import load_catalog
from .config import load_config
from .db import Database


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="joongo-notify")
    parser.add_argument("--config", default=None, help="config.yaml 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="웹 UI + 수집 스케줄러 실행")
    once = sub.add_parser("once", help="활성 Watch 즉시 1회 수집 (스케줄러 없이)")
    once.add_argument("--watch-id", type=int, default=None)
    sub.add_parser("hash-password", help="로그인 비밀번호 해시 생성 (FR-A1)")
    sub.add_parser("stats", help="수집/분석/알림 통계 출력 (FR-D5)")

    args = parser.parse_args(argv)

    if args.command == "hash-password":
        from .web.auth import hash_password

        password = getpass.getpass("비밀번호: ")
        if password != getpass.getpass("비밀번호 확인: "):
            print("비밀번호가 일치하지 않습니다", file=sys.stderr)
            return 1
        print(hash_password(password))
        return 0

    config = load_config(args.config)

    if args.command == "serve":
        import uvicorn

        from .web.app import create_app

        app = create_app(config)
        uvicorn.run(app, host=config.web.host, port=config.web.port)
        return 0

    if args.command == "once":
        from .scheduler import run_once

        db = Database(config.db_path)
        catalog = load_catalog(config.data_dir)
        asyncio.run(run_once(config, db, catalog, watch_id=args.watch_id))
        return 0

    if args.command == "stats":
        db = Database(config.db_path)
        for key, value in db.stats().items():
            print(f"{key}: {value}")
        for h in db.adapter_health():
            print(f"adapter[{h['platform']}]: 연속실패 {h['consecutive_failures']}, 마지막 성공 {h['last_ok_at']}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
