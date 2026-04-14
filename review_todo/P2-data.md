# P2 — Datamodell, migrationer, SQLite-hygien

Fem uppgifter. Viktigast är #1 (migrationer) och #2
(`datetime.utcnow()`-deprecation). Resten är billiga men inte akuta
givet låg trafik.

---

## 1. Versionerade migrationer (`schema_version`-tabell)

**Var:** `app/database.py:203` (`_migrate()`)

**Bakgrund:** Nuvarande `_migrate()` kör `ALTER TABLE` inuti try/except
och tystar *alla* `OperationalError` — även sådana som beror på syntaxfel,
typfel eller NOT NULL-konflikter, inte bara "kolumnen finns redan". Det
är skört; en trasig migration kan se ut att ha lyckats. Dessutom finns
ingen version i databasen, så det går inte att veta vilka migrationer
som körts.

**Lösning:**

1. Lägg till en tabell (i `init_db`-script eller i migration 001):
   ```sql
   CREATE TABLE IF NOT EXISTS schema_version (
       version INTEGER PRIMARY KEY
   );
   ```
2. Skriv migrationerna som rena funktioner:
   ```python
   def _mig_001_baseline(conn: sqlite3.Connection) -> None:
       """Lägg till is_featured/featured_* på links och
       allow_*-flaggor på users."""
       conn.execute("ALTER TABLE links ADD COLUMN is_featured INTEGER DEFAULT 0")
       conn.execute("ALTER TABLE links ADD COLUMN featured_title TEXT")
       ...

   def _mig_002_bundles(conn): ...
   def _mig_003_bundles_body_md(conn): ...
   def _mig_004_featured_external(conn): ...
   def _mig_005_drop_referer_columns(conn): ...

   MIGRATIONS = [
       (1, _mig_001_baseline),
       (2, _mig_002_bundles),
       (3, _mig_003_bundles_body_md),
       (4, _mig_004_featured_external),
       (5, _mig_005_drop_referer_columns),
   ]
   ```
3. `init_db()` kör:
   ```python
   current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
   for version, fn in MIGRATIONS:
       if version > current:
           fn(conn)
           conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
           conn.commit()
   ```
4. För befintliga produktionsdatabaser (som redan har alla kolumner):
   märk alla migrationer som körda vid första uppstart. Enklast är att
   låta varje migration själv vara idempotent — fortsätt med try/except
   kring `ALTER TABLE` *men bara* för `OperationalError` som matchar
   "duplicate column name" (kontrollera felsträngen). Det tillåter en
   smidig övergång från nuvarande best-effort till versionerad.

   Alternativt: låt första uppstart efter deploy sätta `schema_version`
   till senaste numret om `links.is_featured` redan finns (dvs. slutför
   baseline stills "har redan skett").

5. Notera i en kommentar i `database.py`: **nya migrationer läggs till
   sist i listan, aldrig infogas mellan existerande**.

**Klart när:**
- [ ] `schema_version`-tabell finns
- [ ] Befintlig migrationslogik omvandlad till numrerade funktioner
- [ ] Tom SQLite startar upp korrekt (alla migrationer körs)
- [ ] Prod-DB (med alla existerande kolumner) startar upp utan fel och
      landar på rätt version

---

## 2. Byt `datetime.utcnow()` → `datetime.now(timezone.utc)`

**Var:** 15–20 förekomster i `app/routes/public.py`, `app/routes/user.py`,
`app/routes/auth.py`, `app/routes/admin/*`, `app/database.py`.

**Bakgrund:** `datetime.utcnow()` är deprecated i Python 3.12 och kommer
tas bort i framtida version. Den returnerar dessutom en naiv datetime
vilket gör tz-aware operationer tvetydiga.

**Uppgift:**

1. Sök alla förekomster:
   ```
   grep -rn "datetime.utcnow" app/
   ```
2. Byt till `datetime.now(timezone.utc)`. Lägg till `timezone` i
   imports där det behövs.
