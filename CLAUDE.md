# svky.se — Kodbasbeskrivning för Claude

## Vad projektet är

Intern URL-förkortare för Svenska kyrkan. Anställda beställer kortlänkar via ett formulär, verifierar via e-post (magic link), och kan logga in för att hantera sina egna länkar. En admin kan övervaka och moderera alla länkar.

Exempelflöde: `POST /bestall` → verifieringsmail → `GET /verify/<token>` (bekräftelsesida) → `POST /verify/<token>` → länken är aktiv → `GET /<kod>` → 302 till `https://www.svenskakyrkan.se/...`

## Stack

- **Python 3.12 + FastAPI** (ASGI, uvicorn)
- **SQLite** via `sqlite3` i standardbiblioteket — synkront, ingen ORM
- **Jinja2** templates (medföljer FastAPI)
- **itsdangerous** för signerade session-cookies och tokens
- **smtplib** för e-post via Lettermint SMTP
- **Caddy** som reverse proxy i produktion (auto-TLS)

## Filstruktur

```
app/
  main.py          # FastAPI-app, lifespan, middleware, mountar routes, exception handlers
  config.py        # Delade konstanter: BASE_URL, LinkStatus (IntEnum), RESERVED_CODES
  database.py      # init_db(), get_db() contextmanager, hela SQL-schemat + migrationer
  auth.py          # Session-cookies (skapa/läsa/avkoda), get_current_user(), tokens
  deps.py          # Gemensamma FastAPI-beroenden: get_user_or_redirect(),
                   #   get_admin_or_redirect(), check_rate_limit(),
                   #   user_allows_any_domain(), user_allows_external_urls()
  mail.py          # Alla e-postfunktioner — skicka_verifieringsmail(), skicka_loginmail()
                   #   m.fl. (11 funktioner totalt), inline HTML med SMTP via Lettermint
  validation.py    # validate_target_url(), validate_code(), validate_email()
                   #   — returnerar felmeddelande (str) eller None
  csrf.py          # generate_csrf_token(), validate_csrf_token() via itsdangerous
  templating.py    # Jinja2-instans som pekar på app/templates/
  routes/
    auth.py        # GET/POST /login, GET /auth/<token>, GET /logout
    public.py      # GET /, GET /bestall, POST /bestall, GET /verify/<token>,
                   #   GET /<code> (catch-all redirect), om/integritet, transfer-action,
                   #   bundle-takeover-requests, bundle-display
    user.py        # GET /mina-lankar, POST /mina-lankar/<id>/update, /deactivate,
                   #   /request-transfer, /request-transfer-all,
                   #   POST /mina-samlingar (skapa/redigera/sektioner/items)
    admin/         # Admin-paket — varje fil hanterar ett ansvarsområde:
      __init__.py  #   Kombinerar submodulernas routers under prefix /admin
      links.py     #   /admin/links, /admin/links/create, /admin/links/<id> + actions
      users.py     #   /admin/users, /admin/users/<id>/toggle-*, transfer-all, login-link
      bundles.py   #   /admin/bundles, /admin/bundles/<id> + update/disable/transfer
      takeovers.py #   /admin/takeover-requests, /admin/takeover-action/<token>,
                   #   /admin/bundle-takeover-requests (approve/reject)
      snabblänkar.py # /admin/snabblänkar — featured links på startsidan
      settings.py  #   /admin/om, /admin/integritet — markdown-redigering
      stats.py     #   /admin/stats — klick/sidvisnings/samlingsstatistik
      helpers.py   #   pending_takeover_count() — intern hjälpfunktion
  static/
    style.css      # All delad CSS (variabler, layout, komponenter) — monteras på /static
  templates/
    base.html      # Bas-template: header, nav, footer, {% block scripts %}
    index.html     # Startsida med snabblänkar
    bestall.html   # Beställningsformulär (fliken länk + fliken samling)
    login.html     # Magic link-login
    login_sent.html# "Inloggningslänk skickad"
    my_links.html  # Användarens egna länkar och samlingar
    error.html     # Generell felsida
    404.html       # Kod hittades inte
    admin/         # Admin-templates (links.html, users.html, bundles.html, stats.html m.fl.)
```

## Link-statusar (app/config.py: LinkStatus)

`LinkStatus` är en `IntEnum` — jämför fritt mot heltal eller mot konstanterna.

| Värde | Konstant | Betydelse |
|-------|----------|-----------|
| 0 | `PENDING` | Skapad, väntar på e-postverifiering |
| 1 | `ACTIVE` | Aktiv, omdirigerar |
| 2 | `DISABLED_ADMIN` | Avaktiverad av admin |
| 3 | `DISABLED_OWNER` | Avaktiverad av ägare |

## Gemensamma beroenden (app/deps.py)

Importera alltid härifrån — definiera inte lokala kopior i route-filerna:

```python
from app.deps import (
    get_user_or_redirect,     # Kräver inloggad användare, kastar 302 annars
    get_admin_or_redirect,    # Kräver admin, kastar 302 annars
    check_rate_limit,         # check_rate_limit(db, ip, action) → bool
    user_allows_any_domain,   # user_allows_any_domain(email) → bool
    user_allows_external_urls,# user_allows_external_urls(email) → bool
)
```

## Viktiga designbeslut

- **302 och inte 301** — 301 cachas permanent i webbläsaren, omöjliggör ändring av target_url
- **Inga IP-adresser i clicks** — GDPR, enbart link_id + referer + tidsstämpel
- **magic link** — inget lösenord, token är engångsbricka (used_at sätts direkt)
- **Engångslänkar i e-post är skanner-säkra** — alla e-postlänkar som ändrar tillstånd har en GET-handler som *bara* renderar en bekräftelsesida och en POST-handler med CSRF-kontroll som utför den faktiska åtgärden. Det gäller `/verify/<token>`, `/auth/<token>`, `/transfer-action/<token>`, `/mina-samlingar/overlatelse/<token>` samt `/admin/takeover-action/<token>`. Mönstret förhindrar att e-postskannrar (Microsoft Safe Links, Outlook-förhandsvisning m.fl.) "bränner" engångs-tokens eller utför tysta ägarändringar genom att GET:a länken innan användaren hinner klicka. Nya engångslänkar *måste* följa samma mönster.
- **Tokens** — `purpose='verify'` kopplas till link_id, `purpose='login'` har link_id=NULL
- **Rate limiting** — SQLite-tabellen `rate_limits`, max 5 req/timme per IP per action, se `deps.check_rate_limit()`
- **URL-validering** — endast https, domän måste vara `*.svenskakyrkan.se`, inga query/fragment
- **CSRF** — alla POST-formulär kräver `csrf_token`-fält; valideras med `validate_csrf_token()`

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
1. Skapa en ny fil i `app/routes/admin/`, t.ex. `app/routes/admin/reports.py`
2. Definiera `router = APIRouter()` och lägg till dina routes
3. Importera och inkludera i `app/routes/admin/__init__.py`: `from . import reports` + `router.include_router(reports.router)`
4. Lägg till template i `app/templates/admin/`
5. Länka från admin-navbar i `base.html` (`{% block admin_bar %}`)

**Lägga till ett nytt användar-flöde:**
- Lägg till route i `app/routes/user.py`
- Skydda med `user = get_user_or_redirect(request)` (från `app.deps`)

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
