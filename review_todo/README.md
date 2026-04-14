# svky.se — prioriterad review-todo

En genomgång av kodbasen gjordes 2026-04-14. Den här mappen innehåller
resultatet, uppdelat i filer per prioritet och område. Filerna är skrivna
för att kunna betas av en åt gången av Claude Code — varje uppgift har
tillräckligt med kontext (filer, radnummer, bakgrund) för att kunna
implementeras isolerat.

## Prioritetsskala

| Nivå | Betydelse |
|------|-----------|
| **P0** | Säkerhet. Bör fixas snart. |
| **P1** | Strukturella förbättringar som gör vidare arbete lättare. |
| **P2** | Datamodell / migrationer / SQLite-hygien. |
| **P3** | Kvalitet, DX, tooling, deployment-härdning. |
| **P4** | Småplock och kosmetika. |

## Arbetsordning

Ta en uppgift i taget, från P0 och uppåt. Varje fil listar uppgifter i
föreslagen inbördes ordning. Varje uppgift börjar med ett
`## N. Rubrik` och avslutas med ett tydligt "Klart när"-kriterium.

Inga uppgifter förutsätter tester — tester är uttryckligen **utanför
scope** för den här genomgången. Verifiera istället manuellt via
`docker compose -f docker-compose.dev.yml up` + browser.

## Kontext och bakgrund

- Projektet kör som singleton-instans bakom Caddy med SQLite-fil, låg trafik
  (<100 req/s). Det betyder att vissa optimeringar (subqueries,
  rate-limit-index, WAL) har lägre värde än de annars skulle haft — men
  flera av dem är ändå billiga nog att göra.
- Endast en samling (`bundles.body_md`) finns i produktion i skrivande
  stund, ägd av maintainern. XSS-fixen i P0 är alltså **inte** breaking
  för riktiga användare.
- README:n positionerar projektet som återanvändbart för andra
  organisationer. Det är en bonus, inte ett huvudmål — prioritera det
  bara där det är billigt att ta med.
- Tidigare `/request` GET/POST är troligtvis dött — modern flödet går via
  `/bestall`. Verifiera innan radering.
- Se `CLAUDE.md` i repo-roten för kodbasens arkitekturöversikt.

## Filer

1. [P0-security.md](P0-security.md) — CSRF, XSS, SECRET_KEY, kodgenerering
2. [P1-structure.md](P1-structure.md) — splittra stora route-filer, rensa dubbletter
3. [P2-data.md](P2-data.md) — migrationer, `datetime.utcnow()`, WAL, index
4. [P3-quality.md](P3-quality.md) — CI-lint, redirect-mönster, tidszoner, Docker-härdning
5. [P4-smaplock.md](P4-smaplock.md) — småfixar och inkonsekvenser

## Status

Ingen uppgift påbörjad ännu. När du börjar på en uppgift, bocka av den
i respektive fil genom att byta `- [ ]` mot `- [x]` i "Klart när"-listan
och committa.
