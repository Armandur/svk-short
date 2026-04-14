# P4 — Småplock

Små punkter som inte hör hemma i någon av de större filerna. Gör dem
när du ändå är inne i aktuella filen, eller samla ihop dem till en
"hygienkommitt" när du har 30 minuter över.

---

## 1. Case-insensitiv matchning i catch-all redirect

**Var:** `app/routes/public.py:902` (`redirect_code`)

**Problem:** `validate_code` lowercase:ar i POST-flödet, men
`redirect_code` jämför direkt mot `code` som den kommer från URL:en.
Om någon skapat en kod via admin med `secrets.token_hex` blir den alltid
lowercase, men en användare som skriver `svky.se/MyCode` får 404 trots
att `mycode` finns.

**Uppgift:** Lägg till `code = code.lower()` överst i funktionen.
Samma i `bundle_takeover_form` (`public.py:1091`), `takeover_form`,
och catch-all 404-handler (`main.py:73`).

**Klart när:**
- [x] Alla ställen som slår upp kod gör det case-insensitivt

---

## 2. `http://` vs `https://` i externa snabblänkar

**Var:** `app/routes/admin/snabblänkar.py:27`

**Problem:** `_validate_external_url` tillåter både `http` och `https`.
I resten av kodbasen krävs strikt `https` (via `validate_target_url`).
Är inkonsekvensen medveten eller en miss?

**Uppgift:** Bestäm policy:
- Om avsikten är "admin får lägga in vad som helst" → OK som det är,
  men lägg en kommentar som förklarar.
- Om avsikten är "alltid https" → ta bort `"http"` ur tillåten-listan.

**Klart när:**
- [x] Policyn är medveten och dokumenterad i en kommentar

---

## 3. Placeholder-Swish i default about-innehåll

**Var:** `app/database.py:192`

**Problem:** Default-texten innehåller `"070 000 00 00"`. Om admin
aldrig redigerat `about_content` visas detta som ett äkta nummer på
/om-sidan i prod. Kontrollera om det gäller för prod-databasen.

**Uppgift:**

1. Kolla prod: `sqlite3 data/links.db "SELECT value FROM site_settings WHERE key='about_content'"` — avviker från default?
2. Om default fortfarande visas, antingen:
   - Ta bort Swish-stycket helt ur defaulten
   - Ändra texten till `"[sätt upp ett riktigt nummer i /admin/om]"`
     så det är uppenbart att det är en platshållare.

**Klart när:**
- [ ] Prod-innehållet verifierat
- [x] Default-texten uppdaterad om den visas obearbetad någonstans

---

## 4. Rensa upp return-tuple i `_load_transfer_action`

**Var:** `app/routes/public.py:689`

**Problem:** Returnerar en 7-tuple där första elementet är
`("error", msg, status)` eller `("http", status)` eller `None`. Svårt
att läsa och lätt att missbruka.

**Uppgift:** Gör om till en liten dataklass eller två separata returer:

```python
from dataclasses import dataclass

@dataclass
class TransferLoadResult:
    data: dict
    rows: list[dict]
    bundle_rows: list[dict]
    is_bulk: bool

@dataclass
class TransferLoadError:
    message: str | None    # None = generisk HTTP-fel (404/400)
    status: int

def _load_transfer_action(token: str) -> TransferLoadResult | TransferLoadError:
    ...
```

Anropande route kan då skriva:
```python
result = _load_transfer_action(token)
if isinstance(result, TransferLoadError):
    if result.message:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": result.message},
            status_code=result.status)
    raise HTTPException(status_code=result.status)
```

**Klart när:**
- [ ] 7-tuple borta
- [ ] Transfer-flödet manuellt testat (accept, decline, idempotent
      replay, bulk + enskild)

---

## 5. Dubblett `_generate_code`?

**Var:** okänt — eventuellt `routes/public.py:32` + en kopia i
`routes/admin/links.py` eller `routes/user.py`.

**Uppgift:**

1. `grep -rn "def _generate_code\|token_hex(3)" app/`
2. Om det finns flera kopior: lyft till en gemensam helper (t.ex.
   `app/code_generator.py` eller samma fil där koden redan går att
   nå från båda sidorna).
3. Kombinera med P0 #4 (retry-cap + entropi) — gör det på en plats.

**Klart när:**
- [x] En enda implementation finns
- [x] Alla callers använder den

---

## 6. CSS-fil i /static har 315 rader

**Var:** `app/static/style.css`

**Bakgrund:** Inte stort nog att kräva uppdelning men värt att notera
att variabler/layout/komponenter ligger tillsammans. Om den växer
mycket till kan den delas upp (`base.css`, `components.css`, `admin.css`)
och inkluderas från templates. Gör inte nu — notering för framtiden.

**Klart när:**
- [x] (Ej prioriterat — bocka av utan åtgärd när du läst)

---

## 7. `bundle_takeover_requests.bundle_id` saknar CASCADE

**Var:** `app/database.py:270`

**Problem:** `bundle_takeover_requests(bundle_id)` refererar `bundles(id)`
utan `ON DELETE CASCADE`, medan `bundle_items` och `bundle_sections` har
det. Om en bundle raderas blir foreign key-constrainten överträdd.

**Uppgift:** Lägg till en migration som antingen:
- Adderar `ON DELETE CASCADE` (kräver tabellrekreation i SQLite — inte
  trivialt)
- Eller lägg till motsvarande SET NULL / CASCADE på applikationsnivå:
  radera `bundle_takeover_requests` explicit innan `DELETE FROM bundles`.

Det näst enklare alternativet är det pragmatiska valet.

**Klart när:**
- [x] Ingen FK-konflikt vid bundle-radering (manuellt test)

**Implementerat:** Pending `bundle_takeover_requests` raderas explicit när
en bundle inaktiveras (`admin_disable_bundle`). Bundles raderas aldrig ur
databasen — inaktivering är det enda permanenta tillståndet.
