from app import database
from app.config import RATE_LIMIT_PER_HOUR, RATE_LIMIT_PER_HOUR_IP


def _begar_login(client, hamta_csrf_token, email: str, ip: str | None = None):
    csrf_token = hamta_csrf_token(client)
    headers = {"x-test-client-ip": ip} if ip else None
    return client.post(
        "/login", data={"email": email, "csrf_token": csrf_token}, headers=headers
    )


# Om SQL jämför mot Python-ISO växer antalet rader för samma e-post till gränsen plus ett.
def test_login_route_nekar_anropet_efter_timgransen(client, hamta_csrf_token, monkeypatch):
    monkeypatch.setattr("app.routes.auth.skicka_loginmail", lambda *args, **kwargs: None)
    email = "begransad@svenskakyrkan.se"

    svar = [
        _begar_login(client, hamta_csrf_token, email)
        for _ in range(RATE_LIMIT_PER_HOUR + 1)
    ]

    assert all(response.status_code == 200 for response in svar[:RATE_LIMIT_PER_HOUR])
    assert svar[-1].status_code == 429
    with database.get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action='login'",
            (f"email:{email}",),
        ).fetchone()[0]
    assert count == RATE_LIMIT_PER_HOUR


# Om IP-värden delar hink nekas den andra adressens första anrop.
def test_rate_limit_hinkar_ar_atskilda_per_ip(client, hamta_csrf_token, monkeypatch):
    monkeypatch.setattr("app.routes.auth.skicka_loginmail", lambda *args, **kwargs: None)
    first_ip = "192.0.2.10"
    second_ip = "192.0.2.11"
    for index in range(RATE_LIMIT_PER_HOUR_IP):
        response = _begar_login(
            client, hamta_csrf_token, f"person{index}@svenskakyrkan.se", first_ip
        )
        assert response.status_code == 200
    blocked = _begar_login(client, hamta_csrf_token, "blockerad@svenskakyrkan.se", first_ip)
    allowed = _begar_login(client, hamta_csrf_token, "annan@svenskakyrkan.se", second_ip)
    assert blocked.status_code == 429
    assert allowed.status_code == 200
    with database.get_db() as db:
        keys = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT ip FROM rate_limits WHERE action='login-ip'"
            ).fetchall()
        }


# Om IP-taket återanvänder femgränsen får den sjätte olika användaren status 429.
def test_login_route_har_separat_hogre_ip_tak(client, hamta_csrf_token, monkeypatch):
    monkeypatch.setattr("app.routes.auth.skicka_loginmail", lambda *args, **kwargs: None)
    responses = [
        _begar_login(client, hamta_csrf_token, f"person{i}@svenskakyrkan.se")
        for i in range(RATE_LIMIT_PER_HOUR + 1)
    ]
    assert all(response.status_code == 200 for response in responses)
