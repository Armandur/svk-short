# Kortlink — URL-förkortare för Svenska kyrkan

## Översikt

Intern URL-förkortare för anställda i Svenska kyrkan. Anställda beställer kortlänkar via ett formulär, verifierar via e-post, och kan logga in med magic link för att hantera sina egna länkar. En admin kan övervaka och moderera alla länkar.

Primärt syfte: förkorta långa URL:er från `svenskakyrkan.se` till något hanterbart, t.ex. `sk.link/gdpr`.

---

## Stack

- **Python 3.12** med **FastAPI**
- **SQLite** (via inbyggd `sqlite3`, synkront räcker)
- **Jinja2** för HTML-templates (medföljer FastAPI)
- **itsdangerous** för signerade session-cookies
- **Lettermint** för transaktionell e-post (EU/EEA-baserat, GDPR-vänligt, SMTP + API)
- **Docker** + **docker-compose** för driftsättning
- **Caddy** som reverse proxy med automatisk TLS (produktion på Hetzner)
- **Nginx Proxy Manager** för lokal utveckling på Unraid (befintlig setup)

## Hosting

**Produktion — Hetzner Cloud**
- Instans: **CX22** (2 vCPU, 4 GB RAM, 40 GB SSD, ~5 €/mån)
- Datacenter: Falkenstein eller Nürnberg (Tyskland, inom EU)
- Caddy körs som en egen container i samma compose-stack och sköter TLS automatiskt mot Let's Encrypt — ingen separat certifikathantering behövs
- Backup: schemalagd SQLite-snapshot med `sqlite3 links.db ".backup backup.db"` + rsync till Hetzner Storage Box (minsta är 1 TB för ~3 €/mån, mer än nog)

**Lokal utveckling — Unraid**
- Appcontainern körs identiskt med samma Dockerfile och compose-fil
- NPM sköter reverse proxy och TLS precis som för övriga tjänster på Unraid
- Enda skillnaden är `.env`-filen: byt `BASE_URL` och använd en lokal testdomän

---

## Projektstruktur

```
kortlink/
├── app/
│   ├── main.py              # FastAPI-app, lifespan, mount routes
│   ├── database.py          # init_db(), get_db(), schema
│   ├── auth.py              # session-hantering, get_current_user, require_admin
│   ├── mail.py              # skicka_verifieringsmail(), skicka_loginmail()
│   ├── routes/
│   │   ├── public.py        # GET /, POST /request, GET /verify/<token>
│   │   ├── auth.py          # POST /login, GET /auth/<token>, GET /logout
│   │   ├── user.py          # GET /my-links, POST /my-links/<id>/edit, DELETE
│   │   └── admin.py         # GET /admin/*, POST /admin/links/<id>/toggle
│   └── templates/
│       ├── base.html
│       ├── index.html           # Beställningsformulär
│       ├── verify_ok.html
│       ├── login.html
│       ├── my_links.html
│       └── admin/
│           ├── links.html
│           ├── link_detail.html
│           └── users.html
├── data/                    # Monterad volym — links.db hamnar här
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Databas

```sql
CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    email      TEXT UNIQUE NOT NULL,
    is_admin   INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE links (
    id           INTEGER PRIMARY KEY,
    code         TEXT UNIQUE NOT NULL,
    target_url   TEXT NOT NULL,
    owner_id     INTEGER REFERENCES users(id),
    status       INTEGER DEFAULT 0,
    -- 0 = väntar på verifiering, 1 = aktiv, 2 = avaktiverad av admin, 3 = avaktiverad av ägare
    note         TEXT,                          -- intern beskrivning (frivillig)
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used_at DATETIME                       -- uppdateras vid varje klick
);

CREATE TABLE tokens (
    id         INTEGER PRIMARY KEY,
    token      TEXT UNIQUE NOT NULL,          -- kryptografiskt slumpmässig, 32 bytes hex
    user_id    INTEGER REFERENCES users(id),
    link_id    INTEGER REFERENCES links(id),  -- NULL = login-token
    purpose    TEXT NOT NULL,                 -- 'verify' eller 'login'
    expires_at DATETIME NOT NULL,
    used_at    DATETIME                       -- NULL = ej använd än
);

