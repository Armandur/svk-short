#!/usr/bin/env bash
# Håller staging i fas med :latest. Körs av en timer, se drift/systemd/.
#
# Servern HÄMTAR, GitHub pushar inte. Följden är att GitHub inte har någon
# åtkomst alls till den här värden - inget deploykonto, ingen sudoers-rad,
# ingen inkommande ssh. Förtroendeankaret är cosign-signaturen, inte
# transporten, och den kontrollen ägde vi redan.
#
# Gör ingenting när digesten är oförändrad. Tyst i det normalfallet, så att
# en rad i journalen betyder att något faktiskt hände.
set -euo pipefail

ARBETSKATALOG=${SVKY_ARBETSKATALOG:-/home/rasmus/svk-short}
ENVFIL=${SVKY_STAGING_ENV:-.env.staging}
PROJEKT=${SVKY_STAGING_PROJEKT:-svky-staging}
COMPOSE=${SVKY_STAGING_COMPOSE:-docker-compose.staging.yml}
HALSA=${SVKY_STAGING_HALSA:-http://127.0.0.1:8001/healthz}
TAGG=${SVKY_STAGING_TAGG:-latest}
VANTA=${SVKY_STAGING_VANTA:-60}

cd "$ARBETSKATALOG"

# Ett jobb åt gången. Timern kan fyra medan föregående körning väntar på
# health, och två samtidiga byten av samma stack är inget att felsöka.
exec 9>"/tmp/svky-uppdatera-staging.las"
if ! flock -n 9; then
    echo "En körning pågår redan, hoppar över."
    exit 0
fi

logga() { echo "[$(date -Is)] $*"; }

notis() {
    # Valfritt. Utan NTFY_URL och NTFY_TOPIC sker ingenting - notiskanalen
    # provisioneras i TASK-1086 och ska inte blockera den här.
    [ -n "${NTFY_URL:-}" ] && [ -n "${NTFY_TOPIC:-}" ] || return 0
    curl -fsS -m 10 -H "Title: svky staging" -H "Priority: ${2:-default}" \
        ${NTFY_TOKEN:+-H "Authorization: Bearer $NTFY_TOKEN"} \
        -d "$1" "$NTFY_URL/$NTFY_TOPIC" > /dev/null || true
}

NY=$(drift/svky-digest.sh "$TAGG")

NUVARANDE=$(grep -m1 '^SVKY_IMAGE=' "$ENVFIL" 2>/dev/null | cut -d= -f2- || true)
if [ "$NY" = "$NUVARANDE" ]; then
    exit 0
fi

logga "Ny version: $NY (hade $NUVARANDE)"

# Verifiera FÖRE bytet. En osignerad image ska inte kunna nå ens staging -
# annars vore signeringen bara en ritual på produktionssidan.
#
# Meddelandet säger INTE att signaturen saknas. Ett verktyg som inte kunde
# köra och en signatur som inte fanns ger båda ett rött svar här, och att
# gissa mellan dem skickar felsökningen åt fel håll: första gången det här
# föll var orsaken en skrivskyddad hemkatalog, inte en osignerad image.
# Verifierarens egen utdata får därför följa med till journalen.
if ! VERIFIERING=$(drift/svky-verifiera.sh "$NY" 2>&1); then
    logga "AVVISAD: kunde inte verifiera $NY. Staging rörs inte."
    printf '%s\n' "$VERIFIERING" >&2
    notis "Kunde inte verifiera en ny image. Staging kör vidare på den gamla." high
    exit 1
fi

# Skriv om raden atomärt. En halvskriven env-fil hade tagit ner stacken vid
# nästa kommando, inklusive det som skulle laga den.
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
if grep -q '^SVKY_IMAGE=' "$ENVFIL"; then
    sed "s|^SVKY_IMAGE=.*|SVKY_IMAGE=$NY|" "$ENVFIL" > "$TMP"
else
    cat "$ENVFIL" > "$TMP"
    echo "SVKY_IMAGE=$NY" >> "$TMP"
fi
cp --preserve=mode,ownership "$ENVFIL" "$ENVFIL.forra"
mv "$TMP" "$ENVFIL"
trap - EXIT

COMPOSE_ARGS=(-p "$PROJEKT" -f "$COMPOSE" --env-file "$ENVFIL")
docker compose "${COMPOSE_ARGS[@]}" pull -q svky
docker compose "${COMPOSE_ARGS[@]}" up -d svky

for _ in $(seq "$VANTA"); do
    if curl -fsS -o /dev/null -m 3 "$HALSA"; then
        logga "Staging kör $NY"
        notis "Staging uppdaterad till ${NY##*@}"
        exit 0
    fi
    sleep 1
done

# Staging rullas INTE tillbaka. Det är platsen där en trasig version ska få
# synas, ingen drabbas, och en återgång hade städat bort just det man
# behöver läsa. Föregående env-fil ligger kvar som .forra för den som ändå
# vill backa för hand.
logga "FEL: $HALSA svarade inte inom ${VANTA}s. Staging lämnas som den är."
docker compose "${COMPOSE_ARGS[@]}" logs --tail=60 svky || true
notis "Staging blev inte frisk efter uppdatering. Loggen finns i journalen." high
exit 1
