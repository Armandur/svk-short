# Migrationer

Schemat ändras med versionerade funktioner i `app/database.py`. `MIGRATIONS`
är listan, `schema_version` håller reda på vad som körts, och `_run_migrations`
kör det som saknas. Migrationerna körs av **appen vid uppstart**, i containern.

Att köra dem vid uppstart är rätt val här: en container, en SQLite-fil, ingen
samtidighet att skydda mot. Ett separat migrationssteg före appen blir
motiverat först om tjänsten lämnar SQLite eller kör flera repliker.

## Regeln: bakåtkompatibel en version

**En migration måste kunna köras utan att föregående version av appen slutar
fungera.**

Skälet är rollbacken i `drift/svky-promotera.sh`. Faller hälsokontrollen efter
en promotion byts appen tillbaka till föregående image - men den nya versionen
kan redan ha kört sin migration. Den gamla appen möter då ett nyare schema.

Skriptet upptäcker det och VÄGRAR rulla tillbaka när `schema_version` flyttat
sig, eftersom en tyst återgång vore fel svar. Följden är att en icke
bakåtkompatibel migration förvandlar ett misslyckat bygge till ett läge som
kräver en människa och en återläsning från dump. Regeln finns för att det ska
vara sällsynt.

## Expand/contract

Att ta bort eller byta namn på något görs i tre releaser:

1. **Expand.** Lägg till det nya. Skriv till både nytt och gammalt. Den gamla
   appen fungerar, för det gamla finns kvar.
2. **Migrera.** Läs från det nya. Sluta använda det gamla. Fortfarande
   bakåtkompatibelt, för kolumnen finns.
3. **Contract.** Ta bort det gamla. Nu, och först nu, är rollback förbi den
   här punkten inte längre säker.

Steg 3 är den enda som bryter regeln, och den ska göras när steg 2 legat i
drift ett tag - inte i samma release.

`_drop_col` finns och används (`_mig_005_drop_referer`). Den är alltså inte
förbjuden, men varje användning är ett steg 3.

## Regler för själva listan

- Nya migrationer läggs **alltid sist** i `MIGRATIONS`, aldrig infogas mellan
  existerande. Numret är identiteten, inte ordningen i filen.
- Varje funktion ska vara idempotent. `_alter` tolererar "duplicate column
  name" och `_drop_col` tolererar "no such column". Övriga fel propageras.
- `CREATE TABLE IF NOT EXISTS` och `CREATE INDEX IF NOT EXISTS` är redan
  idempotenta.

## Innan en migration som inte är bakåtkompatibel

1. Kontrollera att föregående version inte längre körs någonstans.
2. Kör den i staging först och låt den ligga.
3. Räkna med att en misslyckad promotion kräver återläsning. Promote-skriptet
   tar en backup med `integrity_check` före varje byte, och säger var den
   ligger när det vägrar rulla tillbaka.