CREATE TABLE clicks (
    id         INTEGER PRIMARY KEY,
    link_id    INTEGER REFERENCES links(id),
    clicked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    referer    TEXT                           -- kan vara NULL — ingen IP, ingen UA lagras
);

CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY,
    action     TEXT NOT NULL,                 -- t.ex. 'transfer', 'deactivate', 'reactivate'
    actor_id   INTEGER REFERENCES users(id),  -- admin som utförde åtgärden
    link_id    INTEGER REFERENCES links(id),
    detail     TEXT,                          -- fritext, t.ex. "moved from a@b.se to c@d.se"
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Viktigt:** Inga IP-adresser eller User-Agent-strängar lagras i `clicks`. Klickstatistiken är därmed avanonymiserad och inte att betrakta som personuppgifter.

---

## Flöden

### 1. Beställning av kortlänk

```
POST /request  { email, target_url, code (frivillig), note (frivillig) }
  → Validera target_url (se URL-regler nedan)
  → Validera code (se kodregler nedan)
  → Skapa user om e-postadressen är ny
  → Skapa link med status=0
  → Skapa token: purpose='verify', expires 24h, kopplad till link_id
  → Skicka verifieringsmail till email
  → Visa sida: "Kolla din inkorg och klicka på länken för att aktivera"
```

**URL-regler för target_url:**

Använd `urllib.parse.urlparse()` för att parsa och validera. Samtliga villkor måste uppfyllas:

```python
from urllib.parse import urlparse

def validate_target_url(url: str) -> str | None:
    """Returnerar felmeddelande eller None om OK."""
    try:
        p = urlparse(url)
    except Exception:
        return "Ogiltig URL."

    if p.scheme != "https":
        return "URL:en måste börja med https://."

    # Tillåtna värdar — exakt match eller subdomän av svenskakyrkan.se
    host = p.netloc.lower()
    if host != "www.svenskakyrkan.se" and not host.endswith(".svenskakyrkan.se"):
        return "Endast URL:er under svenskakyrkan.se är tillåtna."

    if p.query:
        return "URL:en får inte innehålla frågeparametrar (?...)."

    if p.fragment:
        return "URL:en får inte innehålla fragment (#...)."

    # Endast rena sökvägsegment — inga tomma segment (dubbla //)
    path_parts = [seg for seg in p.path.split("/") if seg]
    for seg in path_parts:
        # Tillåt bokstäver, siffror, bindestreck och understreck
        if not re.match(r'^[a-zA-Z0-9\-_]+$', seg):
            return f"Ogiltigt sökvägssegment: '{seg}'. Endast bokstäver, siffror, - och _ tillåts."

    return None  # OK
```

Exempel på godkända URL:er:
- `https://www.svenskakyrkan.se/gdpr`
- `https://www.svenskakyrkan.se/harnosandsstift/dataskyddsforordningen`
- `https://goteborg.svenskakyrkan.se/om-oss`

Exempel på avvisade URL:er:
- `https://www.svenskakyrkan.se/gdpr?utm_source=nyhetsbrev` — frågeparametrar ej tillåtna
- `https://www.example.com/foo` — fel domän
- `https://www.svenskakyrkan.se/sida#avsnitt` — fragment ej tillåtet
- `http://www.svenskakyrkan.se/gdpr` — måste vara https

**Kodregler:**

- Endast tecknen `[a-z0-9-]`, lowercase, 2–60 tecken
- Strip och lowercase innan validering
- Bindestreck får ej vara första eller sista tecknet, ej heller dubbla bindestreck i rad
- Ej ett reserverat ord

**Reserverade koder** (får inte användas som kortlänk):
`admin`, `login`, `logout`, `verify`, `auth`, `static`, `my-links`, `request`

