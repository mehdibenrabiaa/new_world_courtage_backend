"""One-off CLI to create or update a CRM user account. Not exposed as an API
endpoint on purpose — there is no public signup flow for this internal tool.

Usage (from the backend project root, with the venv active or via its
python.exe directly, and DATABASE_URL set if it's not already in .env):

    python scripts/create_user.py "Jane Doe" jane@newworldcourtage.fr

It will prompt for a password (hidden input). If a user with that email
already exists, its name/password are updated instead of erroring.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password
from app.database import SessionLocal
from app.models import User


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/create_user.py \"Full Name\" email@example.com")
        sys.exit(1)

    name, email = sys.argv[1], sys.argv[2].strip().lower()
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
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.name = name
            user.password_hash = hash_password(password)
            user.active = True
            db.commit()
            print(f"Updated existing user: {email}")
        else:
            user = User(name=name, email=email, password_hash=hash_password(password))
            db.add(user)
            db.commit()
            print(f"Created user: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
