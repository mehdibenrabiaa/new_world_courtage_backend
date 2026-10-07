import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@db:5432/nwc",
        "postgresql://u:p@db:5432/nwc",
        "postgresql+psycopg://u:p@db:5432/nwc",
        "postgresql+psycopg2://u:p@db:5432/nwc",
        " postgresql+psycopg://u:p@db:5432/nwc ",
    ],
)
def test_postgres_urls_use_the_installed_driver(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg2://u:p@db:5432/nwc"


def test_sqlite_url_is_untouched():
    assert Settings(database_url="sqlite:///./nwc.db").database_url == "sqlite:///./nwc.db"
