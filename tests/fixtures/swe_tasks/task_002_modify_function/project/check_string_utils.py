"""Tests — includes a test for the missing is_palindrome function."""

from string_utils import count_vowels, is_palindrome, remove_whitespace, reverse, to_lower, to_upper


def test_reverse():
    assert reverse("hello") == "olleh"
    assert reverse("") == ""


def test_count_vowels():
    assert count_vowels("hello") == 2
    assert count_vowels("xyz") == 0


def test_to_upper():
    assert to_upper("hello") == "HELLO"


def test_to_lower():
    assert to_lower("HELLO") == "hello"


def test_remove_whitespace():
    assert remove_whitespace("a b c") == "abc"


def test_is_palindrome():
    """is_palindrome should return True for palindromes."""
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False
    assert is_palindrome("") is True
    assert is_palindrome("A man a plan a canal panama") is True