3. Eftersom databasen lagrar datumen som ISO-strängar (naiva), behåll
   det formatet genom att göra `.replace(tzinfo=None).isoformat()` där
   strängen lagras i DB.

   **ELLER** — och det här är renare — börja lagra tz-aware ISO-strängar
   (`"2026-04-14T12:00:00+00:00"`). SQLite:s `datetime('now', …)`-
   uttryck förväntar sig naiva strängar; kontrollera att inget jämför
   `datetime('now')` med tz-aware via strängjämförelse (kan ge fel
   resultat eftersom `+00:00` sorteras annorlunda).

   **Försiktigt alternativ:** håll kvar naiva UTC-strängar i DB för att
   inte bryta befintliga jämförelser; använd bara tz-aware för beräkningar
   i Python, konvertera vid lagring.

**Klart när:**
- [ ] Inga `datetime.utcnow()` kvar i `app/`
- [ ] Befintlig DB fortsätter fungera (cleanup, expires_at-jämförelser)

---

## 3. Aktivera SQLite WAL-mode

**Var:** `app/database.py:8` (`get_connection`)

**Bakgrund:** I WAL-mode blockerar inte läsare skribenter och vice
versa. För en applikation med långa connections per request ger det
märkbart bättre samtidighet. Två rader.

**Uppgift:** Lägg till i `get_connection()`:

```python
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")  # snabbare, fortfarande crash-safe i WAL
    return conn
```

`journal_mode = WAL` är persistent — det räcker egentligen att sätta det
en gång per databasfil. Att sätta det vid varje anslutning är ofarligt
(no-op om redan WAL) och säkerställer det för nya deployer.

**Klart när:**
- [ ] PRAGMA-raderna tillagda
- [ ] Efter första uppstart i dev: verifiera `-wal` och `-shm`-filer
      finns bredvid `links.db`

---

## 4. Index på `rate_limits` och eventuellt `clicks`

**Var:** `app/database.py:76-120`

**Bakgrund:** `check_rate_limit` kör `SELECT COUNT(*) FROM rate_limits
WHERE ip=? AND action=? AND created_at > ?`. Inget index finns på
`(ip, action, created_at)`, så varje POST gör en linjär skanning. Vid
låg trafik är det försumbart, men indexet är gratis.

**Uppgift:**

1. Lägg till i `init_db`:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_rate_limits_lookup
       ON rate_limits(ip, action, created_at);
   ```
2. Om #1 (versionerade migrationer) gjorts: lägg detta som en ny
   migration i stället.
3. Samma gäller potentiellt `clicks(clicked_at)` för statistik-queries.
   Kontrollera `admin/stats.py` för att se om det skulle hjälpa.

**Klart när:**
- [ ] Index på `rate_limits` finns
- [ ] Index på `clicks(clicked_at)` finns om stats använder det

---

## 5. Token-cleanup per `purpose` med vettiga TTL

**Var:** `app/database.py:338` (`run_periodic_cleanup`)

**Bakgrund:** Nuvarande raderar tokens där `expires_at < now - 30 days`.
Det betyder att en login-token (som *expire*:ar efter 1 timme) ligger
kvar i 30 dagar och 1 timme. Kommentaren "Utgångna tokens raderas efter
30 dagar" är inte fel men inte heller informativ.

**Uppgift:** Överväg en per-purpose-policy, t.ex.:

```python
def run_periodic_cleanup() -> None:
    with get_db() as db:
        # Login- och verify-tokens: radera direkt när de är använda eller
        # utgångna (de fyller ingen funktion efter det).
        db.execute(
            """DELETE FROM tokens
                WHERE purpose IN ('login', 'verify', 'delete_account')
                  AND (used_at IS NOT NULL OR expires_at < datetime('now'))"""
        )
        # Transfer- och takeover-action-tokens: behåll 7 dagar för att
        # kunna visa "redan hanterad"-sidan om användaren klickar sent.
        db.execute(
            """DELETE FROM tokens
                WHERE expires_at < datetime('now', '-7 days')"""
        )
        db.execute(
            "DELETE FROM rate_limits WHERE created_at < datetime('now', '-1 day')"
        )
```

Notera: de flesta transfer/takeover-tokens är signerade med itsdangerous
och lagras inte i `tokens`-tabellen alls. Dubbelkolla vilka purposes som
faktiskt finns innan du finputsar policyn.

**Klart när:**
- [ ] Policyn per purpose bestämd och dokumenterad i docstringen
- [ ] Cleanup kör utan fel vid uppstart
- [ ] Manuellt verifierat att `tokens`-tabellen inte växer obegränsat
      vid upprepade login-försök
