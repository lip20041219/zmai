"""String utilities — needs a new feature added."""


def reverse(text: str) -> str:
    """Return the reversed string."""
    return text[::-1]


def count_vowels(text: str) -> int:
    """Count vowels (a, e, i, o, u) in text."""
    vowels = "aeiouAEIOU"
    return sum(1 for c in text if c in vowels)


def to_upper(text: str) -> str:
    """Convert text to uppercase."""
    return text.upper()


def to_lower(text: str) -> str:
    """Convert text to lowercase."""
    return text.lower()


def remove_whitespace(text: str) -> str:
    """Remove all whitespace from text."""
    return "".join(text.split())
