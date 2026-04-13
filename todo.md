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
- Startsidan delas i två sektioner:
  1. **Snabblänkar** — rutnät eller lista med featuade kortlänkar (titel, kod, ev. ikon)
  2. **Beställ ny länk** — befintligt formulär nedanför (eller bakom en "Beställ länk"-knapp)

**Alternativ**

- Separat `featured_links`-tabell med eget namn/beskrivning/ikon, frikopplad från `links`. Ger admin mer kontroll (t.ex. bättre titel utan att ändra länkens interna not).
- Sorterbar via drag-and-drop i admin (`sort_order`-kolumn).

**Mockup:** `mockups/homepage-quicklinks.html`

---

### Idé 2: Bundles — `/hbg` öppnar en linklista

> "Möjlighet att skapa bundles. /hbg går till en lista av länkar"

Tanken: en bundle är en kortlänk som inte pekar till en enskild URL utan till en curatedd sida med flera länkar. Perfekt för en arbetsplats, en enhet eller ett projekt. Exempel: `svky.se/hbg` → "Viktiga länkar för Härnösands stift".

**Databasschema (tillägg)**

```sql
CREATE TABLE bundles (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,      -- samma valideringsregler som links.code
    name        TEXT NOT NULL,             -- "Härnösands stift"
    description TEXT,                      -- valfri undertext
    owner_id    INTEGER REFERENCES users(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bundle_items (
    id          INTEGER PRIMARY KEY,
    bundle_id   INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,             -- visningsnamn på länken
    url         TEXT NOT NULL,             -- valfri https-URL (ej begränsat till sk.se)
    icon        TEXT,                      -- emoji eller kortnamn, t.ex. "📅" eller "calendar"
    description TEXT,                      -- kort beskrivning (valfri)
    sort_order  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bundles_code ON bundles(code);
CREATE INDEX IF NOT EXISTS idx_bundle_items_bundle ON bundle_items(bundle_id);
```

**Routes**

| Route | Beskrivning |
|---|---|
| `GET /<code>` | Kolla bundles *före* links — om koden matchar en bundle, rendera bundle-sidan |
| `GET /admin/bundles` | Admintabell med alla bundles |
| `GET /admin/bundles/new` | Skapa ny bundle |
| `GET /admin/bundles/<id>` | Redigera bundle och dess items |
| `POST /admin/bundles/<id>/items` | Lägg till/redigera item |
| `DELETE /admin/bundles/<id>/items/<item_id>` | Ta bort item |

**Flöde vid `GET /<code>`**

```python
# I public.py, BEFORE the vanliga link-lookup:
bundle = db.execute("SELECT * FROM bundles WHERE code=?", (code,)).fetchone()
if bundle:
    items = db.execute(
        "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
        (bundle["id"],)
    ).fetchall()
    return templates.TemplateResponse("bundle.html", {"bundle": bundle, "items": items, ...})
```

**RESERVED_CODES:** `"bundle"` bör läggas till i `config.py`.

**Beställarflöde (framtida):** Inloggade användare kan skapa egna bundles (liknande länkbeställning). Tills vidare: endast admin.

**Mockup:** `mockups/bundle-page.html`

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

**Mockup:** Se kiosk-fliken i `mockups/bundle-page.html`

---

## Övrig backlog (från kortlink-plan.md)

- [ ] Exportera klickstatistik som CSV
- [ ] Webhook/notifiering till admin vid ny beställning
- [ ] Visa audit_log i admingränssnittet per länk och per användare
- [ ] Paginering i admintabellen (redan klar?)
- [ ] DMARC-rapport-parsing för e-postleveransövervakning
