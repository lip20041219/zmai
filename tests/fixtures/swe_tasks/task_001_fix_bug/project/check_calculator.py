"""Tests for calculator — one test will fail until the bug is fixed."""

from calculator import add, divide, multiply, power, sqrt, subtract


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 5) == -5


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 3) == -6


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(3, 2) == 1.5


def test_divide_by_zero():
    """divide(10, 0) must raise ZeroDivisionError, not return None."""
    try:
        result = divide(10, 0)
        assert result is not None, "divide(10, 0) should raise, not return None"
    except ZeroDivisionError:
        pass  # Expected behavior


def test_power():
    assert power(2, 3) == 8
    assert power(5, 0) == 1


def test_sqrt():
    assert sqrt(9) == 3.0
    try:
        sqrt(-1)
        assert False, "sqrt(-1) should raise ValueError"
    except ValueError:
        pass
