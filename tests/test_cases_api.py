from fastapi.testclient import TestClient

from app.db import create_case
from app.main import app
from app.models import Case, CaseStatus, Channel

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_read_back_a_bare_case(db_conn):
    case = Case(case_id="CASE-TEST-0001", channel=Channel.EMAIL)
    create_case(db_conn, case)

    response = client.get(f"/cases/{case.case_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-TEST-0001"
    assert body["channel"] == "email"
    assert body["status"] == CaseStatus.NEW.value


def test_get_unknown_case_returns_404(db_conn):
    response = client.get("/cases/does-not-exist")
    assert response.status_code == 404
