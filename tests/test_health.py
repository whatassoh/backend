import pytest


@pytest.mark.django_db
def test_health_endpoint(client):
    response = client.get(
        "/health/",
    )

    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert "messaging" in payload
