"""웹 UI (FR-A1~A3, FR-D4 /health) + 백그라운드 스케줄러 기동."""
from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..catalog import Catalog, load_catalog
from ..config import Config, load_config
from ..db import Database
from ..models import Watch, WatchCondition
from ..scheduler import build_notifier, scheduler_loop
from .auth import LoginGuard, SessionManager, verify_password

logger = logging.getLogger("joongo_notify")

TEMPLATES_DIR = Path(__file__).parent / "templates"
SESSION_COOKIE = "joongo_session"


def build_price_chart(series: list[dict]) -> dict | None:
    """가격 시계열 → 인라인 SVG용 좌표 데이터 (외부 의존 없음).

    산점도(각 매물) + 일자별 중앙값 추세선. 값이 2개 미만이면 None.
    """
    points = [(s["day"], s["price"]) for s in series if s.get("price")]
    if len(points) < 2:
        return None
    prices = [p for _, p in points]
    days = sorted({d for d, _ in points})
    lo, hi = min(prices), max(prices)
    span = hi - lo or 1
    W, H, PAD = 640, 260, 40
    day_x = {d: PAD + (W - 2 * PAD) * (i / max(len(days) - 1, 1)) for i, d in enumerate(days)}

    def y_of(price: int) -> float:
        return H - PAD - (H - 2 * PAD) * (price - lo) / span

    dots = [{"x": round(day_x[d], 1), "y": round(y_of(p), 1), "price": p, "day": d}
            for d, p in points]
    # 일자별 중앙값 추세선
    median_pts = []
    for d in days:
        vals = sorted(p for dd, p in points if dd == d)
        med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals)//2-1]+vals[len(vals)//2])//2
        median_pts.append((round(day_x[d], 1), round(y_of(med), 1)))
    trend = " ".join(f"{x},{y}" for x, y in median_pts)
    return {
        "width": W, "height": H, "dots": dots, "trend": trend,
        "lo": lo, "hi": hi, "y_lo": round(y_of(lo), 1), "y_hi": round(y_of(hi), 1),
        "pad": PAD,
    }


