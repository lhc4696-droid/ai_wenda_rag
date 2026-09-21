from app.integrations.email import EmailUnavailable


class FakeMailer:
    def __init__(self):
        self.codes = []
        self.notifications = []
        self.fail = False

    def ensure_ready(self):
        pass

    def send_code(self, email, code, purpose, ttl):
        if self.fail:
            raise EmailUnavailable("simulated delivery failure")
        self.codes.append({"email": email, "code": code, "purpose": purpose})

    def send_password_changed(self, email):
        self.notifications.append(email)


def request_code(client, email, purpose="register"):
    response = client.post("/api/auth/email-code", json={"email": email, "purpose": purpose})
    assert response.status_code == 202, response.text
    return next(item["code"] for item in reversed(client.app.state.mailer.codes) if item["email"] == email and item["purpose"] == purpose)


def register_user(client, credentials, headers=None):
    email = f"user{len(client.app.state.mailer.codes)}@example.com"
    code = request_code(client, email)
    return client.post("/api/auth/register", json={**credentials, "email": email, "code": code}, headers=headers)
