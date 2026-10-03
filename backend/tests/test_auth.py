"""Signing in, and an admin deciding who can."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.user import AuthSession, User
from app.db.session import SessionLocal
from app.main import app
from app.services import auth as auth_service

client = TestClient(app)

PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def _no_users(real_auth):
    with SessionLocal() as db:
        db.execute(delete(AuthSession))
        db.execute(delete(User))
        db.commit()
    yield


def _setup_admin(username="admin", password=PASSWORD) -> str:
    response = client.post("/auth/setup", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_user(admin_token: str, username: str, role: str = "user") -> dict:
    response = client.post(
        "/users", json={"username": username, "password": PASSWORD, "role": role}, headers=_bearer(admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(username: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"username": username, "password": password})


# --- the way in ----------------------------------------------------------------


def test_an_empty_studio_asks_for_its_first_admin():
    assert client.get("/auth/status").json() == {"needs_setup": True, "database_ready": True}


def test_setup_creates_an_admin_and_signs_them_in():
    token = _setup_admin()
    me = client.get("/auth/me", headers=_bearer(token)).json()
    assert me["username"] == "admin"
    assert me["role"] == "admin"
    assert client.get("/auth/status").json()["needs_setup"] is False


def test_setup_is_refused_once_anyone_exists():
    _setup_admin()
    response = client.post("/auth/setup", json={"username": "intruder", "password": PASSWORD})
    assert response.status_code == 409


def test_the_api_is_closed_without_signing_in():
    response = client.get("/projects")
    assert response.status_code == 401
    assert response.json()["code"] == "not_signed_in"


def test_the_health_check_and_schema_banner_stay_open():
    assert client.get("/health").status_code == 200
    assert client.get("/schema").status_code == 200


def test_a_signed_in_user_reaches_the_api():
    token = _setup_admin()
    assert client.get("/projects", headers=_bearer(token)).status_code == 200


def test_the_token_can_ride_in_the_url_for_pictures_and_video():
    token = _setup_admin()
    assert client.get(f"/projects?access_token={token}").status_code == 200


def test_a_wrong_password_says_the_same_as_a_wrong_username():
    _setup_admin()
    wrong_password = _login("admin", "nope-nope-nope")
    no_such_user = _login("nobody", PASSWORD)
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json()["message"] == no_such_user.json()["message"]


def test_usernames_are_not_case_sensitive():
    _setup_admin("Harsh")
    assert _login("HARSH").status_code == 200


def test_signing_out_ends_that_session():
    token = _setup_admin()
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 204
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_passwords_are_not_stored():
    _setup_admin()
    with SessionLocal() as db:
        stored = db.query(User).one().password_hash
    assert PASSWORD not in stored
    assert auth_service.verify_password(PASSWORD, stored)


def test_a_short_password_is_refused():
    response = client.post("/auth/setup", json={"username": "admin", "password": "short"})
    assert response.status_code == 422


# --- managing people -----------------------------------------------------------


def test_an_admin_adds_a_user_who_can_then_sign_in():
    admin = _setup_admin()
    _add_user(admin, "labeller")
    assert _login("labeller").status_code == 200


def test_a_taken_username_is_refused():
    admin = _setup_admin()
    _add_user(admin, "labeller")
    response = client.post(
        "/users", json={"username": "Labeller", "password": PASSWORD}, headers=_bearer(admin)
    )
    assert response.status_code == 409


def test_a_plain_user_cannot_manage_users_but_can_work():
    admin = _setup_admin()
    _add_user(admin, "labeller")
    token = _login("labeller").json()["token"]
    assert client.get("/users", headers=_bearer(token)).status_code == 403
    assert client.get("/projects", headers=_bearer(token)).status_code == 200


def test_switching_a_user_off_signs_them_out_and_keeps_them_out():
    admin = _setup_admin()
    user = _add_user(admin, "labeller")
    token = _login("labeller").json()["token"]

    response = client.patch(f"/users/{user['id']}", json={"is_active": False}, headers=_bearer(admin))
    assert response.status_code == 200

    assert client.get("/projects", headers=_bearer(token)).status_code == 401
    assert _login("labeller").json()["code"] == "account_disabled"


def test_resetting_a_password_signs_the_user_out():
    admin = _setup_admin()
    user = _add_user(admin, "labeller")
    token = _login("labeller").json()["token"]

    response = client.put(
        f"/users/{user['id']}/password", json={"new_password": "brand-new-pass"}, headers=_bearer(admin)
    )
    assert response.status_code == 204
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401
    assert _login("labeller", "brand-new-pass").status_code == 200


def test_changing_your_own_password_needs_the_current_one():
    token = _setup_admin()
    wrong = client.put(
        "/auth/me/password",
        json={"current_password": "not-it-at-all", "new_password": "another-pass"},
        headers=_bearer(token),
    )
    assert wrong.status_code == 403

    right = client.put(
        "/auth/me/password",
        json={"current_password": PASSWORD, "new_password": "another-pass"},
        headers=_bearer(token),
    )
    assert right.status_code == 204
    # This window stays signed in.
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200
    assert _login("admin", "another-pass").status_code == 200


def test_the_last_admin_cannot_be_demoted_switched_off_or_deleted():
    admin = _setup_admin()
    me = client.get("/auth/me", headers=_bearer(admin)).json()
    other = _add_user(admin, "other-admin", role="user")

    assert client.patch(f"/users/{me['id']}", json={"role": "user"}, headers=_bearer(admin)).status_code == 409
    assert client.delete(f"/users/{me['id']}", headers=_bearer(admin)).status_code == 409

    # With a second admin, the first may step down.
    client.patch(f"/users/{other['id']}", json={"role": "admin"}, headers=_bearer(admin))
    assert client.patch(f"/users/{me['id']}", json={"role": "user"}, headers=_bearer(admin)).status_code == 200


def test_an_admin_deletes_a_user():
    admin = _setup_admin()
    user = _add_user(admin, "labeller")
    assert client.delete(f"/users/{user['id']}", headers=_bearer(admin)).status_code == 204
    assert _login("labeller").status_code == 401
    assert [u["username"] for u in client.get("/users", headers=_bearer(admin)).json()] == ["admin"]


# --- the database update, once people exist -----------------------------------


def test_once_someone_exists_only_an_admin_may_update_the_database():
    admin = _setup_admin()
    _add_user(admin, "labeller")
    labeller = _login("labeller").json()["token"]

    anonymous = client.post("/schema/upgrade")
    assert anonymous.status_code == 401
    assert client.post("/schema/upgrade", headers=_bearer(labeller)).status_code == 403
    # Reading where the database stands stays open: the banner needs it
    # before anyone has signed in.
    assert client.get("/schema").status_code == 200