class NotAuthenticated(Exception):
    """미인증 접근 — 전역 핸들러가 /login으로 리다이렉트한다.

    require_login이 응답 객체를 반환하는 대신 예외를 던지므로,
    보호 라우트에서 가드 체크를 잊는 것이 구조적으로 불가능하다.
    """


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(
    config: Config | None = None,
    db: Database | None = None,
    catalog: Catalog | None = None,
    run_scheduler: bool = True,
) -> FastAPI:
    config = config or load_config()
    db = db or Database(config.db_path)
    catalog = catalog or load_catalog(config.data_dir)

    if not config.auth.password_hash:
        raise RuntimeError(
            "auth.password_hash 미설정 — `joongo-notify hash-password`로 생성해 "
            "config.yaml 또는 JOONGO_AUTH_PASSWORD_HASH에 넣으세요 (평문 저장 금지, FR-A1)"
        )
    session_secret = config.auth.session_secret or secrets.token_hex(32)
    sessions = SessionManager(session_secret)
    guard = LoginGuard(config.auth.max_login_failures, config.auth.lockout_minutes)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(scheduler_loop(config, db, catalog)) if run_scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="joongo_notify", lifespan=lifespan)
    app.state.db = db
    app.state.catalog = catalog
    app.state.config = config

    # ---- 인증 ----------------------------------------------------------
    def require_login(request: Request) -> str:
        user = sessions.verify(request.cookies.get(SESSION_COOKIE))
        if not user:
            raise NotAuthenticated()
        return user

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": None, "locked": guard.is_locked(_client_key(request))},
        )

    @app.post("/login")
    async def login(request: Request, username: str = Form(...), password: str = Form(...)):
        key = _client_key(request)
        if guard.is_locked(key):
            return templates.TemplateResponse(
                request, "login.html", {"error": "잠시 후 다시 시도하세요 (잠금)", "locked": True},
                status_code=429,
            )
        if username == config.auth.username and verify_password(
            password, config.auth.password_hash
        ):
            guard.record_success(key)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, sessions.issue(username),
                httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14,
            )
            return response
        guard.record_failure(key)
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "아이디 또는 비밀번호가 올바르지 않습니다", "locked": guard.is_locked(key)},
            status_code=401,
        )

    @app.post("/logout")
    async def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    # ---- 화면 ----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, user: str = Depends(require_login)):
        return templates.TemplateResponse(
            request, "index.html",
            {
                "watches": db.list_watches(),
                "matches": db.recent_matches(20),
                "products": catalog.products,
                "stats": db.stats(),
            },
        )

    @app.get("/watches/new", response_class=HTMLResponse)
    async def watch_form(request: Request, user: str = Depends(require_login)):
        return templates.TemplateResponse(
            request, "watch_form.html",
            {
                "products": list(catalog.products.values()),
                "categories": catalog.categories,
                "default_interval": config.collect.default_interval_minutes,
                "min_interval": config.collect.min_interval_minutes,
                "default_threshold": config.scoring.default_threshold,
            },
        )

    def _watch_from_form(form, existing: Watch | None) -> Watch | None:
        """폼 → Watch (등록/수정 공용). 알 수 없는 제품이면 None."""
        product_id = str(form.get("product_id", ""))
        product = catalog.products.get(product_id)
        category = catalog.category_of(product_id)
        if not product or not category:
            return None

        conditions = []
        for attr in category.attributes:
            accepted = [str(v) for v in form.getlist(f"cond_{attr.id}")]
            numeric_min = str(form.get(f"cond_{attr.id}_min", "")).strip()
            if numeric_min:
                accepted = [f">={numeric_min}"]
            if not accepted:
                continue
            conditions.append(
                WatchCondition(
                    attribute_id=attr.id,
                    accepted_values=accepted,
                    required=form.get(f"req_{attr.id}") == "on",
                )
            )

        def _int_or_default(key: str, default: int | None) -> int | None:
            """0을 유효값으로 보존 (`or default`의 falsy-zero 함정 회피)."""
            value = str(form.get(key, "")).strip()
            return int(value) if value.lstrip("-").isdigit() else default

        interval = _int_or_default("interval_minutes", config.collect.default_interval_minutes)
        interval = max(interval, config.collect.min_interval_minutes)  # FR-C1 AC
        threshold = _int_or_default("threshold", config.scoring.default_threshold)

        chat_mode = str(form.get("auto_chat_mode", "off"))
        if chat_mode not in ("off", "approve", "auto"):
            chat_mode = "off"

        return Watch(
            id=existing.id if existing else None,
            product_id=product_id,
            name=str(form.get("name") or product.name),
            price_min=_int_or_default("price_min", None),
            price_max=_int_or_default("price_max", None),
            region=str(form.get("region", "")).strip() or None,
            interval_minutes=interval,
            threshold=threshold,
            conditions=conditions,
            status=existing.status if existing else "active",
            last_run_at=existing.last_run_at if existing else None,
            created_at=existing.created_at if existing else Watch.__dataclass_fields__["created_at"].default_factory(),
            auto_chat_mode=chat_mode,
            auto_chat_threshold=_int_or_default("auto_chat_threshold", None),
            auto_chat_price=_int_or_default("auto_chat_price", None),
        )

    @app.post("/watches")
    async def create_watch(request: Request, user: str = Depends(require_login)):
        watch = _watch_from_form(await request.form(), existing=None)
        if watch is None:
            return JSONResponse({"error": "unknown product"}, status_code=400)
        db.insert_watch(watch)
        return RedirectResponse("/", status_code=303)

    @app.get("/watches/{watch_id}/edit", response_class=HTMLResponse)
    async def edit_watch_form(watch_id: int, request: Request, user: str = Depends(require_login)):
        watch = db.get_watch(watch_id)
        if watch is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request, "watch_form.html",
            {
                "products": list(catalog.products.values()),
                "categories": catalog.categories,
                "default_interval": config.collect.default_interval_minutes,
                "min_interval": config.collect.min_interval_minutes,
                "default_threshold": config.scoring.default_threshold,
                "watch": watch,
                "selected_conditions": {c.attribute_id: c for c in watch.conditions},
            },
        )

    @app.post("/watches/{watch_id}/edit")
    async def edit_watch(watch_id: int, request: Request, user: str = Depends(require_login)):
        existing = db.get_watch(watch_id)
        if existing is None:
            return RedirectResponse("/", status_code=303)
        watch = _watch_from_form(await request.form(), existing=existing)
        if watch is None:
            return JSONResponse({"error": "unknown product"}, status_code=400)
        db.update_watch(watch)
        return RedirectResponse("/", status_code=303)

    @app.post("/watches/{watch_id}/pause")
    async def pause_watch(watch_id: int, user: str = Depends(require_login)):
        db.set_watch_status(watch_id, "paused")
        return RedirectResponse("/", status_code=303)

    @app.post("/watches/{watch_id}/resume")
    async def resume_watch(watch_id: int, user: str = Depends(require_login)):
        db.set_watch_status(watch_id, "active")
        return RedirectResponse("/", status_code=303)

    @app.post("/watches/{watch_id}/delete")
    async def delete_watch(watch_id: int, user: str = Depends(require_login)):
        db.delete_watch(watch_id)
        return RedirectResponse("/", status_code=303)

    # ---- 설정 (FR-A1b: 텔레그램 채널 연결) --------------------------------
    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, user: str = Depends(require_login)):
        return templates.TemplateResponse(
            request, "settings.html",
            {
                "chat_id": db.get_setting("telegram_chat_id", config.telegram.chat_id),
                "telegram_enabled": config.telegram.enabled and bool(config.telegram.token),
                "message": request.query_params.get("message"),
            },
        )

    @app.post("/settings/telegram")
    async def save_telegram(user: str = Depends(require_login), chat_id: str = Form("")):
        db.set_setting("telegram_chat_id", chat_id.strip())
        message = "저장되었습니다"
        if config.telegram.enabled and config.telegram.token and chat_id.strip():
            notifier = build_notifier(config, db)
            try:
                await notifier.send_operator_alert("✅ joongo_notify 알림 연결 테스트")
                message = "저장 및 테스트 알림 발송 완료"  # FR-A1b AC
            except Exception as exc:
                message = f"저장됨 — 테스트 발송 실패: {exc}"
        return RedirectResponse(f"/settings?message={quote(message)}", status_code=303)

    # ---- 매물 이력 (웹 UI 고도화) ----------------------------------------
    @app.get("/listings", response_class=HTMLResponse)
    async def listings_page(request: Request, user: str = Depends(require_login)):
        platform = request.query_params.get("platform") or None
        return templates.TemplateResponse(
            request, "listings.html",
            {
                "listings": db.recent_listings(platform=platform, limit=100),
                "platform": platform,
                "platforms": config.collect.platforms,
            },
        )

    # ---- 매칭 상세 -------------------------------------------------------
    @app.get("/matches/{match_id}", response_class=HTMLResponse)
    async def match_detail(match_id: int, request: Request, user: str = Depends(require_login)):
        match = db.get_match(match_id)
        if match is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "match_detail.html", {"m": match})

    @app.post("/matches/{match_id}/feedback")
    async def match_feedback(match_id: int, request: Request, user: str = Depends(require_login),
                             feedback: str = Form(...)):
        if feedback in ("good", "bad", "ignore"):
            db.set_match_feedback(match_id, feedback)
        referer = request.headers.get("referer", "/")
        return RedirectResponse(referer, status_code=303)

    # ---- 시세 그래프 -----------------------------------------------------
    @app.get("/prices", response_class=HTMLResponse)
    async def prices_page(request: Request, user: str = Depends(require_login)):
        product_id = request.query_params.get("product") or next(iter(catalog.products), None)
        series = db.price_series(product_id) if product_id else []
        return templates.TemplateResponse(
            request, "prices.html",
            {
                "products": list(catalog.products.values()),
                "product_id": product_id,
                "chart": build_price_chart(series),
                "point_count": len(series),
            },
        )

    # ---- 자동 채팅 큐 (FR-D6) --------------------------------------------
    @app.get("/chats", response_class=HTMLResponse)
    async def chats_page(request: Request, user: str = Depends(require_login)):
        return templates.TemplateResponse(
            request, "chats.html",
            {
                "chats": db.recent_chats(50),
                "autochat_enabled": config.autochat.enabled,
                "dry_run": config.autochat.dry_run,
            },
        )

    @app.post("/chats/{chat_id}/approve")
    async def approve_chat(chat_id: int, user: str = Depends(require_login)):
        chat = db.get_chat(chat_id)
        if chat and chat.status == "pending":
            db.set_chat_status(chat_id, "queued")  # 스케줄러가 상한 내 발송
        return RedirectResponse("/chats", status_code=303)

    @app.post("/chats/{chat_id}/cancel")
    async def cancel_chat(chat_id: int, user: str = Depends(require_login)):
        chat = db.get_chat(chat_id)
        if chat and chat.status in ("pending", "queued", "failed"):
            db.set_chat_status(chat_id, "cancelled")
        return RedirectResponse("/chats", status_code=303)

    # ---- 상태 (FR-D4) ---------------------------------------------------
    @app.get("/health")
    async def health(user: str = Depends(require_login)):
        return {"adapters": db.adapter_health(), "stats": db.stats()}

    return app
