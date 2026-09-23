#!/usr/bin/env bash
# Birinchi marta Let's Encrypt sertifikatini olish.
# DNS A-yozuvi allaqachon VPS IP'siga qaratilgan bo'lishi kerak.
set -euo pipefail

cd "$(dirname "$0")"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

require() {
  if [ -z "${2:-}" ]; then
    echo "❌ .env faylida $1 ko'rsatilmagan." >&2
    exit 1
  fi
}

require DOMAIN "${DOMAIN:-}"
require LETSENCRYPT_EMAIL "${LETSENCRYPT_EMAIL:-}"

COMPOSE="docker compose -f docker-compose.prod.yml"

echo "▸ vaqtinchalik HTTP-only nginx"
mkdir -p nginx/conf.d
cat > nginx/conf.d/stolda.conf <<CONF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'stolda.uz tayyorlanmoqda'; add_header Content-Type text/plain; }
}
CONF

$COMPOSE up -d nginx

echo "▸ sertifikat so'ralmoqda"
$COMPOSE run --rm --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" -d "www.$DOMAIN" \
  --email "$LETSENCRYPT_EMAIL" --agree-tos --no-eff-email

echo "▸ to'liq konfiguratsiya qaytarilmoqda"
sed "s/DOMAIN/$DOMAIN/g" nginx/nginx.conf > nginx/conf.d/stolda.conf
$COMPOSE up -d nginx
$COMPOSE exec -T nginx nginx -s reload

echo "✓ sertifikat olindi. Endi ./scripts/deploy.sh ishga tushiring."
