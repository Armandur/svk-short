#!/usr/bin/env bash
# Flyttar den version staging KÖR till produktionen.
#
#   drift/svky-promotera.sh              # visar vad som skulle ske
#   drift/svky-promotera.sh --ja         # gör det
#
# Skillnaden mot en vanlig deploy är kontrollerna, inte kommandona. Utan dem
# vore det här bara ett kortare sätt att skriva docker compose up -d.
set -euo pipefail

ARBETSKATALOG=${SVKY_ARBETSKATALOG:-/home/rasmus/svk-short}
ENVFIL=${SVKY_PROD_ENV:-.env}
STAGING_PROJEKT=${SVKY_STAGING_PROJEKT:-svky-staging}
STAGING_COMPOSE=${SVKY_STAGING_COMPOSE:-docker-compose.staging.yml}
STAGING_ENV=${SVKY_STAGING_ENV:-.env.staging}
HALSA=${SVKY_PROD_HALSA:-https://svky.se/healthz}
BACKUPKATALOG=${SVKY_BACKUPKATALOG:-backups}
VANTA=${SVKY_PROD_VANTA:-60}

cd "$ARBETSKATALOG"
logga() { echo "[$(date -Is)] $*"; }
avbryt() { logga "AVBRUTET: $*"; exit 1; }

notis() {
    # Valfritt, se TASK-1086. Utan NTFY_URL och NTFY_TOPIC sker ingenting.
    [ -n "${NTFY_URL:-}" ] && [ -n "${NTFY_TOPIC:-}" ] || return 0
    curl -fsS -m 10 -H "Title: svky produktion" -H "Priority: ${2:-default}" \
        ${NTFY_TOKEN:+-H "Authorization: Bearer $NTFY_TOKEN"} \
        -d "$1" "$NTFY_URL/$NTFY_TOPIC" > /dev/null || true
}

# --- 1. Vad kör staging FAKTISKT? ---------------------------------------
# Läs containern, inte env-filen. Filen säger vad någon skrev dit, containern
# vad som verkligen startades - och en kandidat från i förrgår säger
# ingenting om det som körs nu.
STAGING_ID=$(docker compose -p "$STAGING_PROJEKT" -f "$STAGING_COMPOSE" \
    --env-file "$STAGING_ENV" ps -q svky 2>/dev/null || true)
[ -n "$STAGING_ID" ] || avbryt "staging kör inte. Det finns inget att befordra."

KANDIDAT=$(docker inspect --format '{{.Config.Image}}' "$STAGING_ID")
[[ "$KANDIDAT" =~ @sha256:[0-9a-f]{64}$ ]] \
    || avbryt "staging kör inte på en digest utan på '$KANDIDAT'."

COMMIT=$(docker inspect --format \
    '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$STAGING_ID")

NUVARANDE=$(grep -m1 '^SVKY_IMAGE=' "$ENVFIL" | cut -d= -f2- || true)

echo "  Staging kör:   $KANDIDAT"
echo "  Commit:        ${COMMIT:-okänd}"
echo "  Produktionen:  ${NUVARANDE:-inget satt}"

[ "$KANDIDAT" != "$NUVARANDE" ] || { logga "Produktionen kör redan den versionen."; exit 0; }

# --- 2. Verifiera signaturen IGEN ---------------------------------------
# Kontrollen gjordes när staging bytte, men det var då. Utan den här vore en
# rad i en fil ensam nog att avgöra vad som körs i produktion.
if ! VERIFIERING=$(drift/svky-verifiera.sh "$KANDIDAT" 2>&1); then
    printf '%s\n' "$VERIFIERING" >&2
    avbryt "kunde inte verifiera kandidatens signatur."
fi
echo "  Signatur:      verifierad"

if [ "${1:-}" != "--ja" ]; then
    echo
    echo "Torrkörning. Kör om med --ja för att genomföra."
    exit 0
fi

# --- 3. Färsk backup, och den ska gå att LÄSA ---------------------------
# En backup som inte går att läsa är ingen backup. Kontrollen sker före
# bytet: går den inte igenom ska ingenting ha ändrats.
mkdir -p "$BACKUPKATALOG"
DUMP="$BACKUPKATALOG/links-$(date +%Y%m%d-%H%M%S).db"
sqlite3 data/links.db ".backup '$DUMP'" || avbryt "kunde inte ta backup."
LAGE=$(sqlite3 "$DUMP" "PRAGMA integrity_check;" 2>&1 || true)
[ "$LAGE" = "ok" ] || avbryt "backupen går inte att läsa: $LAGE"
logga "Backup: $DUMP (integrity_check ok)"

# --- 4. Logga föregående FÖRE bytet -------------------------------------
# Efteråt är den borta ur env-filen, och vägen tillbaka med den.
logga "Föregående version: ${NUVARANDE:-inget satt}"

# Schemaversionen före bytet. Migrationerna körs av appen vid uppstart, så
# den nya versionen kan hinna flytta schemat innan hälsokontrollen faller -
# och en tyst återgång sätter då den GAMLA appen mot ett NYARE schema.
# Additiva migrationer överlever det, men _drop_col finns och används.
SCHEMA_FORE=$(sqlite3 data/links.db \
    "SELECT COALESCE(MAX(version), 0) FROM schema_version;" 2>/dev/null || echo "?")
logga "Schemaversion före: $SCHEMA_FORE"

# --- 5. Byt -------------------------------------------------------------
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
sed "s|^SVKY_IMAGE=.*|SVKY_IMAGE=$KANDIDAT|" "$ENVFIL" > "$TMP"
cp --preserve=mode,ownership "$ENVFIL" "$ENVFIL.forra"
mv "$TMP" "$ENVFIL"; trap - EXIT

docker compose pull -q svky
docker compose up -d svky

# --- 6. Hälsa, och tillbaka om den inte kommer --------------------------
# Här är avvägningen den OMVÄNDA mot staging: en trasig produktion får inte
# stå kvar medan någon felsöker. Bara appen rullas tillbaka - databasen
# nedgraderas aldrig automatiskt, för det kan kasta data. Är schemat
# oförenligt med den gamla imagen krävs dumpen från steg 3 och en människa.
for _ in $(seq "$VANTA"); do
    if curl -fs -o /dev/null -m 3 "$HALSA"; then
        logga "Produktionen kör $KANDIDAT (commit ${COMMIT:-okänd})"
        exit 0
    fi
    sleep 1
done

logga "FEL: $HALSA svarade inte inom ${VANTA}s."
docker compose logs --tail=60 svky || true

if [ -z "$NUVARANDE" ]; then
    avbryt "föregående version är okänd - produktionen kör den NYA versionen trots misslyckad kontroll."
fi

SCHEMA_EFTER=$(sqlite3 data/links.db \
    "SELECT COALESCE(MAX(version), 0) FROM schema_version;" 2>/dev/null || echo "?")

# Har schemat flyttat sig är en tyst återgång FEL svar. Den gamla appen
# möter då ett schema den inte känner igen, och en migration som tagit bort
# en kolumn går inte att önska tillbaka. Databasen nedgraderas aldrig
# automatiskt - det kan kasta data, och en människa ska välja.
if [ "$SCHEMA_FORE" != "$SCHEMA_EFTER" ]; then
    logga "RULLAR INTE TILLBAKA: schemat gick från $SCHEMA_FORE till $SCHEMA_EFTER."
    logga "Den gamla appen känner inte igen det schemat. Produktionen kör den"
    logga "NYA versionen och är trasig. Detta kräver en människa:"
    logga "  backup före bytet: $DUMP"
    logga "  föregående image:  $NUVARANDE"
    logga "  föregående env:    $ENVFIL.forra"
    notis "Promotion misslyckades OCH schemat ändrades. Manuell åtgärd krävs." urgent
    exit 1
fi

logga "Schemat oförändrat ($SCHEMA_FORE). Rullar tillbaka."
mv "$ENVFIL.forra" "$ENVFIL"
docker compose up -d svky
logga "Tillbaka på $NUVARANDE."
exit 1
