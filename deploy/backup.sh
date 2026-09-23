#!/usr/bin/env bash
# Kunlik zaxira: PostgreSQL dump + yuklangan rasmlar.
# Cron: 0 3 * * * /srv/stolda-backend/deploy/backup.sh >> /var/log/stolda-backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

BACKUP_DIR="${BACKUP_DIR:-/srv/backups/stolda}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y-%m-%d_%H%M)"
COMPOSE="docker compose -f docker-compose.prod.yml"

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] baza zaxiralanmoqda"
$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | gzip > "$BACKUP_DIR/db-$STAMP.sql.gz"

echo "[$(date -Iseconds)] rasmlar zaxiralanmoqda"
$COMPOSE run --rm --no-deps -T --user root \
  -v "$BACKUP_DIR:/backup" \
  --entrypoint tar api -czf "/backup/media-$STAMP.tar.gz" -C /srv/media .

echo "[$(date -Iseconds)] $KEEP_DAYS kundan eski nusxalar o'chirilmoqda"
find "$BACKUP_DIR" -name 'db-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media-*.tar.gz' -mtime "+$KEEP_DAYS" -delete

echo "[$(date -Iseconds)] ✓ tayyor: $BACKUP_DIR"
ls -lh "$BACKUP_DIR" | tail -4
