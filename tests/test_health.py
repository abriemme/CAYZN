from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cayzn.db import get_db
from cayzn.main import app


def test_health():
    def sqlite_db():
        with Session(create_engine("sqlite://")) as session:
            yield session

    app.dependency_overrides[get_db] = sqlite_db
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
