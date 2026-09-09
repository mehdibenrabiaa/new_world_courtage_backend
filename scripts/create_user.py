"""One-off CLI to create or update a CRM user account. Not exposed as an API
endpoint on purpose — there is no public signup flow for this internal tool.

Usage (from the backend project root, with the venv active or via its
python.exe directly, and DATABASE_URL set if it's not already in .env):

    python scripts/create_user.py "Jane Doe" jane.doe jane@newworldcourtage.fr

It will prompt for a password (hidden input). If a user with that username
already exists, its name/email/password are updated instead of erroring.
"""
import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password
from app.database import SessionLocal
from app.models import User


def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: python scripts/create_user.py \"Full Name\" username email@example.com")
        sys.exit(1)

    name, username, email = sys.argv[1], sys.argv[2].strip().lower(), sys.argv[3].strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]+", username):
        print("Username can only contain lowercase letters, digits, dots, dashes and underscores.")
        sys.exit(1)

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords don't match.")
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.name = name
            user.email = email
            user.password_hash = hash_password(password)
            user.active = True
            db.commit()
            print(f"Updated existing user: {username}")
        else:
            user = User(name=name, username=username, email=email, password_hash=hash_password(password))
            db.add(user)
            db.commit()
            print(f"Created user: {username}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
