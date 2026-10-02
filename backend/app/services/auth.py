"""Users, passwords and sign-in sessions.

Standard library only: PBKDF2-SHA256 for passwords and opaque random
tokens for sessions, so signing in adds no dependency to a backend that
is also shipped as a frozen executable.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models.user import ROLE_ADMIN, ROLES, AuthSession, User

PBKDF2_ITERATIONS = 390_000
MIN_PASSWORD_LENGTH = 8
MAX_USERNAME_LENGTH = 64


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ValidationError(AppError):
    status_code = 422
    code = "invalid"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive(moment: datetime) -> datetime:
    """SQLite hands datetimes back without a zone; compare like with like."""
    return moment.replace(tzinfo=None) if moment.tzinfo else moment


# --- passwords -----------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"A password needs at least {MIN_PASSWORD_LENGTH} characters.")


def _normalise_username(username: str) -> str:
    name = username.strip().lower()
    if not name:
        raise ValidationError("A username is required.")
    if len(name) > MAX_USERNAME_LENGTH:
        raise ValidationError(f"A username can be at most {MAX_USERNAME_LENGTH} characters.")
    if any(ch.isspace() for ch in name):
        raise ValidationError("A username cannot contain spaces.")
    return name


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValidationError(f"Role must be one of: {', '.join(ROLES)}.")


# --- users ---------------------------------------------------------------------


def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def _active_admins(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True))
    ) or 0


def get_user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User not found: {user_id}")
    return user


def create_user(db: Session, username: str, password: str, display_name: str = "", role: str = "user") -> User:
    name = _normalise_username(username)
    _check_password(password)
    _check_role(role)
    if db.scalar(select(User).where(User.username == name)) is not None:
        raise ConflictError(f'The username "{name}" is already taken.')
    user = User(
        username=name,
        display_name=display_name.strip() or name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(
    db: Session,
    acting: User,
    user: User,
    *,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> User:
    """Change a user. Refuses anything that would leave nobody able to
    manage users - the one mistake here that cannot be fixed from the app."""
    losing_admin = (role is not None and role != ROLE_ADMIN) or is_active is False
    if user.role == ROLE_ADMIN and user.is_active and losing_admin and _active_admins(db) <= 1:
        raise ConflictError("This is the only active admin. Make someone else an admin first.")
    if user.id == acting.id and is_active is False:
        raise ConflictError("You cannot switch off your own account.")

    if display_name is not None:
        user.display_name = display_name.strip() or user.username
    if role is not None:
        _check_role(role)
        user.role = role
    if is_active is not None:
        user.is_active = is_active
        if not is_active:
            revoke_all_sessions(db, user.id, commit=False)
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, password: str, *, keep_session_id: str | None = None) -> None:
    """Set a new password and sign the user out everywhere else."""
    _check_password(password)
    user.password_hash = hash_password(password)
    query = delete(AuthSession).where(AuthSession.user_id == user.id)
    if keep_session_id is not None:
        query = query.where(AuthSession.id != keep_session_id)
    db.execute(query)
    db.commit()


def delete_user(db: Session, acting: User, user: User) -> None:
    if user.id == acting.id:
        raise ConflictError("You cannot delete your own account.")
    if user.role == ROLE_ADMIN and user.is_active and _active_admins(db) <= 1:
        raise ConflictError("This is the only active admin. Make someone else an admin first.")
    revoke_all_sessions(db, user.id, commit=False)
    db.delete(user)
    db.commit()


# --- sessions ------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    # The same message whichever part was wrong: saying "no such user"
    # tells a stranger which usernames exist.
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Wrong username or password.", code="invalid_credentials")
    if not user.is_active:
        raise AuthError("This account has been switched off. Ask an admin.", code="account_disabled")
    return user


def start_session(db: Session, user: User) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    session = AuthSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().session_ttl_hours),
    )
    user.last_login_at = now
    db.add(session)
    # Expired rows are swept on the way, so the table does not grow forever.
    db.execute(delete(AuthSession).where(AuthSession.expires_at < _naive(now)))
    db.commit()
    db.refresh(session)
    return token, session


def resolve_token(db: Session, token: str) -> tuple[User, AuthSession]:
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash_token(token)))
    if session is None or _naive(session.expires_at) < _naive(_utcnow()):
        raise AuthError("Your sign-in has expired. Please sign in again.", code="session_expired")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AuthError("This account has been switched off. Ask an admin.", code="account_disabled")
    return user, session


def end_session(db: Session, session: AuthSession) -> None:
    db.delete(session)
    db.commit()


def revoke_all_sessions(db: Session, user_id: str, *, commit: bool = True) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    if commit:
        db.commit()
