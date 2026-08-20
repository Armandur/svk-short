# Backlog Export

## [P2][done] [svky] Alla besökare delar rate limit-hink bakom Caddy - kritiskt nu när spärren fungerar

Mätt 2026-08-20 mot lokal instans, uvicorn 0.30.6. ÅTGÄRDAD i commit ef9337a + d444edb, träder i kraft vid nästa docker compose up -d.

PROBLEMET: uvicorns ProxyHeadersMiddleware har trusted_hosts='127.0.0.1' som default (verifierat i installerad källkod, config.py:333 läser FORWARDED_ALLOW_IPS). Caddy når appen från containernätets 172.x-adress, alltså inte 127.0.0.1, så X-Forwarded-For ignorerades och request.client.host blev Caddys adress för samtliga besökare.

Mätt före: två anrop med olika X-Forwarded-For från en klient utanför 127.0.0.1 gav EN distinkt rad i rate_limits. Sex olika personer som begärde inloggningslänk från samma klientadress: den sjätte fick 'För många försök'. Sex olika användare som beställde varsin kortlänk: den sjätte spärrad.

Varför det inte märkts: fram till commit fee21f0 räknade check_rate_limit alltid noll (se TASK-1434), så ingen spärr har haft effekt.

ÅTGÄRDAT PÅ TVÅ SÄTT (Rasmus val 2026-08-20):
1. ef9337a - inloggningsspärren nycklas på e-postadressen i stället för IP. Mätt efter: sex olika personer från samma adress släpps igenom, samma adress sju gånger spärras på det sjätte försöket. Överlåtelseflödena nycklas på user:<id> sedan 3f7d5b2.
2. d444edb - FORWARDED_ALLOW_IPS='*' i docker-compose.yml, så uvicorn litar på Caddys X-Forwarded-For och de fortfarande IP-nycklade flödena (anonym beställning, återutskick, takeover) räknar per besökare. Mätt efter: två X-Forwarded-For ger två distinkta hinkar. Värdet förutsätter att svky-tjänsten inte publicerar någon port utåt - den gör inte det i compose-filen.

KVAR ATT GÖRA I DRIFT: verifiera efter deploy att ip-kolumnen i prods rate_limits innehåller riktiga besökaradresser och inte en enda 172.x-adress.

ÖVRIGT: Caddyfile saknar request_body max_size, så ingen body-gräns finns framför appen. Längdgränserna i 2196a50 sitter i valideringen och täcker fälten.

- ID: `01M0GF47B53YC81762BDPN6F44`
- Type: bug
- Actor: human:rasmus

---

## [P2][done] [svky] Rate limit-räknaren räknade alltid noll - ingen spärr har fungerat

Upptäckt under arbetet med TASK-1425, mätt mot lokal instans 2026-08-20.

check_rate_limit jämförde created_at mot datetime.now(UTC).isoformat() (2026-08-20T19:49:46) medan raden skrivs av CURRENT_TIMESTAMP som '2026-08-20 20:49:27'. SQLite jämför som strängar och mellanslag (0x20) sorterar före T (0x54), så varje rad föll utanför fönstret och COUNT blev alltid 0.

Utfall: ingen av spärrarna har spärrat - login, resend, beställning, takeover och kontoborttagning har alla varit ospärrade sedan de infördes. Granskningen 2026-08-20 antog att rate limiting fungerade och bedömde TASK-1425 som lågallvarlig avvikelse mot ett fungerande mönster.

Mätt före fix: 7 anrop i rad gav 7 genomsläpp. Efter fix (jämförelse i SQL mot datetime('now', '-1 hour')): 5 genomsläpp, 429 på det sjätte.

Fixad i commit fee21f0.

- ID: `01M0GF3F328V60MHD3GPHV7HRD`
- Type: bug
- Actor: human:rasmus

---

## [P2][done] [svky] POST /admin/takeover-action saknar admin-kontroll som GET-varianten har

Behörighetsbypass, BEKRÄFTAD MED MÄTNING mot lokal instans 2026-08-20 (kontrollfall isolerar orsaken).

GET /admin/takeover-action/{token} (takeovers.py:243) anropar get_admin_or_redirect(request) på första raden. POST-varianten (takeovers.py:309, takeover_action) gör INTE det - den validerar bara CSRF och utför sedan ägarbytet via _handle_link/bundle_takeover_action. Enda av ~25 admin-POST-routes som saknar admin-koll.

