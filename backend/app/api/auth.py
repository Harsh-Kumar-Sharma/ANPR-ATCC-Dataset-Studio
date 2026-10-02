"""Signing in, and who may.

Every other router is mounted behind ``require_user`` in ``app.main``;
the routes here are the way in, so only ``/auth/status``,
``/auth/setup`` and ``/auth/login`` are open.

The token travels as ``Authorization: Bearer <token>``. Pictures,
video and downloads are fetched by the browser itself - an ``<img>``
cannot send a header - so ``?access_token=`` is accepted as well.
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models.user import ROLE_ADMIN, AuthSession, User
from app.db.session import get_db
from app.schemas.auth import (
    AuthStatus,
    LoginRequest,
    PasswordChange,
    PasswordReset,
    SetupRequest,
    SignedIn,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services import auth as auth_service
from app.services.auth import AuthError, ForbiddenError

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


def _token_from(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.query_params.get("access_token") or None


def current_session(request: Request, db: Session = Depends(get_db)) -> tuple[User, AuthSession]:
    token = _token_from(request)
    if token is None:
        raise AuthError("Please sign in.", code="not_signed_in")
    return auth_service.resolve_token(db, token)


def require_user(signed_in: tuple[User, AuthSession] = Depends(current_session)) -> User:
    return signed_in[0]


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise ForbiddenError("Only an admin can manage users.")
    return user


def _signed_in(db: Session, user: User) -> SignedIn:
    token, session = auth_service.start_session(db, user)
    return SignedIn(token=token, expires_at=session.expires_at, user=UserRead.model_validate(user))


# --- the way in ----------------------------------------------------------------


@router.get("/status", response_model=AuthStatus)
def status(db: Session = Depends(get_db)) -> AuthStatus:
    try:
        return AuthStatus(needs_setup=auth_service.user_count(db) == 0)
    except OperationalError:
        # The users table is not there yet. The schema banner offers the
        # fix; saying so here keeps the sign-in screen from just failing.
        return AuthStatus(needs_setup=False, database_ready=False)


@router.post("/setup", response_model=SignedIn, status_code=201)
def setup(payload: SetupRequest, db: Session = Depends(get_db)) -> SignedIn:
    """Create the first admin. Only while there is nobody at all."""
    if auth_service.user_count(db) > 0:
        raise ConflictError("The studio is already set up. Sign in instead.", code="already_set_up")
    user = auth_service.create_user(db, payload.username, payload.password, payload.display_name, ROLE_ADMIN)
    return _signed_in(db, user)


@router.post("/login", response_model=SignedIn)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> SignedIn:
    user = auth_service.authenticate(db, payload.username, payload.password)
    return _signed_in(db, user)


@router.post("/logout", status_code=204)
def logout(signed_in: tuple[User, AuthSession] = Depends(current_session), db: Session = Depends(get_db)) -> Response:
    auth_service.end_session(db, signed_in[1])
    return Response(status_code=204)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(require_user)) -> User:
    return user


@router.put("/me/password", status_code=204)
def change_my_password(
    payload: PasswordChange,
    signed_in: tuple[User, AuthSession] = Depends(current_session),
    db: Session = Depends(get_db),
) -> Response:
    user, session = signed_in
    if not auth_service.verify_password(payload.current_password, user.password_hash):
        raise ForbiddenError("Your current password is not right.", code="wrong_password")
    # This window stays signed in; every other one is signed out.
    auth_service.set_password(db, user, payload.new_password, keep_session_id=session.id)
    return Response(status_code=204)


# --- managing people (admin) ------------------------------------------------


@users_router.get("", response_model=list[UserRead])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@users_router.post("", response_model=UserRead, status_code=201)
def create_user(payload: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> User:
    return auth_service.create_user(db, payload.username, payload.password, payload.display_name, payload.role)


@users_router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: str, payload: UserUpdate, acting: User = Depends(require_admin), db: Session = Depends(get_db)
) -> User:
    user = auth_service.get_user_or_404(db, user_id)
    return auth_service.update_user(
        db, acting, user, display_name=payload.display_name, role=payload.role, is_active=payload.is_active
    )


@users_router.put("/{user_id}/password", status_code=204)
def reset_password(
    user_id: str, payload: PasswordReset, _: User = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    user = auth_service.get_user_or_404(db, user_id)
    auth_service.set_password(db, user, payload.new_password)
    return Response(status_code=204)


@users_router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, acting: User = Depends(require_admin), db: Session = Depends(get_db)) -> Response:
    user = auth_service.get_user_or_404(db, user_id)
    auth_service.delete_user(db, acting, user)
    return Response(status_code=204)
