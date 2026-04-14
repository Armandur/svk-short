# Idéer och backlog

## Inkomna idéer från anställd

---

### Idé 1: Startsidan som kurerade snabblänkar

> "Startsidan på den där domänen är en lista med kurerade snabblänkar"

Tanken: när man besöker `svky.se` möts man inte bara av ett beställningsformulär utan av en handplockad lista med viktiga interna länkar — en slags "startskärm" för vanliga resurser i Svenska kyrkan.

**Teknisk approach**

- Lägg till kolumn `is_featured INTEGER DEFAULT 0` på `links`-tabellen (+ migrering)
- Admin bockar i "Visa på startsidan" i länkens detaljvy
- `GET /` hämtar aktiva, featuade länkar: `SELECT * FROM links WHERE is_featured=1 AND status=1 ORDER BY sort_order, created_at`
- Startsidan visar **enbart snabblänkarna** + en tydlig CTA-knapp till `/bestall`
- Beställningsformuläret **flyttas till `/bestall`** (se Idé 2 nedan för detaljer om den sidan)

**Alternativ**

- Separat `featured_links`-tabell med eget namn/beskrivning/ikon, frikopplad från `links`. Ger admin mer kontroll (t.ex. bättre titel utan att ändra länkens interna not).
- Sorterbar via drag-and-drop i admin (`sort_order`-kolumn).

**RESERVED_CODES:** `"bestall"` läggs till om den routen används.

**Mockup:** `mockups/homepage-quicklinks.html`

---

### Idé 2: Bundles — `/hbg` öppnar en linklista

> "Möjlighet att skapa bundles. /hbg går till en lista av länkar"

Tanken: en bundle är en kortlänk som inte pekar till en enskild URL utan till en curatedd sida med flera länkar. Perfekt för en arbetsplats, en enhet eller ett projekt. Exempel: `svky.se/hbg` → "Viktiga länkar för Härnösands stift". Bundles visas inte på startsidan — de är fristående sidor man delar direkt.

**Vem kan skapa bundles?** Alla inloggade användare — samma modell som för kortlänkar. Varje användare äger sina egna bundles och kan redigera dem. Admin kan se och moderera alla bundles.

**Databasschema (tillägg)**

```sql
CREATE TABLE bundles (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,      -- samma valideringsregler som links.code
    name        TEXT NOT NULL,             -- "Härnösands stift"
    description TEXT,                      -- valfri undertext
    theme       TEXT NOT NULL DEFAULT 'rich', -- 'rich' eller 'compact'
    owner_id    INTEGER REFERENCES users(id),
    status      INTEGER DEFAULT 1,         -- 1=aktiv, 2=avaktiverad av admin
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Sektioner som egna rader — ger oberoende sortering av rubrikerna
CREATE TABLE bundle_sections (
    id          INTEGER PRIMARY KEY,
    bundle_id   INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,             -- "Administration"
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE bundle_items (
    id          INTEGER PRIMARY KEY,
    bundle_id   INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
    section_id  INTEGER REFERENCES bundle_sections(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,             -- visningsnamn på länken
    url         TEXT NOT NULL,             -- valfri https-URL (ej begränsat till sk.se)
    icon        TEXT,                      -- emoji, t.ex. "📅" (valfritt, används i rich-tema)
    description TEXT,                      -- kort beskrivning (valfritt, används i rich-tema)
    sort_order  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Bundle-överlåtelser (separat tabell — tokens-tabellen är knuten till links)
CREATE TABLE bundle_transfers (
    id          INTEGER PRIMARY KEY,
    bundle_id   INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
    to_email    TEXT NOT NULL,
    token       TEXT UNIQUE NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    used_at     DATETIME                   -- sätts när mottagaren accepterar
);

CREATE INDEX IF NOT EXISTS idx_bundles_code ON bundles(code);
CREATE INDEX IF NOT EXISTS idx_bundle_items_bundle ON bundle_items(bundle_id);
CREATE INDEX IF NOT EXISTS idx_bundle_sections_bundle ON bundle_sections(bundle_id);
CREATE INDEX IF NOT EXISTS idx_bundle_transfers_token ON bundle_transfers(token);
```

**Routes**

