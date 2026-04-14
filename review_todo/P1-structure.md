# P1 — Strukturella förbättringar

Sex uppgifter som gör kodbasen lättare att underhålla. Gör dem innan
större funktionella tillägg — de minskar ytan som nästa ändring behöver
läsa.

---

## 1. Verifiera och ta bort döda `/request`-routes

**Var:** `app/routes/public.py:450` (`/request/check-code`),
`app/routes/public.py:468` (`POST /request`).

**Bakgrund:** `/request` verkar vara det gamla beställningsflödet från
innan `/bestall` fanns. Den renderar `index.html` med formulärfel —
vilket tyder på att `index.html` en gång hade ett inline-formulär. Den
nuvarande startsidan har inte det.

**Uppgift:**

1. Verifiera att `index.html` inte postar till `/request` (läs templaten).
2. Sök igenom kodbasen efter länkar till `/request` eller
   `/request/check-code`:
   ```
   grep -rn "request/check-code\|\"/request\"\|'/request'" app/
   ```
3. Om `/request` POST inte används: ta bort `request_link`-handlern
   (`public.py:468-576`). Behåll inte `_generate_code` eller liknande
   som duplikat; de finns redan.
4. `/request/check-code` (GET, JSON) — kolla om `bestall.html`
   fortfarande pollar den via JS. Om ja: behåll. Om nej: ta bort.
5. `/request/resend` (POST) — används från `pending.html`? Behåll om ja.

**Klart när:**
- [x] Döda routes borttagna
- [ ] `pending.html` och `bestall.html` fungerar som tidigare
- [ ] Inga trasiga länkar i templates

---

## 2. Splittra `routes/public.py` (1191 rader)

**Var:** hela `app/routes/public.py`

**Uppgift:** Dela upp enligt ansvarsområde. Inspireras av den
existerande `routes/admin/`-strukturen.

Ny struktur:
```
app/routes/
  public.py       # GET /, /om, /integritet, catch-all GET /{code}
  orders.py       # /bestall (GET+POST), /verify/<token> (GET+POST),
                  #   (ev. /request/resend om den behålls)
  takeovers.py    # /request/takeover (+ bundle-varianten),
                  #   båda GET och POST
  transfers.py    # /transfer-action/<token> (GET+POST) +
                  #   _load_transfer_action
```

Praktiska råd:
- `public.py` blir då kort och innehåller bara läs-endpoints + redirect.
- Registrera de nya routerna i `app/main.py` i samma ordning som tidigare
  (catch-all `/{code}` *sist*).
- Lämna `_generate_code` där `/bestall` hamnar (troligen `orders.py`).
- Flytta `_load_transfer_action` till `transfers.py`.

**Klart när:**
- [ ] Nya filer skapade
- [ ] `public.py` under ~250 rader
- [ ] Alla tidigare endpoints svarar på samma URLs
- [ ] Ingen import-cirkel
- [ ] Manuellt test: bestall → verify, login, takeover-request-flöde
      fungerar hela vägen

---

## 3. Splittra `routes/user.py` (1515 rader)

**Var:** hela `app/routes/user.py`

**Uppgift:** Lik P1 #2 men för user-filen.

Ny struktur:
```
app/routes/user/
  __init__.py     # router = APIRouter(); include submodules
  links.py        # /mina-lankar*, /mina-lankar/<id>/*, per-länk transfer,
                  #   request-transfer-all
  bundles.py      # /mina-samlingar/* (hela samlings-CRUD)
  account.py      # /mina-lankar/radera-konto*, /mina-lankar/export
```

Observera att nuvarande URLs innehåller `/mina-lankar` även för
exportfunktionen — behåll URL:erna exakt som de är, det är bara filerna
som flyttas.

**Klart när:**
- [ ] Ny paketstruktur
- [ ] Ingen fil över ~700 rader
- [ ] Alla tidigare endpoints svarar
- [ ] Manuellt test: skapa, redigera, inaktivera, överlåta, exportera

---

## 4. Extrahera duplikat "hämta mina länkar"-SQL

**Var:** `app/routes/user.py` — upprepas minst 5 gånger:
- `my_links` (~rad 33)
- `_render_error` i `request_transfer_all` (~rad 380)
- `update_link`-felväg (~rad 510)
- `request_transfer`-felvägar (~rad 600, ~rad 620, ~rad 645)

