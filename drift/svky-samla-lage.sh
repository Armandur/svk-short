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

# En path-enhet som fallerat plockar inte upp något, och knappen blir tyst
# död. Utan den här raden är enda spåret att en begäran ligger kvar - och
# det ser ut som att jobbet bara är långsamt.
begaran_trasiga=""
for e in svky-begaran-uppdatera.path svky-begaran-promotera.path; do
    [ "$(systemctl is-active "$e" 2>/dev/null)" = "active" ] || begaran_trasiga="$begaran_trasiga $e"
done
begaran_trasiga=${begaran_trasiga# }
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

# --- Driftkoden -----------------------------------------------------------
# Appens kod når servern genom en signerad image. Driftkoden här når den bara
# genom att någon kör git pull och sudo cp för hand, och utan de här raderna
# läses tystnad som framgång: en kvarglömd utrullning ser likadan ut som en
# gjord. Hände 2026-09-07 - StartLimitIntervalSec-fixen kopierades aldrig, och
# knapparna kunde dö tyst igen medan sidan såg frisk ut.
#
# TVÅ frågor som slocknar vid OLIKA tillfällen, och därför inte slås ihop:
# hur många commitar efter utcheckningen ligger, och vilka rotägda kopior som
# skiljer sig från den.
drift_efter=""
drift_amne=""
drift_fel=""
# Fånga git:s EGEN förklaring. "Kunde inte jämföra" utan orsak skickar
# felsökningen till fel ställe - och en enhet som faller av en rättighet ser
# likadan ut som ett nätfel.
if _fetchfel=$(git fetch origin 2>&1 >/dev/null); then
    drift_efter=$(git rev-list --count HEAD..origin/main 2>/dev/null)
    [ "${drift_efter:-0}" -gt 0 ] 2>/dev/null \
        && drift_amne=$(git log -1 --format=%s origin/main 2>/dev/null)
else
    drift_fel=$(printf '%s' "$_fetchfel" | tr '\n' ' ' | cut -c1-300)
    [ -n "$drift_fel" ] || drift_fel="git fetch föll utan att säga varför"
fi

# Kopiorna. Namnen står HÄR och läses aldrig ur en katalog på servern - ett
# steg som läser vad det ska jämföra ur någon annans fil är svårare att lita
# på än en lista man kan granska.
drift_outrullade=""
drift_olasbara=""
_jamfor() {  # _jamfor <i utcheckningen> <installerad>
    local namn; namn=$(basename "$2")
    [ -f "$2" ] || { drift_outrullade="$drift_outrullade $namn"; return; }
    # SKILJ på "kan inte läsa" och "skiljer sig". En rotägd 600-fil går inte
    # att jämföra som rasmus, och att rapportera det som en skillnad är att
    # presentera en oförmåga att kontrollera som ett resultat - sidan sa att
    # servern körde annan kod fast filerna var identiska.
    if [ ! -r "$2" ]; then
        drift_olasbara="$drift_olasbara $namn"
        return
    fi
    diff -q "$1" "$2" >/dev/null 2>&1 || drift_outrullade="$drift_outrullade $namn"
}
_jamfor drift/svky-driftyta.py /usr/local/bin/svky-driftyta
for e in svky-driftyta.service svky-samla-lage.service svky-samla-lage.timer \
         svky-staging-uppdatera.service svky-staging-uppdatera.timer \
         svky-begaran-uppdatera.path svky-begaran-uppdatera.service \
         svky-begaran-promotera.path svky-begaran-promotera.service; do
    _jamfor "drift/systemd/$e" "/etc/systemd/system/$e"
done
drift_outrullade=${drift_outrullade# }
drift_olasbara=${drift_olasbara# }

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
  "begaran_trasiga": $(js "$begaran_trasiga"),
  "drift": {"efter": $(js "$drift_efter"), "amne": $(js "$drift_amne"), "fel": $(js "$drift_fel"), "outrullade": $(js "$drift_outrullade"), "olasbara": $(js "$drift_olasbara")},
  "ci": $ci
}
EOF
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$TMP" || { echo "ogiltig JSON, skriver inte" >&2; rm -f "$TMP"; exit 1; }
chmod 644 "$TMP" && mv "$TMP" "$UT"
