# P0 — Säkerhet

Fyra säkerhetsrelaterade punkter. Ordningen är ungefärlig prioritering
inom P0; alla fyra bör göras.

---

## 1. Sanera user-genererad Markdown (stored XSS)

**Var:** `app/routes/public.py:943` (`bundle.body_md` i `/`+`{code}`-redirect),
och eventuellt `featured_intro_html` (`app/routes/public.py:65`),
`about_content` / `integritet_content` (admin-edit, lägre risk men sanera
för konsistens).

**Problem:** `markdown.markdown(...)` returnerar rå HTML utan sanering.
Resultatet skickas genom `Markup(...)` och renderas direkt i templaten.
Det betyder att en vanlig användare som äger en samling kan lägga in
`<script>` eller `<img src=x onerror=...>` i `body_md`, och det körs för
alla som öppnar samlingen. Stored XSS.

**Lösning:**

1. Lägg till `bleach` (eller `nh3`, som är betydligt snabbare och
   underhålls aktivt) i `requirements.txt`. `nh3` föredras om det
   fungerar för projektet — annars `bleach`.
2. Skapa en liten helper, t.ex. `app/markdown_safe.py`:

   ```python
   import markdown
   import nh3  # eller bleach
   from markupsafe import Markup

   _ALLOWED_TAGS = {
       "p", "br", "strong", "em", "ul", "ol", "li",
       "h1", "h2", "h3", "h4", "blockquote", "code", "pre",
       "a", "hr",
   }
   _ALLOWED_ATTRS = {"a": {"href", "title", "rel"}}

   def render_markdown(md_text: str) -> Markup:
       raw_html = markdown.markdown(md_text or "", extensions=["nl2br"])
       clean = nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)
       return Markup(clean)
   ```
3. Byt ut alla ställen där `markdown.markdown(...)` + `Markup(...)` används
   mot `render_markdown(...)`. Sök i kodbasen:
   - `app/routes/public.py` (bundle body, featured intro, om, integritet)
   - `app/routes/admin/settings.py` (ev. preview)
   - Eventuellt i `app/routes/user.py` för samlingsförhandsvisning
4. Manuell test: skapa en testsamling med `<script>alert(1)</script>` i
   `body_md`, verifiera att skriptet inte körs (renderas som `&lt;script&gt;`
   eller tas bort beroende på allow-list).

**Notera:** `body_md` används redan av maintainern för en riktig samling
— sanera så att rimliga markdown-element (rubriker, listor, länkar)
fortfarande fungerar. Testa på den befintliga samlingen efter ändringen.

**Klart när:**
- [x] `nh3` (eller `bleach`) tillagt i `requirements.txt`
- [x] Gemensam `render_markdown` införd och används överallt där markdown
      tidigare renderades med `Markup`
- [ ] Manuellt verifierat att `<script>` i `bundle.body_md` inte körs
- [ ] Manuellt verifierat att befintlig samling fortfarande ser korrekt ut

---

## 2. Failfast på saknad `SECRET_KEY`

**Var:** `app/auth.py:6`

**Problem:**
```python
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
```
Om någon glömmer sätta `SECRET_KEY` i prod blir alla session-cookies,
CSRF-tokens, takeover-tokens och transfer-tokens trivialt förfalskningsbara.
Uppstart ska inte lyckas tyst i det läget.

**Lösning:**

