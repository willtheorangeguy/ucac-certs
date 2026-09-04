import pytest

from lss_report.web.db import Database
from lss_report.web.settings import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "test.sqlite3",
        uploads_path=tmp_path / "uploads",
        session_secret="x" * 40,
        base_url="http://testserver",
        managers=("manager@example.org",),
    )


@pytest.fixture
def database(settings) -> Database:
    db = Database(settings.database_path)
    yield db
    db.close()
