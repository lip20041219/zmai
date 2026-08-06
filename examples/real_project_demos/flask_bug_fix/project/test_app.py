"""Tests for Flask TODO App — edit test will fail until the bug is fixed."""

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index(client):
    rv = client.get("/")
    assert rv.status_code == 200


def test_add_todo(client):
    rv = client.post("/add", data={"title": "Test Task"}, follow_redirects=True)
    assert rv.status_code == 200
    assert b"Test Task" in rv.data


def test_edit_page_loads(client):
    client.post("/add", data={"title": "Edit Me"})
    rv = client.get("/edit/1")
    assert rv.status_code == 200
    assert b"Edit Me" in rv.data


def test_edit_submit(client):
    """This test FAILS until the edit route handles POST."""
    client.post("/add", data={"title": "Old Title"})
    # Submit the edit form (POST)
    rv = client.post("/edit/1", data={"title": "New Title"}, follow_redirects=True)
    assert rv.status_code == 200, f"Edit POST failed: {rv.status_code}"
    # Verify the title was updated
    rv = client.get("/")
    assert b"New Title" in rv.data


def test_delete_todo(client):
    client.post("/add", data={"title": "Delete Me"})
    rv = client.get("/delete/1", follow_redirects=True)
    assert rv.status_code == 200


def test_nonexistent_todo(client):
    rv = client.get("/edit/999")
    assert rv.status_code == 404