I `app/config.py` (inte `auth.py`, så csrf.py inte längre behöver importera
från auth.py — se P1 #5):

```python
import os
import sys

SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
BASE_URL: str = os.environ.get("BASE_URL", "http://localhost:8000")

if not SECRET_KEY:
    if BASE_URL.startswith("https://"):
        sys.exit("SECRET_KEY saknas — vägrar starta i HTTPS-läge.")
    SECRET_KEY = "dev-secret-change-in-production"
    import warnings
    warnings.warn("SECRET_KEY saknas — använder dev-default. Sätt SECRET_KEY i .env.")
```

Uppdatera `auth.py` och `csrf.py` att importera `SECRET_KEY` från `config.py`.

**Klart när:**
- [x] `SECRET_KEY` flyttad till `config.py` med failfast-logik
- [x] `auth.py` och `csrf.py` importerar den därifrån
- [ ] Verifierat att `BASE_URL=https://... SECRET_KEY= uvicorn ...` avbryts
      med tydligt felmeddelande
- [ ] Verifierat att dev-läget fortfarande funkar utan `SECRET_KEY`
      (med warning)

---

## 3. CSRF-tokens bundna till sessionen (eller medvetet borttagna)

**Var:** `app/csrf.py:9`

**Problem:** `_serializer.dumps("csrf")` producerar en sträng som bara
beror på `SECRET_KEY` och tid. Det betyder:
- Samma token fungerar för *alla användare*
- Giltig i 24 h oavsett vem
- Skyddar i praktiken bara mot blinda cross-origin POSTs utan
  JavaScript — vilket `SameSite=Lax` på sessionscookien redan gör.

Det är "CSRF-teater" — ser säkert ut, ger inget.

**Lösning (föredragen):** Bind token till användarens session.

1. I `app/auth.py`: utöka sessionscookien att innehålla ett random
   `csrf_secret`:
   ```python
   def create_session_cookie(user_id: int, csrf_secret: str | None = None) -> str:
       return _serializer.dumps({
           "user_id": user_id,
           "csrf_secret": csrf_secret or secrets.token_urlsafe(16),
       })
   ```
2. Anropare (login, verify) genererar ett `csrf_secret` vid cookie-skapande.
3. För utloggade användare (endast `/login`-formuläret och `/bestall`-
   formuläret utan inloggning) genereras istället en slumpmässig
   `csrf_secret` som lagras i en separat, kortlivad cookie (t.ex.
   `csrf_anon`, httponly, samesite=lax, max_age=3600).
4. `generate_csrf_token(secret)` signerar `secret` (med salt `"csrf"`).
5. `validate_csrf_token(token, secret)` avkodar och jämför.
6. Jinja global `csrf_token` byts ut mot en funktion som tar `request`
   och hämtar rätt secret.

**Alternativ lösning (om ovan känns för stort):** ta bort CSRF-fältet
helt och dokumentera i en kommentar i `csrf.py` att skyddet i stället
förlitar sig på `SameSite=Lax` + att alla state-changing endpoints
använder POST. Då är det åtminstone *medveten* säkerhet i stället för
sken.

**Rekommendation:** Gör den föredragna lösningen — det är inte värt att
lämna ett säkerhetsfält som ser skyddande ut men inte gör något.

**Klart när:**
- [x] Token bundet till sessionens `csrf_secret`
- [x] Utloggade formulär får en anonym csrf-cookie
- [ ] Alla befintliga formulär fungerar efter omstart + omlogg
- [ ] Verifierat manuellt att en token från en användare inte kan
      återanvändas från en annan användare

---

## 4. Code-generation: bredare entropi + retry-cap

**Var:** `app/routes/public.py:32` (`_generate_code`)

**Problem:**
```python
def _generate_code(db) -> str:
    while True:
        code = secrets.token_hex(3)  # 6 hex chars = 24 bitar
        existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
        if not existing:
            return code
```

- `secrets.token_hex(3)` = 24 bitar ≈ 16,7M koder.
- Vid några tusen länkar växer kollisionsfrekvensen snabbt (födelsedags-
  paradoxen: vid ~5000 länkar är chansen ~0,15% per generering).
- Oändlig loop utan cap. Om DB-index är trasig eller tabellen mot förmodan
  fylls så hänger begäran i stället för att fela.
- Kollar inte mot `bundles.code` — om en bundle har samma kod krockar det
  senare.

**Lösning:**

```python
_MAX_ATTEMPTS = 10

def _generate_code(db) -> str:
    for _ in range(_MAX_ATTEMPTS):
        code = secrets.token_urlsafe(5)[:7].lower()  # ~40 bitar entropi
        # token_urlsafe kan innehålla '_' och '-' — kontrollera mot
        # validate_code om det fortsatt ska vara giltigt format
        if not re.match(r"^[a-z0-9-]+$", code):
            continue
        link_exists = db.execute("SELECT 1 FROM links WHERE code=?", (code,)).fetchone()
        bundle_exists = db.execute("SELECT 1 FROM bundles WHERE code=?", (code,)).fetchone()
        if not link_exists and not bundle_exists:
            return code
    raise RuntimeError("Kunde inte generera en unik kod efter 10 försök")
```

Överväg i stället en dedikerad alphabet (base32 utan ambig tecken,
`23456789abcdefghjkmnpqrstuvwxyz`, 30 tecken → 5 tecken ≈ 24,5 bitar,
7 tecken ≈ 34 bitar) för att undvika homoglyfer. Välj 6–7 tecken som
balans mellan entropi och läsbarhet.

**Klart när:**
- [x] Retry-cap införd
- [x] Genererad kod kolliderar inte med bundles
- [x] Entropin höjd (minst ~30 bitar)
- [ ] Manuell test: skapa 20 länkar i rad och verifiera att ingen krashar
