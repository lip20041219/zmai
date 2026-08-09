"""ZMAI Demo Site 测试 — 初始全部失败，修复后全部通过。"""

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_home_shows_welcome(client):
    rv = client.get("/")
    assert rv.status_code == 200
    assert "Welcome ZMAI Demo" in rv.get_data(as_text=True)


def test_users_api_works(client):
    data = client.get("/api/users").get_json()
    assert "user" in data
    assert data["user"] == "alice"


def test_button_works(client):
    assert client.get("/button").status_code == 200
