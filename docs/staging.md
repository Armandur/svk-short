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
cd ~/svk-short          # samma utcheckning som produktionen
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

Sätt samma digest i produktionens `.env` och driftsätt:

```sh
echo "SVKY_IMAGE=ghcr.io/armandur/svk-short@sha256:<digest>" >> .env
docker compose pull && docker compose up -d
```

Ingen ombyggnad. Samma image-lager som redan provats i staging startas i
produktionen.

Detta är promotionens manuella form. Signering, automatisk stagingdeploy och
en promotionsyta är steg 3-5 i TASK-1087 och finns inte än. Det betyder att
det ännu är en människa som avgör vad som går ut, och att ingenting
verifierar signaturen. Skriv inte om steget till ett skript utan att ta steg
3 först: ett skript som ser ut som en grind men inte är en grind är sämre än
ett handgrepp.

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
