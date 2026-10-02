import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: The two roles. An admin manages people; everyone does the work.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


class User(Base):
    """Someone who can sign in to the studio.

    The password is never stored, only a salted PBKDF2 hash of it - see
    ``app.services.auth``. A user is switched off rather than deleted
    when the point is to stop them signing in: deleting is for mistakes.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    #: Stored lower-cased, so "Harsh" and "harsh" cannot be two people.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_USER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuthSession(Base):
    """One signed-in browser or app window.

    The token itself is never stored, only its SHA-256: a copy of the
    database is not a bag of working logins. A row per sign-in rather
    than a signed token so that signing out, switching a user off and
    resetting a password all take effect at once.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
