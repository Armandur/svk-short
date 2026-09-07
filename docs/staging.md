# Staging på svky-servern

Staging ersätter den handstartade dev-stacken som tidigare nåddes på
`100.74.163.31:8000`. Miljön har produktionens säkerhet men ingen av dess
utgående verkningar: all e-post fångas av Mailpit och når aldrig en riktig
mottagare.

Underlaget till upplägget står i backlog-docen "Staging och promotion:
underlag från slöjda.de". TASK-1087 äger resten av kedjan.

## Nås bara över tailnet

Ingen publik DNS-post och inget ACME-certifikat. `tailscale serve` terminerar
TLS på tailnet-adressen med ett certifikat Tailscale ställer ut.

**Portarna 8443 och 8444, inte 443.** Produktionens Caddy publicerar
`443:443`, vilket binder ALLA gränssnitt på värden, tailnet inräknat. En
`tailscale serve` på 443 hade krockat med den, och en förfrågan mot
ts.net-namnet hade i bästa fall besvarats med produktionens certifikat.

## Första gången

```sh
cd ~/svk-short          # utcheckningen, samma som produktionen
                        # (katalognamnet är från före namnbytet och
                        #  byter inte av att repot gjorde det)
cp .env.staging.example .env.staging

# Skapa databaskatalogen SJÄLV, före första starten.
mkdir -p data-staging
```

**Katalogen måste finnas innan stacken startas.** Gör den inte det skapar
Docker den som `root:root`, och imagen kör som `appuser` (uid 1000, se
`USER appuser` i Dockerfile). Appen får då inte skriva och kraschar med
`sqlite3.OperationalError: unable to open database file` - ett fel som pekar
mot databasen när problemet är ägarskapet. Blev det ändå fel:
`sudo chown -R 1000:1000 data-staging`.

Fyll i `.env.staging`: en EGEN `SECRET_KEY` (`openssl rand -hex 32`), aldrig
produktionens. `BASE_URL` ska vara den adress serve publicerar, med `https`.
`SVKY_IMAGE` sätts i nästa steg. `ADMIN_EMAILS` med din adress ger dig
adminrätt direkt, utan en `UPDATE` i sqlite efter varje omstart.

Sätt upp serve en gång:

```sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8001
sudo tailscale serve --bg --https=8444 http://127.0.0.1:8025
sudo tailscale serve status
```

Slå **aldrig** på funnel för portarna. Det vore att lägga hela stagingen, och
hela dess brevlåda, på internet.

## Starta och byta version

Skriv digesten i `.env.staging` och kör med `--env-file`, så gäller den för
varje kommando i stället för bara det du råkar sätta variabeln på:

```sh
drift/svky-digest.sh latest          # skriv in resultatet som SVKY_IMAGE

STAGING="-p svky-staging -f docker-compose.staging.yml --env-file .env.staging"
docker compose $STAGING up -d
docker compose $STAGING ps
docker compose $STAGING logs -f svky
```

Utan `--env-file` faller varje anrop på `required variable SVKY_IMAGE is
missing a value`, eftersom compose bara läser `.env` automatiskt - och den
tillhör produktionen.

Filen är fristående, inte ett override-lager, och `-f` är därför obligatorisk.
Glöms den bort startar produktionsstacken i stället, vilket är högljutt fel
och inte tyst fel.

`SVKY_IMAGE` är obligatorisk med `:?`. Stacken vägrar starta odefinierad
hellre än att tyst dra `:latest` - hela poängen med staging är att veta exakt
vilken version som provades.

Varje sida i staging bär en gul markering överst, satt av `MILJO: staging`
i compose-filen. Den styr ingen spärr - att posten fångas beror på
`SMTP_HOST`, inte på markeringen. Glöms `MILJO` bort blir utseendet
produktionens, vilket är rätt håll att fela åt.

- Appen: <https://svky-server.ussuri-tawny.ts.net:8443>
- Brevlådan: <https://svky-server.ussuri-tawny.ts.net:8444>

## Att ta en provad version vidare till produktion

Läs vad staging FAKTISKT kör, inte vad du tror att den kör:

```sh
docker inspect --format '{{index .Config.Image}}' svky-staging-svky-1
```

