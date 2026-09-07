#!/usr/bin/env bash
# Verifierar att en image är signerad av VÅR workflow på main.
#
#   drift/svky-verifiera.sh ghcr.io/armandur/svky.se@sha256:...
#   drift/svky-verifiera.sh                     # läser SVKY_IMAGE ur .env
#
# Att en image ligger i vårt registry är inget bevis. Den som kan pusha dit
# kan pusha vad som helst, och :latest säger bara vad någon senast döpte
# något till. Identiteten nedan binder signaturen till exakt repository,
# workflowfil och gren.
#
# Kontrollen görs HÄR, på servern, och inte bara i CI. CI som intygar åt sig
# självt är ingen grind - den som kan ändra workflowen kan ändra intyget.
set -euo pipefail

REPO=${SVKY_REPO:-Armandur/svky.se}
WORKFLOW=${SVKY_WORKFLOW:-.github/workflows/docker.yml}
GREN=${SVKY_GREN:-refs/heads/main}

IMAGE=${1:-}
if [ -z "$IMAGE" ] && [ -f .env ]; then
    IMAGE=$(grep -m1 '^SVKY_IMAGE=' .env | cut -d= -f2-)
fi

if [ -z "$IMAGE" ]; then
    echo "Ange en image, eller sätt SVKY_IMAGE i .env." >&2
    exit 2
fi

# En tagg går inte att verifiera meningsfullt: kontrollen skulle gälla vad
# taggen pekar på nu, medan nästa pull hämtar vad den pekar på då.
if [[ ! "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "Måste vara en digest, inte en tagg: $IMAGE" >&2
    exit 2
fi

if ! command -v cosign >/dev/null; then
    echo "cosign saknas. Installera med:" >&2
    echo "  sudo curl -fsSL -o /usr/local/bin/cosign \\" >&2
    echo "    https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64" >&2
    echo "  sudo chmod +x /usr/local/bin/cosign" >&2
    exit 3
fi

cosign verify "$IMAGE" \
    --certificate-identity "https://github.com/${REPO}/${WORKFLOW}@${GREN}" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    > /dev/null

echo "OK: $IMAGE är signerad av ${REPO} på ${GREN}"
