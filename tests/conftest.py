import pytest

from app.db import get_connection, init_db


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    conn = get_connection()
    try:
        init_db(conn)
    finally:
        conn.close()


@pytest.fixture
def db_conn():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.execute("DELETE FROM cases")
        conn.close()
