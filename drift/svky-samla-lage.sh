#!/usr/bin/env bash
# Samlar driftläget till en JSON-fil som driftytan läser.
#
# Delningen finns av en anledning: att läsa vad som KÖRS kräver dockersocketen,
# och den som når den kan allt med varje container - alltså rotekvivalent. Den
# webbyta någon surfar mot ska inte ha det. Samlaren kör privilegierat och
# skriver en fil, ytan läser filen och har inga rättigheter alls.
set -uo pipefail

ARBETSKATALOG=${SVKY_ARBETSKATALOG:-/home/rasmus/svk-short}
UT=${SVKY_LAGESFIL:-/var/lib/svky/lage.json}
PROD_CONTAINER=${SVKY_PROD_CONTAINER:-svk-short-svky-1}
STAGING_CONTAINER=${SVKY_STAGING_CONTAINER:-svky-staging-svky-1}
REPO=${SVKY_REPO:-Armandur/svky.se}

cd "$ARBETSKATALOG" 2>/dev/null || { echo "saknar $ARBETSKATALOG" >&2; exit 1; }

# Varje fält är antingen ett värde eller null. Null betyder "gick inte att
# hämta" och ytan SKA säga det - en panel som ser tom ut när något är trasigt
# säger samma sak som att allt är bra, och det är fel svar på rätt fråga.
js() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1" 2>/dev/null || echo null; }

falt() {  # falt <container> <format>
    local v
    v=$(docker inspect --format "$2" "$1" 2>/dev/null) || return
    [ -n "$v" ] && printf '%s' "$v"
}

image_prod=$(falt "$PROD_CONTAINER" '{{.Config.Image}}')
commit_prod=$(falt "$PROD_CONTAINER" '{{index .Config.Labels "org.opencontainers.image.revision"}}')
status_prod=$(falt "$PROD_CONTAINER" '{{.State.Status}}')
image_stag=$(falt "$STAGING_CONTAINER" '{{.Config.Image}}')
commit_stag=$(falt "$STAGING_CONTAINER" '{{index .Config.Labels "org.opencontainers.image.revision"}}')
status_stag=$(falt "$STAGING_CONTAINER" '{{.State.Status}}')

# Vad :latest pekar på just nu. Misslyckas anropet ska fältet bli null, inte
# den senast kända digesten - ett gammalt värde som ser färskt ut är värre.
senaste=$(drift/svky-digest.sh latest 2>/dev/null)

enhet() {  # enhet <namn> <egenskap>
    systemctl show "$1" --property="$2" --value 2>/dev/null
}
upp_result=$(enhet svky-staging-uppdatera.service Result)
upp_kod=$(enhet svky-staging-uppdatera.service ExecMainStatus)
upp_aktiv=$(systemctl is-active svky-staging-uppdatera.service 2>/dev/null)

# ExecMainExitTimestamp TÖMS medan tjänsten kör. Samlaren och uppdateraren
# har samma period, så de krockar regelbundet - och sidan sa okänt fast
# ingenting var okänt. En signal som ropar varg slutar betyda något.
# InactiveEnterTimestamp överlever körningen och bär samma tidpunkt.
upp_tid=$(enhet svky-staging-uppdatera.service ExecMainExitTimestamp)
[ -n "$upp_tid" ] || upp_tid=$(enhet svky-staging-uppdatera.service InactiveEnterTimestamp)
sond_aktiv=$(systemctl is-active uppetidssond.timer 2>/dev/null)
timer_aktiv=$(systemctl is-active svky-staging-uppdatera.timer 2>/dev/null)

# Senaste körningen på main. UTAN den här raden vet ytan bara vad som NÅTT
# servern, och ett bygge som faller når den aldrig. Tokenen är fine-grained
# med endast Actions: read och är valfri - saknas den blir fältet null och
# ytan säger att den inte vet, inte att allt är bra.
ci=null
if [ -n "${SVKY_GITHUB_TOKEN:-}" ]; then
    svar=$(curl -sS -m 10 -H "Authorization: Bearer $SVKY_GITHUB_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/actions/runs?branch=main&per_page=1" 2>/dev/null)
    ci=$(printf '%s' "$svar" | python3 -c '
import json,sys
try:
    r = json.load(sys.stdin)["workflow_runs"][0]
    print(json.dumps({"namn": r["name"], "utfall": r["conclusion"] or r["status"],
                      "sha": r["head_sha"][:8], "url": r["html_url"],
                      "tid": r["updated_at"]}))
except Exception:
    print("null")
' 2>/dev/null || echo null)
fi

install -d -m 755 "$(dirname "$UT")"
TMP=$(mktemp)
cat > "$TMP" <<EOF
{
  "hamtad": "$(date -Is)",
  "produktion": {"image": $(js "$image_prod"), "commit": $(js "$commit_prod"), "status": $(js "$status_prod")},
  "staging":    {"image": $(js "$image_stag"), "commit": $(js "$commit_stag"), "status": $(js "$status_stag")},
  "senaste_bygge": $(js "$senaste"),
  "uppdaterare": {"resultat": $(js "$upp_result"), "avslutad": $(js "$upp_tid"), "exitkod": $(js "$upp_kod"), "timer": $(js "$timer_aktiv"), "aktiv": $(js "$upp_aktiv")},
  "uppetidssond": $(js "$sond_aktiv"),
  "ci": $ci
}
EOF
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$TMP" || { echo "ogiltig JSON, skriver inte" >&2; rm -f "$TMP"; exit 1; }
chmod 644 "$TMP" && mv "$TMP" "$UT"
