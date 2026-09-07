from pathlib import Path


REPOROT = Path(__file__).resolve().parents[1]


# Om uvicorn inte litar på proxykedjan saknas värdet * för FORWARDED_ALLOW_IPS.
def test_compose_later_uvicorn_lasa_proxyadress():
    compose = (REPOROT / "docker-compose.yml").read_text()
    assert 'FORWARDED_ALLOW_IPS: "*"' in compose


# Om Caddy vidarebefordrar en förfalskad kedja saknas överskrivningen med remote_host.
def test_caddy_satter_forsta_ip_adressen_sjalv():
    caddyfile = (REPOROT / "Caddyfile").read_text()
    assert "header_up X-Forwarded-For {remote_host}" in caddyfile
