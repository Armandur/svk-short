#!/usr/bin/env bash
# Rullar ut driftkoden: kopierar till /usr/local/bin och /etc/systemd/system.
# Startas av driftytans knapp, körs som root.
#
# install -m 644, inte cp. cp bevarar källans rättigheter, och en umask på 077
# ger rotägda 600-filer som samlaren inte kan LÄSA - den rapporterade dem då
# som olika fast de var identiska. Enhetsfiler bär inga hemligheter.
#
# Filnamnen står HÄR och läses aldrig ur en katalog. Ett steg som läser vad det
# ska installera ur någon annans fil är en godtycklig installationsprimitiv.
set -euo pipefail

ARBETSKATALOG=${SVKY_ARBETSKATALOG:-/home/rasmus/svk-short}
cd "$ARBETSKATALOG"

logga() { echo "[$(date -Is)] $*"; }

ENHETER="
svky-driftyta.service
svky-samla-lage.service
svky-samla-lage.timer
svky-staging-uppdatera.service
svky-staging-uppdatera.timer
svky-begaran-uppdatera.path
svky-begaran-uppdatera.service
svky-begaran-promotera.path
svky-begaran-promotera.service
svky-begaran-hamta-driftkod.path
svky-begaran-hamta-driftkod.service
svky-begaran-rulla-ut.path
svky-begaran-rulla-ut.service
"

install -m 755 drift/svky-driftyta.py /usr/local/bin/svky-driftyta
logga "Installerade /usr/local/bin/svky-driftyta"

for e in $ENHETER; do
    [ -f "drift/systemd/$e" ] || { logga "SAKNAS i repot: $e"; continue; }
    install -m 644 "drift/systemd/$e" "/etc/systemd/system/$e"
done
logga "Installerade $(echo "$ENHETER" | grep -c .) enheter"

systemctl daemon-reload

# Starta om det som kör långlivat. De kortlivade jobben läser sin enhet när de
# startar, men en långlivad tjänst sitter kvar på den konfiguration den läste -
# enable --now startar INTE om något som redan kör.
systemctl restart svky-driftyta.service
systemctl restart svky-begaran-uppdatera.path svky-begaran-promotera.path \
    svky-begaran-hamta-driftkod.path svky-begaran-rulla-ut.path 2>/dev/null || true

# Skriv ner vad som rullades ut. Utan den här filen går det inte att svara
# på "vilken kod kör de rotägda kopiorna" annat än genom att jämföra filer -
# och en jämförelse säger bara om de skiljer sig, inte VAD som saknas.
COMMIT=$(git rev-parse HEAD)
install -d -m 755 /var/lib/svky
printf '%s\n' "$COMMIT" > /var/lib/svky/utrullat
chmod 644 /var/lib/svky/utrullat

logga "Utrullat från $(git rev-parse --short=8 HEAD)"
