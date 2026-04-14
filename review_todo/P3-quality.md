# P3 — Kvalitet, DX, deployment

Sju uppgifter. Billiga var för sig, tillsammans gör de kodbasen
märkbart mer professionell utan att ändra beteende.

---

## 1. Lägg till lint/format i CI

**Var:** `.github/workflows/docker.yml`

**Bakgrund:** CI bygger och publicerar Docker-image men gör ingen
statisk kontroll. Ruff är så snabbt att det inte finns någon anledning
att inte köra det.

**Uppgift:**

1. Skapa `.github/workflows/lint.yml`:
   ```yaml
   name: Lint
   on:
     push:
       branches: ["**"]
     pull_request:
   jobs:
     ruff:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/ruff-action@v3
           with:
             args: "check app/"
         - uses: astral-sh/ruff-action@v3
           with:
             args: "format --check app/"
   ```
2. Skapa `ruff.toml` (eller `[tool.ruff]` i `pyproject.toml`) i repo-roten:
   ```toml
   line-length = 100
   target-version = "py312"

   [lint]
   select = ["E", "F", "W", "I", "UP", "B", "SIM"]
   ignore = ["E501"]  # line length kontrolleras separat av formattern

   [lint.per-file-ignores]
   "app/mail.py" = ["E501"]  # långa HTML-strängar
   ```
3. Kör `ruff check app/` lokalt och fixa de uppenbara varningarna
   (oanvända imports, gamla typannotationer etc.). Commit:a de fixen
   separat från workflow-tillägget.

**Klart när:**
- [ ] `ruff.toml` finns
- [ ] CI kör ruff på varje push och blockerar merge vid fel
- [ ] `ruff check app/` passerar rent

---

## 2. Snyggare redirect från dependencies

**Var:** `app/deps.py:24, 32`

**Bakgrund:**
```python
raise HTTPException(status_code=302, headers={"Location": "/login"})
```
Fungerar men är ett antimönster — `HTTPException` är tänkt för *fel*.
Resulterar också i en tom response-body vilket vissa klienter kan tolka
konstigt.

**Uppgift:**

1. Skapa en egen exception:
   ```python
   # app/deps.py
   class RedirectRequired(Exception):
       def __init__(self, location: str):
           self.location = location
   ```
2. Kasta `RedirectRequired("/login")` från `get_user_or_redirect` och
   `get_admin_or_redirect`.
3. Registrera en exception handler i `app/main.py`:
   ```python
   from app.deps import RedirectRequired
   from fastapi.responses import RedirectResponse

   @app.exception_handler(RedirectRequired)
   async def _redirect_required(request, exc: RedirectRequired):
       return RedirectResponse(url=exc.location, status_code=303)
   ```
4. Använd 303 (See Other) för POST-redirects — säkrare än 302 som vissa
   klienter ompostar.

**Klart när:**
- [ ] `RedirectRequired` finns och används
- [ ] `HTTPException(status_code=302, …)` borta ur kodbasen
- [ ] Inloggningsredirect fungerar för både GET och POST

---

## 3. Tidszoner i UI

**Var:** alla templates som visar datum — `my_links.html`,
`admin/links.html`, `admin/stats.html`, `admin/users.html`, etc.

**Bakgrund:** Datumen är UTC men renderas som råa ISO-strängar eller
formatteras utan tz-konvertering. Användarna är i Sverige (CET/CEST).

**Uppgift:**

1. Lägg till ett Jinja-filter i `app/templating.py`:
   ```python
   from datetime import datetime
   from zoneinfo import ZoneInfo

   _STHLM = ZoneInfo("Europe/Stockholm")

   def sthlm_datetime(value) -> str:
       if not value:
           return ""
       if isinstance(value, str):
           value = datetime.fromisoformat(value)
       if value.tzinfo is None:
           value = value.replace(tzinfo=ZoneInfo("UTC"))
       return value.astimezone(_STHLM).strftime("%Y-%m-%d %H:%M")

   templates.env.filters["sthlm"] = sthlm_datetime
   ```
2. Använd i templates: `{{ link.created_at | sthlm }}`.
3. Gå igenom templates och ersätt rådatumsvisning. Kör `grep -rn
   "created_at\|last_used_at\|last_login" app/templates/`.

