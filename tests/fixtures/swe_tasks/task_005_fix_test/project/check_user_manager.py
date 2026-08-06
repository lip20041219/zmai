"""Tests for UserManager — email validation test will fail."""

import pytest
from user_manager import UserManager


@pytest.fixture
def mgr():
    return UserManager()


def test_add_and_get_user(mgr):
    user = mgr.add_user("alice", "alice@example.com", 30)
    assert user["username"] == "alice"
    assert mgr.get_user("alice") == user


def test_duplicate_user_raises(mgr):
    mgr.add_user("bob", "bob@test.com", 25)
    with pytest.raises(ValueError):
        mgr.add_user("bob", "bob@test.com", 25)


def test_valid_email(mgr):
    assert mgr.validate_email("user@example.com") is True
    assert mgr.validate_email("a.b@domain.co.uk") is True


def test_invalid_email(mgr):
    """This test will fail until validate_email is fixed."""
    assert mgr.validate_email("not-an-email") is False
    assert mgr.validate_email("") is False
    assert mgr.validate_email("@") is False


def test_remove_user(mgr):
    mgr.add_user("charlie", "charlie@test.com", 35)
    assert mgr.remove_user("charlie") is True
    assert mgr.get_user("charlie") is None


def test_age_validation(mgr):
    with pytest.raises(ValueError):
        mgr.add_user("bad", "bad@test.com", -1)
    with pytest.raises(ValueError):
        mgr.add_user("old", "old@test.com", 200)