Imagen bär också commiten den byggdes från, som en OCI-etikett. Frågan
"vilken kod kör vi" har alltså ett svar utan uppslagstabell, och samma
kommando fungerar på produktionens container:

```sh
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
  svky-staging-svky-1
```

Sätt SAMMA digest i produktionens `.env`, ersätt den rad som redan står där,
och driftsätt:

```sh
${EDITOR:-nano} .env                 # byt SVKY_IMAGE
docker compose config | grep 'image: ghcr'   # läs vad du faktiskt ska starta
docker compose pull && docker compose up -d

# Vänta in uppstarten. Kontrollen direkt efter up -d träffar ofta Caddy
# medan appen ännu startar, och svarar 502 - vilket ser ut som ett fel
# men bara är för tidigt frågat.
until curl -sf -o /dev/null https://svky.se/healthz; do sleep 1; done
echo "uppe"
```

Ingen ombyggnad. Samma image-lager som redan provats i staging startas i
produktionen.

`SVKY_IMAGE` är obligatorisk också i produktionen. Saknas den faller
kommandot vid inläsningen av compose-filen, alltså innan något ändras -
och det är en bättre utgång än att `:latest` tyst hämtar något ingen provat.
Compose läser `.env` av sig själv, så `--env-file` behövs inte här.

Kontrollen `docker compose config` finns med av en anledning: en redigerad
`.env` är det enda ledet i kedjan där ett handgrepp kan bli fel, och raden
visar vad som verkligen kommer att startas i stället för vad du tror att du
skrev.

### Verifiera signaturen först

```sh
drift/svky-verifiera.sh ghcr.io/armandur/svky.se@sha256:<digest>
```

Skriptet avvisar allt som inte är en digest, och kräver att signaturen är
utställd till vår workflowfil på `main`. **Att en image ligger i vårt registry
är inget bevis** - den som kan pusha dit kan pusha vad som helst.

Kontrollen görs på servern och inte bara i CI. CI som intygar åt sig självt är
ingen grind: den som kan ändra workflowen kan ändra intyget.

Cosign installeras en gång:

```sh
sudo curl -fsSL -o /usr/local/bin/cosign \
  https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
sudo chmod +x /usr/local/bin/cosign
```

Ingen nyckel lagras någonstans. Signeringen är nyckellös: CI byter GitHubs
kortlivade OIDC-token mot ett certifikat, och verifieringen kontrollerar att
certifikatet är utställt till exakt vårt repository, vår workflowfil och
`main`.

**Bilder byggda före 2026-09-07 har ingen signatur** och avvisas därför. Det
är rätt beteende, men betyder att en rollback till en gammal digest måste
göras med `docker compose` direkt, förbi skriptet, och med öppna ögon.

### Resten av kedjan

Detta är promotionens manuella form. Automatisk stagingdeploy och
en promotionsyta är steg 4 och 5 i TASK-1087 och finns inte än. Det betyder
att det ännu är en människa som avgör vad som går ut - men numera en människa
med en kontroll att luta sig mot.

## Databasen

`data-staging/` är stagingens egen katalog och ligger i `.gitignore`.

**Kopiera inte produktionsdatabasen hit.** svky lagrar anställdas
e-postadresser, och tjänsten har inget datainnehåll som kräver verklig data
för att prova en ändring. Seeda med påhittade adresser i stället.

Adminrätt sätts med `ADMIN_EMAILS` i `.env.staging` och gäller från nästa
uppstart. Den ger bara, tar aldrig ifrån - att stryka en adress degraderar
alltså ingen, avsättning sker i adminytan där den syns i audit-loggen.

## Fallgropar

- **En ändrad monterad konfigfil startar inte om något.** `up -d` jämför
  tjänstens definition, inte innehållet i en bind-mount. Gäller Caddyfile i
  produktionsstacken: rätt åtgärd är
  `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile`.
- **Byter tailnetet namn i Tailscale-adminen** slutar certifikaten gälla,
  eftersom de är utställda på FQDN:en. Kör `sudo tailscale serve reset` och
  båda serve-raderna igen, och rätta `BASE_URL` i `.env.staging`.
- **`SMTP_*` hör hemma i compose-filen, inte i `.env.staging`.** Sätts de i
  env-filen vinner de, och då kan staging nå Lettermint.
