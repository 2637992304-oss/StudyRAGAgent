from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import hash_password
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_engine


def main() -> None:
    with Session(get_engine()) as db, db.begin():
        for email in ("student-a@example.test", "student-b@example.test"):
            if not db.scalar(select(User).where(User.email == email)):
                db.add(
                    User(
                        email=email,
                        password_hash=hash_password(get_settings().demo_password),
                    )
                )
    print("Demo accounts: student-a@example.test, student-b@example.test")
    print("Password: value of DEMO_PASSWORD in .env")


if __name__ == "__main__":
    main()
