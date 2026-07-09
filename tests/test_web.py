"""FR-A1/A2/A3: 웹 인증과 Watch 관리."""
import pytest
from httpx import ASGITransport, AsyncClient

from joongo_notify.catalog import load_catalog
from joongo_notify.web.app import create_app
from joongo_notify.web.auth import hash_password, verify_password


@pytest.fixture()
def app(config, db):
    config.auth.password_hash = hash_password("test-pw-123", iterations=1000)
    config.auth.session_secret = "test-secret"
    catalog = load_catalog(config.data_dir)
    return create_app(config=config, db=db, catalog=catalog, run_scheduler=False)


@pytest.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
async def logged_in(client):
    resp = await client.post("/login", data={"username": "admin", "password": "test-pw-123"})
    assert resp.status_code == 303
    return client


def test_password_hash_roundtrip():
    stored = hash_password("비밀번호123")
    assert stored.startswith("pbkdf2:")
    assert verify_password("비밀번호123", stored)
    assert not verify_password("wrong", stored)


async def test_requires_login_redirect(client):
    """FR-A1 AC: 미로그인 → 로그인 페이지로."""
    for path in ["/", "/watches/new", "/settings", "/health"]:
        resp = await client.get(path)
        assert resp.status_code == 303, path
        assert resp.headers["location"] == "/login"


async def test_login_wrong_password(client):
    resp = await client.post("/login", data={"username": "admin", "password": "nope"})
    assert resp.status_code == 401


async def test_login_lockout_after_failures(config, db):
    """FR-A1 AC: 실패 5회 → 잠금."""
    config.auth.password_hash = hash_password("pw", iterations=1000)
    catalog = load_catalog(config.data_dir)
    app = create_app(config=config, db=db, catalog=catalog, run_scheduler=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            await client.post("/login", data={"username": "admin", "password": "bad"})
        resp = await client.post("/login", data={"username": "admin", "password": "pw"})
        assert resp.status_code == 429  # 올바른 비밀번호여도 잠금 중엔 거부


async def test_create_watch_flow(logged_in, db):
    """FR-A2: 등록 → 목록 반영, 주기 하한 강제."""
    resp = await logged_in.post(
        "/watches",
        data={
            "product_id": "iphone-14-pro",
            "name": "테스트 감시",
            "price_max": "900000",
            "interval_minutes": "5",  # 최소 15분보다 작게 → 강제 상향
            "cond_burn_in": ["없음"],
            "req_burn_in": "on",
            "cond_full_box": ["풀박스"],
        },
    )
    assert resp.status_code == 303
    watches = db.list_watches()
    assert len(watches) == 1
    w = watches[0]
    assert w.interval_minutes == 15  # FR-C1 AC
    assert any(c.attribute_id == "burn_in" and c.required for c in w.conditions)
    assert any(c.attribute_id == "full_box" and not c.required for c in w.conditions)

    # 대시보드에 표시
    page = await logged_in.get("/")
    assert "테스트 감시" in page.text


async def test_pause_resume_delete(logged_in, db):
    await logged_in.post(
        "/watches", data={"product_id": "iphone-14-pro", "name": "w"}
    )
    watch_id = db.list_watches()[0].id
    await logged_in.post(f"/watches/{watch_id}/pause")
    assert db.get_watch(watch_id).status == "paused"
    await logged_in.post(f"/watches/{watch_id}/resume")
    assert db.get_watch(watch_id).status == "active"
    await logged_in.post(f"/watches/{watch_id}/delete")
    assert db.get_watch(watch_id) is None


async def test_threshold_zero_preserved(logged_in, db):
    """임계 0(모든 매칭 알림)이 기본값으로 대체되지 않는다 (falsy-zero 회귀)."""
    await logged_in.post(
        "/watches",
        data={"product_id": "iphone-14-pro", "name": "z", "threshold": "0"},
    )
    assert db.list_watches()[0].threshold == 0


def test_get_setting_empty_falls_back_to_default(db):
    """빈 설정값이 config 기본값을 가리지 않는다."""
    db.set_setting("telegram_chat_id", "")
    assert db.get_setting("telegram_chat_id", "default-id") == "default-id"
    db.set_setting("telegram_chat_id", "12345")
    assert db.get_setting("telegram_chat_id", "default-id") == "12345"


def test_login_guard_is_per_client():
    """한 클라이언트의 실패가 다른 클라이언트를 잠그지 않는다 (로그인 DoS 방지)."""
    from joongo_notify.web.auth import LoginGuard

    guard = LoginGuard(max_failures=3, lockout_minutes=10)
    for _ in range(3):
        guard.record_failure("attacker")
    assert guard.is_locked("attacker")
    assert not guard.is_locked("owner")


async def test_unknown_product_rejected(logged_in):
    resp = await logged_in.post("/watches", data={"product_id": "no-such"})
    assert resp.status_code == 400


async def test_missing_password_hash_refuses_start(config, db):
    """FR-A1 AC: 평문/미설정 비밀번호로는 기동 불가."""
    config.auth.password_hash = ""
    catalog = load_catalog(config.data_dir)
    with pytest.raises(RuntimeError):
        create_app(config=config, db=db, catalog=catalog, run_scheduler=False)
