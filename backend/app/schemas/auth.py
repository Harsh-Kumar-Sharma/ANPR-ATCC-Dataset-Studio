from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class AuthStatus(BaseModel):
    #: No user exists yet: the first person to arrive creates the admin.
    needs_setup: bool
    #: False while the database is behind the code and has no users table.
    database_ready: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class SignedIn(BaseModel):
    token: str
    expires_at: datetime
    user: UserRead


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordReset(BaseModel):
    new_password: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "user"


class UserUpdate(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
