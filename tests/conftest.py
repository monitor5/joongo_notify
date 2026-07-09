import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from joongo_notify.catalog import load_catalog  # noqa: E402
from joongo_notify.config import Config  # noqa: E402
from joongo_notify.db import Database  # noqa: E402


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(ROOT / "data")


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture()
def config(tmp_path):
    cfg = Config()
    cfg.db_path = str(tmp_path / "test.db")
    cfg.data_dir = str(ROOT / "data")
    # 테스트에서 실제 대기 없도록
    cfg.collect.request_delay_min_seconds = 0.0
    cfg.collect.request_delay_max_seconds = 0.0
    return cfg
