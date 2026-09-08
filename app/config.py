import os
import sys
import warnings
from enum import IntEnum

# Vilken miljö appen kör i: "drift", "staging" eller "utveckling".
# Styr bara den synliga miljömarkeringen. Spärrar som faktiskt skyddar
# något ska ALDRIG hänga på den här - en miljövariabel som glöms bort
# vid en deploy hade då tyst öppnat dem.
MILJO = os.environ.get("MILJO", "drift")

BASE_URL: str = os.environ.get("BASE_URL", "http://localhost:8000")

SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if BASE_URL.startswith("https://"):
        sys.exit("SECRET_KEY saknas - vägrar starta i HTTPS-läge.")
    SECRET_KEY = "dev-secret-change-in-production"
    warnings.warn(
        "SECRET_KEY saknas - använder dev-default. Sätt SECRET_KEY i .env.",
        stacklevel=1,
    )

ALLOWED_EMAIL_DOMAIN: str = os.environ.get("ALLOWED_EMAIL_DOMAIN", "svenskakyrkan.se")

RATE_LIMIT_PER_HOUR: int = 5

# Egen, högre gräns för spärrar som nycklas på IP i flöden där flera personer
# rimligen delar adress: ett kontor bakom samma NAT ser ut som en besökare.
RATE_LIMIT_PER_HOUR_IP: int = 30


class LinkStatus(IntEnum):
    PENDING = 0  # Väntar på e-postverifiering
    ACTIVE = 1  # Aktiv, omdirigerar
    DISABLED_ADMIN = 2  # Avaktiverad av admin
    DISABLED_OWNER = 3  # Avaktiverad av ägare


RESERVED_CODES = {
    "admin",
    "login",
    "logout",
    "verify",
    "auth",
    "static",
    "mina-lankar",
    "request",
    "om",
    "integritet",
    "transfer-action",
    "bestall",
    "bundle",
    "my-bundles",
    "mina-samlingar",
    "nyheter",
    # Reserverade i förväg åt Swish-QR-generatorn (TASK-1673). Kontrollerat
    # 2026-09-07: ingen av dem var tagen i produktionen. Att reservera dem
    # innan funktionen finns kostar ingenting, medan att ta tillbaka en
    # kortkod någon redan tryckt på ett anslag kostar desto mer.
    "swish",
    "swishqr",
}
