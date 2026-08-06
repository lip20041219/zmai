"""User manager — has a bug in email validation."""

import re


class UserManager:
    def __init__(self):
        self.users: dict[str, dict] = {}

    def add_user(self, username: str, email: str, age: int) -> dict:
        if username in self.users:
            raise ValueError(f"User '{username}' already exists")
        if age < 0 or age > 150:
            raise ValueError("Age must be between 0 and 150")
        self.users[username] = {
            "username": username,
            "email": email,
            "age": age,
        }
        return self.users[username]

    def get_user(self, username: str) -> dict | None:
        return self.users.get(username)

    def validate_email(self, email: str) -> bool:
        """BUG: regex is wrong, always returns True for non-empty strings."""
        if not email:
            return False
        # BUG: this regex doesn't actually check email format
        pattern = r".+@.+"  # Too permissive, allows invalid emails
        return bool(re.match(pattern, email))

    def list_users(self) -> list[dict]:
        return list(self.users.values())

    def remove_user(self, username: str) -> bool:
        if username in self.users:
            del self.users[username]
            return True
        return False