| Route | Fil | Beskrivning |
|---|---|---|
| `GET /bestall` | `routes/public.py` | Beställningssidan — typval kortlänk/bundle |
| `POST /bestall` | `routes/public.py` | Ta emot kortlänk-beställning (befintligt `/request`-flöde, nu på ny route) |
| `GET /<code>` | `routes/public.py` | Kolla bundles *före* links — om koden matchar en bundle, rendera bundle-sidan |
| `GET /my-links` | `routes/user.py` | Visar **både** kortlänkar och länksamlingar i två sektioner |
| `POST /my-bundles` | `routes/user.py` | Skapa ny bundle (kräver inloggning) |
| `GET /my-bundles/<id>` | `routes/user.py` | Redigera bundle: namn, tema, sektioner, items |
| `POST /my-bundles/<id>/update` | `routes/user.py` | Spara namn/beskrivning/tema |
| `POST /my-bundles/<id>/items` | `routes/user.py` | Lägg till item |
| `POST /my-bundles/<id>/items/<item_id>/delete` | `routes/user.py` | Ta bort item |
| `POST /my-bundles/<id>/items/<item_id>/move` | `routes/user.py` | Flytta upp/ned (sort_order) |
| `POST /my-bundles/<id>/sections` | `routes/user.py` | Skapa ny sektion |
| `POST /my-bundles/<id>/sections/<sec_id>/rename` | `routes/user.py` | Byt namn på sektion |
| `POST /my-bundles/<id>/sections/<sec_id>/delete` | `routes/user.py` | Ta bort sektion (items får `section_id=NULL`) |
| `POST /my-bundles/<id>/deactivate` | `routes/user.py` | Stäng av bundle (ägarens val) |
| `POST /my-bundles/<id>/request-transfer` | `routes/user.py` | Begär överlåtelse till annan ägare |
| `GET /my-bundles/transfer/<token>` | `routes/user.py` | Mottagare accepterar överlåtelse (loggar in om ej inloggad) |
| `GET /admin/bundles` | `routes/admin.py` | Admin: alla bundles, ägare, status |
| `GET /admin/bundles/<id>` | `routes/admin.py` | Admin: detaljvy för en bundle |
| `POST /admin/bundles/<id>/update` | `routes/admin.py` | Admin: ändra namn/beskrivning/tema |
| `POST /admin/bundles/<id>/disable` | `routes/admin.py` | Admin: avaktivera bundle |
| `POST /admin/bundles/<id>/transfer` | `routes/admin.py` | Admin: tvångsöverflytta bundle till ny ägare |
| `GET /admin/snabblänkar` | `routes/admin.py` | Admin: hantera kurerade snabblänkar på startsidan |
| `POST /admin/snabblänkar/add` | `routes/admin.py` | Lägg till kortlänk på startsidan |
| `POST /admin/snabblänkar/remove` | `routes/admin.py` | Ta bort kortlänk från startsidan |
| `POST /admin/snabblänkar/reorder` | `routes/admin.py` | Uppdatera sorteringsordning |
| `POST /admin/snabblänkar/settings` | `routes/admin.py` | Spara inställningar (max antal) |

**Sektioner i bundle:** Hanteras via den separata `bundle_sections`-tabellen (se schema ovan). Items pekar på en sektion via `section_id`. Om en sektion tas bort sätts `section_id=NULL` på dess items — de hamnar i en implicit "Utan sektion"-grupp längst ned.

**Flöde: lägga till items i en befintlig bundle**

Bundle-items är *inte* kortlänkar — de behöver ingen verifiering. Ägaren av bundles har redan verifierat sin identitet vid registrering. Items är bara poster i en visningslista. Därför gäller:

- **Ny länk** → ägaren anger titel + valfri https-URL direkt. Ingen e-postverifiering.
- **Lägg till en av mina kortlänkar** → välj bland egna aktiva `links`. Kortlänkens kod används som URL i itemet (`https://svky.se/<code>`), och `note` föreslås som titel. Ingen ny verifiering.
- **Vill ägaren ha en ny svky.se/xxx-länk som inte finns ännu?** → separerat flöde: gå till `/bestall`, beställ kortlänken normalt, verifiera via e-post, kom sedan tillbaka och lägg till via "Lägg till en av mina kortlänkar". Bundle-editorn föreslår en länk till `/bestall` om söklistan är tom.

