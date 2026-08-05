from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_hello() -> None:
    response = client.get("/api/hello")
    assert response.status_code == 200
    assert "message" in response.json()