**Uppgift:** Lyft till två helpers i lämplig modul (t.ex.
`app/routes/user/_queries.py` efter P1 #3, eller `app/queries.py`):

```python
def fetch_user_links(db, user_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT l.id, l.code, l.target_url, l.status, l.note,
                  l.created_at, l.last_used_at,
                  (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count,
                  (SELECT b.id FROM bundles b WHERE b.code=l.code AND b.status=1 LIMIT 1) AS converted_bundle_id
             FROM links l
            WHERE l.owner_id=?
         ORDER BY l.created_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]

def fetch_user_bundles(db, user_id: int) -> list[dict]:
    ...  # motsvarande
```

Ersätt alla ställen som duplicerar SQL:en. Det ska minska `user.py` med
ett par hundra rader.

**Klart när:**
- [ ] Helpers finns på ett ställe
- [ ] Alla tidigare anrop ersatta
- [ ] Manuellt test: `/mina-lankar` visar samma kolumner som förut

---

## 5. Bryt ut `SECRET_KEY` från `auth.py` → `config.py`

**Var:** `app/auth.py:6`, `app/csrf.py:2`

**Bakgrund:** `csrf.py` importerar `SECRET_KEY` från `auth.py`. Det är
en oäkta modulberoende — `csrf` hör logiskt till `config`, inte `auth`.
Flytten låter också P0 #2 (failfast) landa på ett naturligt ställe.

**Uppgift:** Se P0 #2 — implementerar redan flytten. Om P0 #2 görs först,
bocka bara av den här.

**Klart när:**
- [x] `SECRET_KEY` definierad i `config.py`
- [x] `auth.py` och `csrf.py` importerar därifrån

---

## 6. Refaktorera mail.py — extrahera gemensam HTML-layout

**Var:** `app/mail.py` (841 rader, 11 funktioner)

**Bakgrund:** Varje funktion duplicerar samma `<table><tr><td>`-boilerplate,
färger och footer. Konsekvent uppdatering kräver 11 ändringar.

**Uppgift:** Välj en av två vägar:

### Alternativ A — Jinja2-templates (rekommenderat)

1. Skapa `app/templates/mail/_base.html` med layouten (header, logo,
   rund ram, footer). Använd `{% block %}`-struktur.
2. Skapa en template per mail-typ (`verifieringsmail.html`,
   `loginmail.html`, `overlatelseforfragan.html` etc.) som extends
   `_base.html`.
3. I `mail.py`, byt ut varje f-string-baserad HTML mot
   `templates.get_template("mail/verifieringsmail.html").render(...)`.
4. `_send()` förblir oförändrad.

### Alternativ B — Python-helper

1. Behåll inline-HTML men extrahera en gemensam funktion:
   ```python
   def _layout(*, title: str, intro_html: str,
               button_url: str | None = None,
               button_label: str | None = None,
               footer_html: str = "") -> str:
       ...
   ```
2. Varje `skicka_*`-funktion bygger bara sitt specifika innehåll och
   anropar `_layout()`.

Alternativ A är snyggare men kräver Jinja2-environment-åtkomst i
`mail.py`. Alternativ B är snabbare att genomföra men låter HTML:en bo
kvar i Python.

**Klart när:**
- [ ] En gemensam layout används av alla 11 funktioner
- [ ] `mail.py` är minst ~300 rader kortare
- [ ] Manuellt test: skicka minst tre olika mailtyper och kontrollera
      att de renderas korrekt i t.ex. Gmail eller en lokal mail-catcher

---

## 7. Städa döda funktioner i `auth.py`

**Var:** `app/auth.py:77-89` (`require_user`, `require_admin`)

**Bakgrund:** Projektet använder konsekvent `deps.get_user_or_redirect`
och `deps.get_admin_or_redirect`. `require_user`/`require_admin` i
`auth.py` används ingenstans — kvarleva från tidig version. `require_admin`
returnerar dessutom 403 medan `deps`-varianten returnerar 302 → /login,
vilket är inkonsekvent och riskerar bli fel använd av misstag.

**Uppgift:**

1. Bekräfta att de är oanvända: `grep -rn "require_user\|require_admin" app/`
2. Ta bort dem från `auth.py`.

**Klart när:**
- [x] Funktionerna borttagna
- [x] `grep` returnerar tomt