Roll: någon utan admin-session som fått tag i en giltig takeover-action-token (itsdangerous-signerad, 7 dagars giltighet, mailas till admin - kan läcka via mail-scanner-förhandsvisning, vidarebefordrat mail, mailrelä-logg, referrer).

Repro (mätt): seedade offer + länk 'offermal' (owner_id=1) + pending takeover till angripare. (1) Hämtade anon-CSRF från GET /login utan session. (2) KONTROLL A: GET action-URL utan admin -> 303 till /login (kollen fungerar där). (3) KONTROLL B: POST med fel CSRF -> 403 (CSRF enda grinden). (4) EXPLOIT: POST med anon-CSRF, ingen admin-session -> 303 approved, länkens owner_id ändrades 1->2 (angripare). Efter: takeover_request=approved.

Utfall: icke-admin kan godkänna/avslå en överlåtelse (flytta ägarskap av kortlänk/samling) helt utan inloggning - kringgår den behörighetsgräns koden själv har på GET.

Fix: lägg get_admin_or_redirect(request) först i takeover_action, analogt med GET. Kolla samtidigt bundle-motsvarigheten.

Klart när: POST /admin/takeover-action utan admin-session ger redirect till /login (som GET), och exploit-reprot ovan flyttar INTE längre ägandet. Verifiera: kör om repro-scriptet, KONTROLL A-mönstret ska nu gälla även POST.

- ID: `01M0GCNTYWT0MGV0EDAGF14BH3`
- Type: bug
- Actor: ai:claude-code

---

## [P2][done] [svky] Dev-instansen är publicerad på 0.0.0.0:8000 med inloggning över oskyddad HTTP

Uppmätt utifrån 2026-08-08 från ubuntu-ai, under TASK-1088.

Port 8000 svarar på http://204.168.252.40:8000/ med dev-instansen - /healthz ger ok true, och framsidan skiljer sig från https://svky.se, alltså inte produktionen. /login svarar 200. Ett lösenord som skrivs där går i klartext över internet, utan TLS.

ss -tlnp på servern visar docker-proxy på 0.0.0.0:8000, alltså publicerad på alla gränssnitt.

EN BRANDVÄGGSREGEL LÖSER DET INTE. Docker skriver egna iptables-regler i DOCKER-kedjan, som konsulteras före ufw:s INPUT. En ufw deny 8000 hade sett rätt ut i ufw status och ändå släppt igenom trafiken. Det är den vanligaste fällan på en Docker-värd med ufw, och skälet att kontrollen måste ske i publiceringen.

ATT GÖRA: bind publiceringen till loopback i docker-compose.dev.yml, alltså 127.0.0.1:8000:8000. Ska dev nås från annan maskin är tailnet-adressen rätt väg nu när servern är ansluten: 100.74.163.31:8000:8000.

VERIFIERA UTIFRÅN, inte i konfigfilen: porten ska sluta svara från en maskin utanför tailnetet. En kontroll som bara läser compose-filen bevisar inte att containern startades om.

Kontrollera samma sak för produktionsstacken - 80 och 443 ska vara publicerade, men ingen apps egen port.

- ID: `01KZGT78WXDQED2S3EM99AANNW`
- Type: bug
- Actor: ai:claude-code

---

## [P3][done] [svky] Inga längdgränser på target_url, note, reason, samlingsnamn/beskrivning (DoS/svällning)

Saknad gräns, kodläst + grep-verifierad 2026-08-20. Body-size = verifieringsfråga (Caddy).

Bara 'code' längdvalideras (validation.py:69, 2-60). validate_target_url (validation.py:22-64) och validate_email (validation.py:8) saknar len()-kontroll. Fält target_url, note, reason, bundle name/description, sektionsnamn m.fl. lagras oavkortat i SQLite TEXT. Ingen body-size-middleware i main.py (den enda middlewaren, rad 63, räknar bara sidvisningar).

Roll: inloggad användare (roll 2). Anon /bestall är rate-limitad 5/h, men inloggad har ingen gräns på /mina-samlingar-skapande - stora textfält kan mätta db och tunga sidrenderingar (fälten renderas upprepat på startsida/listor).

VERIFIERINGSFRÅGA: Caddy i prod kan ha request_body max_size som mildrar. Går inte att avgöra ur repot - läs prod-Caddyfile.

