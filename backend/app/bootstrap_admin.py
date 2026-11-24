from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.user_account import UserAccount
from app.services.auth_service import hash_password


def main():
    username = settings.BOOTSTRAP_ADMIN_USERNAME.strip()
    password = settings.BOOTSTRAP_ADMIN_PASSWORD.get_secret_value()
    if len(username) < 3 or len(password) < 12:
        raise RuntimeError(
            "Set BOOTSTRAP_ADMIN_USERNAME and a 12-character "
            "BOOTSTRAP_ADMIN_PASSWORD before bootstrapping"
        )

    db = SessionLocal()
    try:
        active_admin = db.query(UserAccount.id).filter(
            UserAccount.role == "admin",
            UserAccount.is_active.is_(True),
        ).first()
        if active_admin:
            raise RuntimeError("An active administrator already exists")

        user = UserAccount(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise RuntimeError(
                "The bootstrap username is already in use"
            ) from error
        print(f"Bootstrapped administrator account: {username}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