**Klart när:**
- [ ] Filtret finns och är registrerat
- [ ] Alla användarvisade datum renderas i Europe/Stockholm
- [ ] Internlogik (audit_log, tokens) behöver inte ändras

---

## 4. Dockerfile: kör som non-root + healthcheck

**Var:** `Dockerfile`, `docker-compose.yml`

**Uppgift:**

1. I `Dockerfile`:
   ```dockerfile
   FROM python:3.12-slim
   RUN apt-get update && apt-get install -y --no-install-recommends \
         sqlite3 curl && rm -rf /var/lib/apt/lists/*
   RUN useradd --create-home --shell /bin/bash appuser
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY app/ ./app/
   RUN mkdir -p data && chown -R appuser:appuser /app
   USER appuser
   HEALTHCHECK --interval=30s --timeout=3s \
       CMD curl -fsS http://localhost:8000/ || exit 1
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```
2. Överväg ett dedikerat `/healthz`-endpoint som inte loggar `page_views`
   (`app/main.py:47` — `/` är i `_TRACKED_PATHS`, healthchecken
   skulle annars inflatera statistiken):
   ```python
   @app.get("/healthz")
   async def healthz():
       return {"ok": True}
   ```
   Uppdatera HEALTHCHECK att peka på `/healthz`.

**Klart när:**
- [ ] Imagen kör som non-root
- [ ] Healthcheck definierad
- [ ] `/healthz` finns och räknas inte som page view
- [ ] `docker compose up` startar utan behörighetsfel mot `/app/data`

---

## 5. Mail-fel loggas i stället för sväljs

**Var:** ca 10 ställen som har `try: skicka_X() except MailError: pass`

**Bakgrund:** När ett mail misslyckas får varken användaren eller
admin veta det (förutom i vissa flöden där `mail_ok` visas på sidan).
Vid verklig drift är det extremt svårt att diagnostisera "jag fick
inget mail".

**Uppgift:**

1. Byt varje `except MailError: pass` mot:
   ```python
   except MailError:
       log.exception("Kunde inte skicka %s till %s", "typ", to)
   ```
2. Välj ett logger-namn per route-modul (`log = logging.getLogger(__name__)`).
3. Överväg att stänga cirkeln för admin-notifieringar: om *ingen* admin
   nås, lagra en `failed_notification`-rad i audit_log så admin ser det
   nästa gång hen loggar in.

**Klart när:**
- [ ] Ingen `except MailError: pass` utan logrop kvar
- [ ] Loggnivån är `error` eller `exception`
- [ ] Manuellt test: sätt fel SMTP-lösenord och verifiera att loggen
      visar ett begripligt felmeddelande

---

## 6. `ALLOWED_EMAIL_DOMAIN` som env-variabel

**Var:** `app/validation.py:6`

**Bakgrund:** README:n positionerar projektet som återanvändbart. Att
kräva kodändring för att byta tillåten mailderdomän är ett onödigt
hinder.

**Uppgift:**

1. Flytta till `config.py`:
   ```python
   ALLOWED_EMAIL_DOMAIN: str = os.environ.get("ALLOWED_EMAIL_DOMAIN", "svenskakyrkan.se")
   ```
2. Importera i `validation.py`.
3. Lägg till i `.env.example` och `README.md`.
4. Uppdatera README-avsnittet "Restrict to your email domain" att peka
   på env-variabeln i stället för kodfilen.

**Klart när:**
- [ ] Konstanten läses från env
- [ ] `.env.example` uppdaterad
- [ ] README-avsnittet uppdaterat

---

## 7. Döp om `snabblänkar.py`

**Var:** `app/routes/admin/snabblänkar.py`

**Bakgrund:** Enda filen i projektet med icke-ASCII. Fungerar på
Linux/macOS men kan strula på Windows, vissa CI-system och äldre
editors. Resten av kodbasen är engelska filnamn.

**Uppgift:**

1. Döp om till `app/routes/admin/featured.py` (eller `quicklinks.py`).
2. Uppdatera importen i `app/routes/admin/__init__.py`.
3. URL:en `/admin/snabblänkar` kan behållas (endpoint-dekoreringen är
   sträng, inte filnamn) — det är bara filen som ändras.

**Klart när:**
- [ ] Filen heter `featured.py` eller `quicklinks.py`
- [ ] `admin/__init__.py` importerar den
- [ ] `/admin/snabblänkar` fungerar fortfarande i dev
