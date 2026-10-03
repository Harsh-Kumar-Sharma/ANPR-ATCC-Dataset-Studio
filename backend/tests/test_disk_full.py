"""A full disk says so, rather than "An unexpected error occurred"."""

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.errors import register_exception_handlers

# Its own app, with the studio's error handling: the real one should not
# grow routes that only exist to raise.
app = FastAPI()
register_exception_handlers(app)


@app.get("/disk-full")
def _disk_full():
    raise OperationalError("INSERT ...", {}, sqlite3.OperationalError("database or disk is full"))


@app.get("/locked")
def _locked():
    raise OperationalError("INSERT ...", {}, sqlite3.OperationalError("database is locked"))


client = TestClient(app, raise_server_exceptions=False)


def test_a_full_disk_is_named():
    response = client.get("/disk-full")
    assert response.status_code == 507
    assert response.json()["code"] == "disk_full"
    assert "disk is full" in response.json()["message"]


def test_other_database_errors_stay_generic():
    response = client.get("/locked")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
