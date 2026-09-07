#!/usr/bin/env bash
# Hämtar ny driftkod till utcheckningen. Startas av driftytans knapp.
#
# merge --ff-only, ALDRIG reset --hard. Finns lokala commitar på servern ska
# kommandot vägra och säga det, inte kasta dem tyst - en utcheckning man inte
# kan lita på är värre än en som ligger efter.
#
# Rullar INTE ut. Det är en egen knapp: den här enheten är härdad och får inte
# skriva i /usr/local/bin eller /etc, och bara ETT jobb åt gången kan vara
# aktivt, så kedjade jobb är inget alternativ. Att den är egen ger dessutom en
# egenskap värd mer än ett sparat knapptryck - en fallerad utrullning kan inte
# dölja att koden hämtades.
set -euo pipefail

ARBETSKATALOG=${SVKY_ARBETSKATALOG:-/home/rasmus/svk-short}
cd "$ARBETSKATALOG"

logga() { echo "[$(date -Is)] $*"; }

FORE=$(git rev-parse --short=8 HEAD)

if ! UT=$(git fetch origin 2>&1 >/dev/null); then
    logga "AVBRUTET: git fetch föll: $UT"
    exit 1
fi

EFTER_REMOTE=$(git rev-parse --short=8 origin/main)
if [ "$FORE" = "$EFTER_REMOTE" ]; then
    logga "Redan i fas med origin/main ($FORE)."
    exit 0
fi

# Vägra hellre än att gissa. En divergerad utcheckning betyder att någon
# arbetat direkt på servern, och det ska en människa titta på.
if ! git merge-base --is-ancestor HEAD origin/main; then
    logga "VÄGRAR: utcheckningen har divergerat från origin/main."
    logga "  lokalt: $FORE   origin/main: $EFTER_REMOTE"
    logga "  Det betyder lokala commitar här. Titta på dem först - jag kastar dem inte."
    exit 1
fi

if ! UT=$(git merge --ff-only origin/main 2>&1); then
    logga "AVBRUTET: merge --ff-only föll: $UT"
    exit 1
fi

logga "Hämtade $FORE -> $(git rev-parse --short=8 HEAD)"
logga "Kör Rulla ut drift/ för att få kopiorna i /usr/local/bin och /etc i fas."