**Flöde: överlåtelse av bundle**

Liknar kortlänk-överlåtelse men använder `bundle_transfers`-tabellen (inte `tokens`, som är knuten till `links`):

```
POST /my-bundles/<id>/request-transfer  { to_email }
  → INSERT INTO bundle_transfers (bundle_id, to_email, token)
  → Skicka mail till to_email med länk till /my-bundles/transfer/<token>
  → Visa bekräftelse: "Förfrågan skickad, mottagaren bekräftar via e-post"

GET /my-bundles/transfer/<token>
  → Hämta bundle_transfer WHERE token=? AND used_at IS NULL
  → Hitta eller skapa user med to_email
  → Skapa session för mottagaren (magic-login — de loggas in automatiskt)
  → UPDATE bundles SET owner_id = mottagare WHERE id = ?
  → UPDATE bundle_transfers SET used_at = now() WHERE id = ?
  → Logga i audit_log
  → Redirect till /my-links med flashmeddelande
```

Ägaren behåller bundles tills mottagaren klickar länken. Acceptlänken loggar in mottagaren direkt om de inte redan är inloggade — ingen separat login-step behövs. Admin kan tvångsöverflytta direkt via `POST /admin/bundles/<id>/transfer` utan token-flow.

**Flöde vid `GET /<code>`**

```python
# I public.py, BEFORE den vanliga link-lookup:
bundle = db.execute(
    "SELECT * FROM bundles WHERE code=? AND status=1", (code,)
).fetchone()
if bundle:
    items = db.execute(
        "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
        (bundle["id"],)
    ).fetchall()
    return templates.TemplateResponse("bundle.html", {
        "bundle": bundle, "items": items, ...
    })
```

**Kodvalidering:** Bundle-koder går igenom exakt samma validering som `links.code`. Vid skapande kontrolleras mot både `links`-tabellen och `bundles`-tabellen för att undvika kollisioner.

**RESERVED_CODES:** `"bundle"`, `"my-bundles"` och `"bestall"` läggs till i `config.py`.

**De två temana**

| Tema | Beskrivning |
|---|---|
| `rich` | Hero-header med gradient, ikontiles med beskrivningstext, sektionsrubriker. Visuellt, bra för MDM-kiosk. |
| `compact` | Minimalt sidhuvud, ren länklista utan ikoner/beskrivningar. Snabbt att skumma, bra för delade linklänkar i mail/chatt. |

Temat sparas i `bundles.theme` och kan ändras av ägaren. Båda temana stöder kiosk-läge (`?kiosk=1`).

**Beställningssida (`/bestall`)**

Eftersom startsidan inte längre rymmer formuläret samlas allt skapande på `/bestall`. Sidan presenterar ett typval:

- **Kortlänk** — befintligt flöde: e-post, mål-URL, kod, not → verifieringsmail. Kräver **inte** inloggning — vem som helst med en giltig SK-adress kan beställa.
- **Länksamling (bundle)** — namn, beskrivning, kod, tema → skapas direkt. Kräver **inloggning** (till skillnad från kortlänkar). Är användaren inte inloggad visas login-formuläret före bundle-formuläret.

Bundle-formuläret inkluderar en items-editor redan vid skapandet:
- **"+ Ny länk"** — miniforulär med titel, valfri https-URL, ikon (emoji), beskrivning. Ingen verifiering — bundles ägs av skaparen som redan är verifierad.
- **"+ Lägg till en av mina kortlänkar"** — sök bland inloggad användares aktiva kortlänkar i `links`-tabellen. Kortlänkens kod används som URL, `note` föreslås som titel.

Befintlig `/request`-route behålls som redirect till `/bestall` för bakåtkompatibilitet.

**Snabblänkar (startsidan)**

Databasändringar: lägg till kolumner på `links`-tabellen:
- `is_featured INTEGER DEFAULT 0`
- `featured_title TEXT` — eget visningsnamn på startsidan (om NULL används `note`)
- `featured_icon TEXT` — emoji
- `featured_sort INTEGER DEFAULT 0`

