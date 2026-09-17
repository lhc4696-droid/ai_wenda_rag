import pytest

from tests.fakes import request_code


@pytest.mark.parametrize("password", ["abcd1234", "x" * 128])
def test_password_boundaries_support_registration_login_and_reset(client, password):
    credentials = {"username": "password_user", "password": password}
    email = "password@example.com"
    registration_code = request_code(client, email)
    assert client.post("/api/auth/register", json={
        **credentials, "email": email, "code": registration_code,
    }).status_code == 201
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post("/api/auth/login", json=credentials).status_code == 200

    code = request_code(client, email, "reset_password")
    new_password = "newpass8"
    result = client.post("/api/auth/password/reset", json={
        "email": email, "code": code, "new_password": new_password,
    })
    assert result.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json=credentials).status_code == 401
    assert client.post("/api/auth/login", json={**credentials, "password": new_password}).status_code == 200


@pytest.mark.parametrize("password", ["abc1234", " " * 8, "x" * 129])
@pytest.mark.parametrize("action", ["register", "login", "password/reset"])
def test_password_limits_are_enforced_without_echoing_secrets(client, action, password):
    if action == "password/reset":
        field = "new_password"
        payload = {"email": "password@example.com", "code": "345678", field: password}
    else:
        field = "password"
        payload = {"username": "password_user", field: password}
        if action == "register":
            payload.update(email="password@example.com", code="345678")
    response = client.post(f"/api/auth/{action}", json=payload)
    assert response.status_code == 422
    assert any(error["loc"][-1] == field for error in response.json()["detail"])
    assert password not in response.text


@pytest.mark.parametrize("schema_name,field", [
    ("Credentials", "password"), ("RegisterRequest", "password"),
    ("ResetPasswordRequest", "new_password"),
])
def test_openapi_documents_eight_character_minimum(client, schema_name, field):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    password = schemas[schema_name]["properties"][field]
    assert password["minLength"] == 8
    assert password["maxLength"] == 128
