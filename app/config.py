import os


BASE_URL: str = os.environ.get("BASE_URL", "http://localhost:8000")

RATE_LIMIT_PER_HOUR: int = 5


class LinkStatus:
    PENDING = 0        # Väntar på e-postverifiering
    ACTIVE = 1         # Aktiv, omdirigerar
    DISABLED_ADMIN = 2 # Avaktiverad av admin
    DISABLED_OWNER = 3 # Avaktiverad av ägare


RESERVED_CODES = {
    "admin", "login", "logout", "verify", "auth",
    "static", "mina-lankar", "request", "om", "integritet",
    "transfer-action", "bestall", "bundle", "my-bundles",
}
