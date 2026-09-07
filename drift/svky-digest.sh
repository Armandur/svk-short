#!/usr/bin/env bash
# Slår upp den digest en tagg pekar på just nu, och skriver ut den i den form
# SVKY_IMAGE vill ha.
#
#   drift/svky-digest.sh                # latest
#   drift/svky-digest.sh sha-abc1234    # en bestämd commit
#
# Skälet att den finns: "docker compose pull" hämtar vad taggen råkar peka på
# i det ögonblicket. Vill man kunna säga vilken version som körs, och kunna
# skicka SAMMA version vidare från staging till produktion utan ombyggnad,
# måste digesten skrivas ner någonstans. Här är den enda platsen som vet.
set -euo pipefail

IMAGE=${SVKY_IMAGE_REPO:-ghcr.io/armandur/svk-short}
TAGG=${1:-latest}

if ! command -v docker >/dev/null; then
    echo "docker saknas" >&2
    exit 1
fi

# Hämta först. Utan det svarar inspect på en gammal lokal kopia, och skriptet
# hade rapporterat en digest som inte längre är den taggen pekar på.
docker pull -q "$IMAGE:$TAGG" >/dev/null

DIGEST=$(docker image inspect "$IMAGE:$TAGG" \
    --format '{{index .RepoDigests 0}}' 2>/dev/null | cut -d@ -f2)

if [[ ! "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Fick ingen giltig digest för $IMAGE:$TAGG" >&2
    exit 1
fi

echo "$IMAGE@$DIGEST"