Admin-route `GET /admin/snabblänkar` hanterar vilka kortlänkar som visas, deras ordning, visningsnamn och ikon. Om en länk avaktiveras (`status != 1`) döljs den automatiskt från startsidan utan att `is_featured` ändras.

**Nya templates som behöver skapas** (under `app/templates/`):

| Fil | Route | Beskrivning |
|---|---|---|
| `bestall.html` | `GET /bestall` | Typval + kortlänk-formulär + bundle-formulär (login-gate om utloggad) |
| `bundle.html` | `GET /<code>` (bundle) | Den publika bundle-sidan, stöder `?theme=` och `?kiosk=1` |
| `my_bundle_detail.html` | `GET /my-bundles/<id>` | Redigera bundle: inställningar, items, sektioner, överlåt |
| `admin/bundles.html` | `GET /admin/bundles` | Admintabell med alla bundles |
| `admin/bundle_detail.html` | `GET /admin/bundles/<id>` | Admin-detaljvy: info, redigering, tvångsöverlåtelse, audit-logg |
| `admin/snabblänkar.html` | `GET /admin/snabblänkar` | Hantera kurerade snabblänkar på startsidan |

**Mockups:** `mockups/bundle-page.html` (tema-switcher + kiosk-toggle), `mockups/order-page.html` (typval + båda formulär, utloggad/inloggad-toggle), `mockups/my_bundle_detail.html`, `mockups/admin_snabblänkar.html`, `mockups/admin_bundles.html`, `mockups/admin_bundle_detail.html`

---

### Idé 3: Bundle snygg via MDM fullscreen

> "Bundle blir snygg genom MDM fullscreen"

Tanken: bundle-sidan är designad för att fungera som en fullscreen-app på iPad/iPhone, utplacerad via MDM (Jamf, Intune, etc.) som en hemskärmsgenväg. Utan browser-chrome, snyggt rutnät med stora tappbara ytor.

**Teknisk approach**

Lägg till PWA-metataggar i `bundle.html`-templaten:

```html
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{{ bundle.name }}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#193d7a">
```

**Layoutprinciper för kiosk-läge**

- Rutnät: 2 kolumner på telefon, 3–4 på platta
- Tile-storlek: minst 120×100px — fingervänliga ytor (≥ 44px touch target)
- Dölj header/footer i standalone-läge via CSS:
  ```css
  @media (display-mode: standalone) {
    .site-header, .site-footer { display: none; }
    body { padding-top: env(safe-area-inset-top); }
  }
  ```
- Alternativt: detektera standalone med JS och visa ett minimalistiskt in-page header istället
- Färger: `--blue-dark` som bakgrund i hero-headern, guld (`--gold`) som accentfärg
- Optionellt: `?kiosk=1` i URL tvingar kiosk-läge (användbart för MDM-länken)

**MDM-konfiguration (Jamf-exempel)**

Lägg in `https://svky.se/hbg?kiosk=1` som en Web Clip med ikonen från Svenska kyrkan — rullas ut till alla iPads på enheten automatiskt.

**Mockup:** Se kiosk-fliken och tema-switcher i `mockups/bundle-page.html`

---

## Övrig backlog (från kortlink-plan.md)

- [ ] Exportera klickstatistik som CSV
- [ ] Webhook/notifiering till admin vid ny beställning
- [ ] Visa audit_log i admingränssnittet per länk och per användare
- [ ] Paginering i admintabellen (redan klar?)
- [ ] DMARC-rapport-parsing för e-postleveransövervakning
- [ ] Drag-and-drop-sortering i `/admin/snabblänkar` (samma mönster som övriga drag-and-drop-vyer i admin)
- [ ] Admin-vy för reserverade koder: visa både systemets `RESERVED_CODES` och användartillagda reservationer i samma lista, med möjlighet att lägga till/ta bort egna reservationer utan att behöva skapa kortlänkar för dem. Vid beställning ska användaren få tydlig info om att koden är reserverad och inte kan användas — övertagande ska heller inte kunna begäras för reserverade koder. (Kontrollera även att nuvarande beställningsflöde redan informerar om `RESERVED_CODES` — om inte, fixa det samtidigt.)