Klart när: en rimlig maxlängd (t.ex. target_url 2048, fritext några kB) valideras vid indata och avvisas med tydligt fel. Verifiera: POST med överlångt fält ger valideringsfel, inte lagring.

- ID: `01M0GCPJP2TK0MAN08QPTFYGWX`
- Type: improvement
- Actor: ai:claude-code

---

## [P3][done] [svky] Anslut svky.se-servern till tailnetet, som slojda-server

Rasmus 2026-08-08, uppkommet ur hemslojd TASK-833: när uppetidssonden ändå ska in på servern är det lika bra att servern blir nåbar över tailnet först.

FÖRLAGAN är slojda-server, se docs/ci-cd.md i ~/workspace/hemslojden/anmälningssystem:
- Taggad nod, tag:slojda-server. Taggen är inte kosmetika - taggade noder har ingen nyckelutgång, medan en användarägd nod tyst faller ur tailnetet efter ett halvår.
- Vanlig OpenSSH över tailnet, inte Tailscale SSH.
- Publik port 22 stängd EFTER att SSH över tailnet verifierats.
- Hetzner-brandväggen exponerar bara 80 och 443.
- Rootinloggning avstängd, administratören har sudo med interaktivt lösenord.

ATT GÖRA:
1. Lägg till tag:svky-server i tailnetets ACL med en ägare, annars går taggen inte att använda i en auth key.
2. Skapa en engångs-auth key, förautentiserad och taggad. Inte ephemeral - servern ska stå kvar.
3. Installera tailscale på servern och anslut med hostname svky-server.
4. Verifiera SSH över tailnet från ubuntu-ai INNAN något stängs.
5. Först därefter: stäng publik port 22 i Hetzner-brandväggen.

KLART NÄR: svky-server syns i tailscale status utan nyckelutgång, SSH fungerar över tailnet, och publik 22 är stängd.

BLOCKERAR: TASK-1086, uppetidssonden på den servern.

- ID: `01KZGSQHA2VVA4JQP3YA4HWP14`
- Type: chore
- Actor: ai:claude-code

---

## [P3][todo] [svky] Staging och produktion som på slöjda.de, i stället för manuell git pull

Rasmus 2026-08-08. I dag driftsätts svky.se för hand: git pull och docker compose up -d direkt på servern. Det fungerar, men det finns ingen miljö att prova en ändring i innan den möter användarna, och ingen grind mellan att något byggts och att det körs skarpt.

Förlagan finns i anmälningssystemet, se docs/ci-cd.md och docs/miljoer.md i ~/workspace/hemslojden/anmälningssystem. Delarna där:

- Två Compose-stackar med skilda nät, volymer, databaser och hemligheter. Staging på egna namn (dev.<domän>) bakom samma Caddy.
- Push till main bygger, signerar imagen med cosign och driftsätter STAGING automatiskt. Produktionen står stilla.
- Produktionen får nya versioner bara genom promotion från en driftyta, med exakt signerad digest - servern verifierar signaturen själv före deploy.
- Deployvägen är ett forced command med tre validerade argument, så att kunna pusha till main inte är samma sak som att kunna köra kod på värden.
- Hemligheterna ligger utanför git-utcheckningen.

ATT AVGÖRA FÖRST: hur mycket av det som är motiverat här. svky.se är en kortlänkstjänst med mindre rörliga delar än anmälningssystemet, och hela driftytan med promotion-worker kan vara mer maskineri än tjänsten bär. Ett rimligt minimum vore staging plus signerad image och en manuell promotion, utan egen webbyta.

Notera att ett byte av driftsätt rör backupen och återläsningsrutinen också.

- ID: `01KZGSGBYM8RJ6NCKG5C1V3F6C`
- Type: feature
- Actor: ai:claude-code

---

## [P3][todo] [svky] SMTP-fel blockerar inloggning org-brett utan larm

app/mail.py:28-35 loggar och kastar MailError. Anropsställena (app/routes/auth.py:78-80, orders.py:88-90 och 372-374, transfers.py x4, takeovers.py x2) fångar och sätter mail_ok=False eller log.exception. Ingen larmar utåt.

Appbeteendet är korrekt - utskicksstatus ska inte läcka till klienten - men ett SMTP-avbrott (Lettermint nere, fel credentials) blockerar tyst all magic-link-inloggning och all länkverifiering för hela organisationen tills någon hör av sig.

