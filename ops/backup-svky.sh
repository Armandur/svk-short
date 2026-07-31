#!/bin/bash
# Daglig backup av svky.se-databasen till Google Drive.
#
# Kör från rasmus user-crontab på Hetzner-burken:
#   0 3 * * * /home/rasmus/svk-short/ops/backup-svky.sh >> /home/rasmus/backups/backup.log 2>&1
# (burken kör UTC, alltså 05:00 svensk sommartid)
#
# Vid fel skickas en notis till ntfy-topicen svc_ops. Uteblir körningen helt
# skickas ingenting - ntfy kan inte upptäcka frånvaro. Det kräver en extern
# vakt och spåras som TASK-653 (fas A) i backlog-projektet infra.
set -euo pipefail

COMPOSE_DIR=/home/rasmus/svk-short
BACKUP_DIR=/home/rasmus/backups
LOG_FILE=/home/rasmus/backups/backup.log
RCLONE_REMOTE="Pettersson Vik:svky.se-backup"
RETENTION_DAYS=30

# Token och topic. Filen ägs av rasmus med chmod 600 och ligger utanför repot.
# Saknas den fortsätter backupen ändå - en tyst notiskanal får aldrig hindra
# själva säkerhetskopieringen.
NTFY_ENV="$HOME/.config/ntfy.env"
[ -r "$NTFY_ENV" ] && . "$NTFY_ENV"

TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
WORK_FILE="$COMPOSE_DIR/data/backup.db"
DEST_FILE="$BACKUP_DIR/links-$TIMESTAMP.db.gz"

# Vilket steg vi är på, så notisen kan berätta var det brast utan att någon
# behöver öppna loggen.
STEP="init"

mkdir -p "$BACKUP_DIR"
exec >>"$LOG_FILE" 2>&1
echo "=== $(date -Iseconds) starting backup ==="

notify_failure() {
    local rc=$1
    if [ -z "${NTFY_TOKEN:-}" ] || [ -z "${NTFY_URL:-}" ]; then
        echo "VARNING: ingen ntfy-konfig i $NTFY_ENV, notis skickades inte"
        return 0
    fi
    # -f så HTTP-fel ger nollskild status, || true så en död ntfy varken
    # maskerar det riktiga felet eller triggar set -e inne i trappen.
    curl -fsS --max-time 10 \
        -H "Authorization: Bearer $NTFY_TOKEN" \
        -H "Title: svky.se / backup" \
        -H "Priority: 3" \
        -H "Tags: warning,floppy_disk" \
        -d "Backupen misslyckades i steget '$STEP' (exit $rc).
Databasen är osäkrad tills nästa lyckade körning.
Logg: $LOG_FILE på $(hostname)" \
        "$NTFY_URL/${NTFY_TOPIC:-svc_ops}" >/dev/null || \
        echo "VARNING: kunde inte nå ntfy, notis gick inte fram"
}

# Ett enda EXIT-trap. Ett andra trap hade tyst ersatt det första och tagit
# bort städningen av arbetsfilen.
cleanup() {
    local rc=$?
    rm -f "$WORK_FILE"
    if [ "$rc" -ne 0 ]; then
        echo "FEL: backupen avbröts i steget '$STEP' (exit $rc)"
        notify_failure "$rc"
    fi
}
trap cleanup EXIT

cd "$COMPOSE_DIR"

# 1. Dumpa via SQLite online-backup (säkert medan appen skriver)
STEP="sqlite-dump"
docker compose exec -T svky \
    sqlite3 data/links.db ".backup /app/data/backup.db"

# 2. Verifiera integritet innan vi arkiverar
STEP="integrity-check"
docker compose exec -T svky \
    sqlite3 /app/data/backup.db "PRAGMA integrity_check;" | grep -q '^ok$' || {
        echo "FAIL: integrity_check underkänt"
        exit 1
    }

# 3. Komprimera direkt till destinationen
STEP="gzip"
gzip -c "$WORK_FILE" > "$DEST_FILE"

# 4. Ladda upp off-site
STEP="rclone-upload"
rclone copy "$DEST_FILE" "$RCLONE_REMOTE" \
    --log-level INFO --stats 0

# 5. Rensa gamla backuper - både lokalt och på Drive
STEP="retention"
find "$BACKUP_DIR" -name 'links-*.db.gz' -mtime +$RETENTION_DAYS -delete
rclone delete "$RCLONE_REMOTE" \
    --min-age ${RETENTION_DAYS}d \
    --include 'links-*.db.gz'

STEP="klart"
echo "OK: $DEST_FILE ($(du -h "$DEST_FILE" | cut -f1))"
