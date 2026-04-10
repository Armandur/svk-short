# svky.se — Kodbasbeskrivning för Claude

## Vad projektet är

Intern URL-förkortare för Svenska kyrkan. Anställda beställer kortlänkar via ett formulär, verifierar via e-post (magic link), och kan logga in för att hantera sina egna länkar. En admin kan övervaka och moderera alla länkar.

Exempelflöde: `POST /request` → verifieringsmail → `GET /verify/<token>` → länken är aktiv → `GET /gdpr` → 302 till `https://www.svenskakyrkan.se/...`

## Stack

- **Python 3.12 + FastAPI** (ASGI, uvicorn)
- **SQLite** via `sqlite3` i standardbiblioteket — synkront, ingen ORM
- **Jinja2** templates (medföljer FastAPI)
- **itsdangerous** för signerade session-cookies
- **smtplib** för e-post via Lettermint SMTP
- **Caddy** som reverse proxy i produktion (auto-TLS)

## Filstruktur

```
app/
  main.py          # FastAPI-app, lifespan, mountar routes, exception handlers
  database.py      # init_db(), get_db() contextmanager, hela SQL-schemat
  auth.py          # session-cookies (skapa/läsa), get_current_user(), require_user/admin
  mail.py          # skicka_verifieringsmail(), skicka_loginmail() — SMTP
  validation.py    # validate_target_url(), validate_code() — returnerar felstr eller None
  config.py        # Delade konstanter: BASE_URL, LinkStatus, RESERVED_CODES
  routes/
    public.py      # GET /, POST /request, GET /verify/<token>, GET /<code>
    auth.py        # GET/POST /login, GET /auth/<token>, GET /logout
    user.py        # GET /my-links, POST /my-links/<id>/update, /deactivate
    admin.py       # /admin/links, /admin/links/<id>, /admin/users, etc.
  templates/
    base.html      # Bas-template: header, nav, footer, CSS-variabler, {% block scripts %}
    index.html     # Beställningsformulär
    pending.html   # "Kolla din inkorg" efter beställning
    verify_ok.html # "Länken är aktiv!" efter verifiering
    login.html     # Magic link-login
    login_sent.html# "Inloggningslänk skickad"
    my_links.html  # Användarens egna länkar
    error.html     # Generell felsida
    404.html       # Kod hittades inte
    admin/
      links.html        # Admintabell med alla links
      link_detail.html  # Detaljvy, klickgraf (Chart.js), ägaröverföring
      users.html        # Användarlista
```

## Link-statusar (app/config.py: LinkStatus)

| Värde | Konstant | Betydelse |
|-------|----------|-----------|
| 0 | `PENDING` | Skapad, väntar på e-postverifiering |
| 1 | `ACTIVE` | Aktiv, omdirigerar |
| 2 | `DISABLED_ADMIN` | Avaktiverad av admin |
| 3 | `DISABLED_OWNER` | Avaktiverad av ägare |

## Viktiga designbeslut

- **302 och inte 301** — 301 cachas permanent i webbläsaren, omöjliggör ändring av target_url
- **Inga IP-adresser i clicks** — GDPR, enbart link_id + referer + tidsstämpel
- **magic link** — inget lösenord, token är engångsbricka (used_at sätts direkt)
- **Tokens** — `purpose='verify'` kopplas till link_id, `purpose='login'` har link_id=NULL
- **Rate limiting** — SQLite-tabellen `rate_limits`, max 5 req/timme per IP per action
- **URL-validering** — endast https, domän måste vara `*.svenskakyrkan.se`, inga query/fragment

## Miljövariabler (.env)

| Variabel | Beskrivning |
|----------|-------------|
| `DATABASE_PATH` | Sökväg till SQLite-fil, default `data/links.db` |
| `SMTP_HOST` | Lettermint: `smtp.lettermint.net` |
| `SMTP_PORT` | Default `587` |
| `SMTP_USER` | Lettermint-användarnamn |
| `SMTP_PASS` | Lettermint-lösenord |
| `MAIL_FROM` | Avsändaradress, t.ex. `link@svky.se` |
| `SECRET_KEY` | Signeringsnyckel för cookies (generera: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `BASE_URL` | Publik URL, t.ex. `https://svky.se` (används i mail-länkar) |

## Vanliga förändringar

**Lägga till ett nytt admin-flöde:**
1. Lägg till route i `app/routes/admin.py`
2. Lägg till template i `app/templates/admin/`
3. Länka från admin-navbar i `base.html` (`{% block admin_bar %}`)

**Ändra e-postinnehåll:**
Redigera `app/mail.py` — `skicka_verifieringsmail()` eller `skicka_loginmail()`

**Ändra URL-valideringsregler:**
Redigera `app/validation.py` — `validate_target_url()`

**Lägga till en ny reserverad kod:**
Redigera `app/config.py` — `RESERVED_CODES`

**Sätta admin-rättigheter:**
```bash
sqlite3 data/links.db "UPDATE users SET is_admin=1 WHERE email='din@epost.se';"
```

## Deployment

**Produktion (Hetzner):**
```bash
docker compose pull && docker compose up -d
```

**Lokal dev:**
```bash
docker compose -f docker-compose.dev.yml up
```

**CI/CD:** GitHub Actions bygger Docker-image på varje push.
- `main` → `:latest` + SHA-tagg
- annan branch → branch-namn som tagg  
- git-tagg `v1.2.3` → `:1.2.3`, `:1.2`, `:1`
- Image publiceras till `ghcr.io/armandur/svk-short`