**Kodgenerering om ingen kod anges:**
Generera 6 slumpmässiga tecken ur `[a-z0-9]`. Kontrollera mot databasen, försök igen vid kollision.

### 2. Verifiering

```
GET /verify/<token>
  → Slå upp token: måste finnas, purpose='verify', used_at IS NULL, expires_at > now
  → Sätt link.status = 1
  → Sätt token.used_at = now
  → Skapa signerad session-cookie för user
  → Visa bekräftelsesida med den färdiga kortlänken
```

### 3. Omdirigering

```
GET /<code>
  → Slå upp code i links WHERE status=1
  → Om hittad: INSERT INTO clicks (link_id, clicked_at, referer)
               UPDATE links SET last_used_at = CURRENT_TIMESTAMP WHERE id = link_id
               Returnera 302 till target_url
  → Om ej hittad: Returnera 404-sida
```

**Använd 302 (ej 301).** 301 cachas permanent i webbläsaren — gör det omöjligt att ändra eller avaktivera en länk utan att slutanvändaren rensar cache.

### 4. Inloggning (magic link)

```
POST /login  { email }
  → Kolla att user finns (måste ha beställt minst en länk)
  → Skapa token: purpose='login', link_id=NULL, expires 1h
  → Skicka loginmail
  → Visa sida: "Kolla din inkorg"

GET /auth/<token>
  → Validera token: purpose='login', ej använd, ej utgången
  → Sätt token.used_at = now
  → Skapa session-cookie
  → Redirect till /my-links
```

### 5. Användargränssnitt (/my-links)

- Lista egna länkar med status, klickantal, skapad, senast använd
- **Peka om länk:** Användaren kan ändra `target_url` på en aktiv länk. Ny URL valideras med samma regler som vid beställning. Ändringen träder i kraft omedelbart utan ny verifiering (användaren är redan autentiserad).
- **Avaktivera länk:** Sätter `status=3` (avaktiverad av ägare). Länken slutar fungera direkt. Kan återaktiveras av admin vid behov.
- **Kan ej** ändra `code` (koden är permanent)

```
POST /my-links/<id>/update  { target_url }
  → Kontrollera att länken tillhör inloggad user
  → Validera ny target_url med validate_target_url()
  → UPDATE links SET target_url = ? WHERE id = ? AND owner_id = ?
  → Redirect till /my-links med bekräftelsemeddelande

POST /my-links/<id>/deactivate
  → Kontrollera att länken tillhör inloggad user och har status=1
  → UPDATE links SET status = 3 WHERE id = ? AND owner_id = ?
  → Redirect till /my-links
```

### 6. Admingränssnitt (/admin)

Kräver `is_admin=1` i databasen. Sätt manuellt med:
```bash
sqlite3 data/links.db "UPDATE users SET is_admin=1 WHERE email='din@epost.se';"
```

**Vyer:**

| Route | Innehåll |
|---|---|
| `GET /admin/links` | Tabell: alla länkar, ägare, status, klickantal, senast använd, knappar för aktivera/avaktivera |
| `GET /admin/links/<id>` | Detalj: klick per dag (graf), ändra target_url, ändra status, flytta ägare |
| `GET /admin/users` | Lista användare, antal länkar per user, knapp för att flytta alla länkar |
| `POST /admin/links/<id>/transfer` | Flytta enskild länk till annan ägare |
| `POST /admin/users/<id>/transfer-all` | Flytta alla länkar från en användare till annan e-postadress |

**Ägaröverföring:**

```
POST /admin/links/<id>/transfer  { new_email }
  → Hitta eller skapa user med new_email
  → UPDATE links SET owner_id = ny_user_id WHERE id = ?
  → Logga händelsen (se audit_log nedan)

POST /admin/users/<id>/transfer-all  { new_email }
  → Hitta eller skapa user med new_email
  → UPDATE links SET owner_id = ny_user_id WHERE owner_id = gammal_user_id
  → Logga händelsen
```

Notera: den gamla användaren tas inte bort automatiskt — admin får manuellt avgöra om kontot ska inaktiveras.