Åtgärd: räkna misslyckade utskick och notifiera vid t.ex. över 5 fel per timme. Rate-limitera till en notis per timme enligt policyns avsnitt 6, annars skickar ett längre avbrott en notis per inloggningsförsök.

Samma mönster finns i kortspelshornan (chicago), troligen samma författarhand.

- ID: `01KYWKZPWDMY0EC4K8R5WYRYNP`
- Type: improvement
- Actor: ai:claude-opus-5

---

## [P3][todo] [svky] Larm när backupen på Hetzner slutar köra (backupen verifierad frisk)

README.MD:101 rekommenderar regelbunden backup ("e.g. daily cp") men det finns inget backupskript i repot, och det går inte att se utifrån om ett cron-jobb faktiskt kör på Hetzner-servern.

Detta är den enda punkten i hela ntfy-genomgången (2026-07-31) som rör förlust av produktionsdata. svky.se är i skarp drift och varje publicerad kortlänk i organisationen beror på databasen.

Kör `crontab -l` (och `systemctl list-timers`) på Hetzner-burken och avgör.
- Kör backupen: lägg en heartbeat på den. Hör ihop med TASK-658 (fas F i ntfy-retrofitten på projektet infra).
- Kör den inte: det är ett större problem än något annat som kom ut av genomgången, och behöver åtgärdas före allt notifieringsarbete.

- ID: `01KYWKZPQVRE56CQWC0Y8T251R`
- Type: chore
- Actor: ai:claude-opus-5

---

## [P4][done] [svky] request-transfer-endpoints saknar rate limit (intern mail-spam-vektor)

Inkonsekvent rate limiting, grep-verifierad 2026-08-20.

request_transfer (user/links.py:288), begar_overlatelse (user/bundles.py:625) och request_transfer_all (user/account.py:223) anropar INTE check_rate_limit. Övriga mail-utlösande flöden gör det (login, resend, order, delete_account, takeover, bundle-takeover). Verifierat: account.py:s 2 check_rate_limit-anrop gäller delete_account, inte request_transfer_all.

Roll: inloggad användare. Repro: skapa länk, POST request-transfer upprepat med olika to_email (@svenskakyrkan.se) - varje anrop mailar utan takt-spärr. Utfall: ospärrad intern skräppost mot kollegors adresser.

Allvar lågt (kräver konto, mottagare begränsad till org-domänen), men tydlig avvikelse från kodens eget mönster.

Klart när: de tre endpointsen anropar check_rate_limit som övriga mail-flöden. Verifiera: grep check_rate_limit i de tre funktionerna ger träff.

- ID: `01M0GCPJPAN1HDZ6MAD5YNR1MX`
- Type: improvement
- Actor: ai:claude-code

---

## [P4][todo] [svky] Uppetidssond mot slojda.de, och egen check för svky.se

Andra halvan av hemslojd TASK-833, som byggde sonden. Skriptet och systemd-enheterna finns i ~/workspace/infra/uppetidssond/ och är gemensamma - ingenting behöver skrivas här.

De två servrarna bevakar varandra: sonden på svky.se-servern hämtar slojda.de, och sonden på slojda.de-servern hämtar https://svky.se/healthz. Den här tasken är den andra riktningen.

ATT GÖRA:
1. Skapa en check i svky.se-projektet på healthchecks.io, period 300 s och grace 900 s. Nyckeln ligger i secrets.fish som HEALTHCHECKS_API_KEY_SVKY - API-nycklarna är per projekt.
2. Koppla larmkanaler. ntfy-topicen för svky finns inte än; följer konventionen svc_<tjanstnamn> enligt infras notifieringspolicy och provisioneras med ntfy-provision-skillen. E-post ska vara med som reservvag - ligger TERVO2 nere nar ingen ntfy-notis fram.
3. Installera sonden pa slojda.de-servern enligt READMEn, med SOND_MAL=https://svky.se/healthz och SOND_VANTAT_SVAR för ok-true.
4. Framkalla felet och verifiera hela larmvagen. Ett testmeddelande bevisar bara att curl fungerar.

KLART NAR: svky.se har en uppetidscheck som larmar pa samma tva kanaler, och felet ar framkallat en gang.

- ID: `01KZGSFCXPCKH624AAEYVFNTMG`
- Type: feature
- Actor: human:rasmus

---

