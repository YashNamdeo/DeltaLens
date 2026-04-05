"""Sample Python file for testing the parser."""

import os  # noqa: F401
from pathlib import Path  # noqa: F401


class UserService:
    """Manages user operations."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def get_user(self, user_id: int) -> dict:
        """Fetch a user by ID."""
        return {"id": user_id, "name": "test"}

    def create_user(self, name: str, email: str) -> dict:
        """Create a new user."""
        return {"name": name, "email": email}

    def _validate_email(self, email: str) -> bool:
        return "@" in email


class AdminService(UserService):
    """Admin operations extending UserService."""

    def delete_user(self, user_id: int) -> bool:
        """Delete a user. Admin only."""
        user = self.get_user(user_id)
        return user is not None


def authenticate(username: str, password: str) -> bool:
    """Top-level authentication function."""
    service = UserService("/tmp/db")
    user = service.get_user(1)
    return user is not None


def main() -> None:
    """Entry point."""
    result = authenticate("admin", "secret")
    print(f"Auth result: {result}")


if __name__ == "__main__":
    main()