**Kolumner att visa i länktabellen:**
`kod | mål-URL | ägare | status | klick totalt | senast använd | skapad | åtgärder`

`senast använd` hämtas direkt från `links.last_used_at` — ingen extra JOIN behövs.

**Klickgraf:** Hämta `SELECT date(clicked_at) as dag, count(*) as antal FROM clicks WHERE link_id=? GROUP BY dag ORDER BY dag` och rendera med Chart.js (CDN) i templates.

---

## E-post

Använd **Lettermint** (https://lettermint.co). Infrastruktur helt inom EU/EEA, GDPR-vänligt, stödjer både SMTP-relay och REST API. Gratisnivå räcker för detta ändamål.

Konfigurera DNS för domänen enligt Lettermints instruktioner:
- SPF-post i TXT
- DKIM via CNAME-poster (Lettermint genererar dessa i dashboarden)
- Rekommenderat: DMARC

Lettermint stödjer SMTP-relay, vilket gör det enkelt att byta ut mot annan leverantör senare utan kodändringar — konfigurera bara om env-variablerna. Välj SMTP-varianten i koden för maximal portabilitet:

```python
# mail.py — SMTP via Lettermint
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ["SMTP_HOST"]        # smtp.lettermint.net (eller motsv.)
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
MAIL_FROM = os.environ["MAIL_FROM"]        # t.ex. kortlink@din-domän.se

def _send(to: str, subject: str, html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(MAIL_FROM, to, msg.as_string())

def skicka_verifieringsmail(to: str, verify_url: str, code: str, target_url: str):
    _send(
        to=to,
        subject=f"Aktivera din kortlänk /{code}",
        html=f"""
            <p>Du har beställt kortlänken <strong>/{code}</strong>
            som pekar till {target_url}.</p>
            <p><a href="{verify_url}">Klicka här för att aktivera länken</a></p>
            <p>Länken är giltig i 24 timmar.</p>
        """
    )

def skicka_loginmail(to: str, login_url: str):
    _send(
        to=to,
        subject="Logga in på Kortlink",
        html=f"""
            <p><a href="{login_url}">Klicka här för att logga in</a></p>
            <p>Länken är giltig i 1 timme och kan bara användas en gång.</p>
        """
    )
```

---

## Session-hantering

Använd `itsdangerous.URLSafeTimedSerializer` för signerade cookies. Lagra bara `user_id` i cookien — slå upp resten från databasen vid varje request.

```python
from itsdangerous import URLSafeTimedSerializer
import os

SECRET_KEY = os.environ["SECRET_KEY"]  # generera med: python -c "import secrets; print(secrets.token_hex(32))"
serializer = URLSafeTimedSerializer(SECRET_KEY)

def create_session_cookie(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})

def decode_session_cookie(cookie: str) -> dict | None:
    try:
        return serializer.loads(cookie, max_age=60 * 60 * 24 * 30)  # 30 dagar
    except Exception:
        return None
```

Sätt cookien med `httponly=True, secure=True, samesite="lax"`.

---

## Docker

### Dockerfile (gemensam för dev och prod)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Produktion — Hetzner med Caddy

Caddy hämtar och förnyar TLS-certifikat automatiskt. Ingen separat certifikathantering behövs.

```yaml
# docker-compose.yml  (produktion)
services:
  kortlink:
    build: .
    volumes:
      - ./data:/app/data
    env_file: .env
    restart: unless-stopped
    # Exponeras ej mot internet direkt — Caddy proxar

  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data        # Certifikat sparas här
      - caddy_config:/config
    restart: unless-stopped

volumes:
  caddy_data:
  caddy_config:
```

```
# Caddyfile
din-domän.se {
    reverse_proxy kortlink:8000
}
```

Det är allt Caddy behöver. TLS hanteras helt automatiskt.

### Miljöfil

```bash
# .env  (lägg till i .gitignore — checka aldrig in denna)
DATABASE_PATH=data/links.db
SMTP_HOST=smtp.lettermint.net
SMTP_PORT=587
SMTP_USER=din-lettermint-användare
SMTP_PASS=ditt-lettermint-lösenord
MAIL_FROM=kortlink@din-domän.se
SECRET_KEY=       # generera: python -c "import secrets; print(secrets.token_hex(32))"
BASE_URL=https://din-domän.se
```

### Lokal utveckling — Unraid med NPM

Använd samma `docker-compose.yml` men utan Caddy-blocket — NPM sköter reverse proxy precis som för övriga containers på Unraid:

```yaml
# docker-compose.dev.yml  (kör med: docker compose -f docker-compose.dev.yml up)
services:
  kortlink:
    build: .
    volumes:
      - ./data:/app/data
    env_file: .env.dev
    ports:
      - "8000:8000"    # NPM proxar mot denna port
    restart: unless-stopped
```

```bash
# .env.dev
DATABASE_PATH=data/links.db
SMTP_HOST=smtp.lettermint.net
SMTP_PORT=587
SMTP_USER=din-lettermint-användare
SMTP_PASS=ditt-lettermint-lösenord
MAIL_FROM=kortlink@din-domän.se
SECRET_KEY=dev-nyckel-ej-i-produktion
BASE_URL=https://kortlink.lokal.din-domän.se   # din lokala testdomän via NPM
```

### Deploymentsflöde till Hetzner

```bash
# Första gången
ssh root@din-hetzner-ip
apt update && apt install -y docker.io docker-compose-plugin
git clone https://github.com/ditt-repo/kortlink.git
cd kortlink
cp .env.example .env   # fyll i värden
docker compose up -d

# Uppdatering
git pull
docker compose build kortlink
docker compose up -d --no-deps kortlink
```

### Backup av SQLite

Lägg in ett cron-jobb på Hetzner-servern som tar en säker backup utan att låsa databasen:

```bash
# /etc/cron.daily/kortlink-backup
#!/bin/bash
cd /root/kortlink
sqlite3 data/links.db ".backup data/backup-$(date +%Y%m%d).db"
# Rensa backuper äldre än 30 dagar
find data/ -name "backup-*.db" -mtime +30 -delete
```

Vill du ha off-site backup: lägg till rsync mot en Hetzner Storage Box (~3 €/mån för 1 TB).

---

## requirements.txt

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
jinja2==3.1.4
python-multipart==0.0.9
itsdangerous==2.2.0
# E-post skickas via smtplib i standardbiblioteket — ingen extra dependency behövs
```

---

## Säkerhet att tänka på

- **Rate limiting på /request och /login** — annars kan någon spamma verifieringsmail. Enkel lösning: räkna antal requests per IP per timme i en dict i minnet (eller en liten extra tabell i SQLite).
- **Tokens är engångsbrickor** — `used_at` sätts direkt vid användning, aldrig återanvändbara.
- **Utgångna tokens** — kör en daglig cleanup: `DELETE FROM tokens WHERE expires_at < datetime('now')`.
- **Kodvalidering** — strip och lowercase på `code` innan lagring. Tillåt endast `[a-z0-9-]`.
- **URL-validering** — `validate_target_url()` körs både vid beställning och vid användarens ändring av target_url. Domän måste vara `svenskakyrkan.se` eller subdomän, inga query-parametrar, inga fragment, endast rena sökvägssegment.
- **Admin-autentisering** — `is_admin` kontrolleras vid varje request, inte bara vid inloggning.
- **Ägaröverföringar loggas** — alla admin-åtgärder skrivs till `audit_log` för spårbarhet.

---

## Önskade förbättringar (backlog)

- [ ] Anpassade 404/500-felsidor
- [ ] Paginering i admintabellen
- [ ] Exportera klickstatistik som CSV
- [ ] Webhook/notifiering till admin vid ny beställning
- [ ] DMARC-rapport-parsing för att övervaka e-postleverans
- [ ] Visa audit_log i admingränssnittet per länk och per användare
