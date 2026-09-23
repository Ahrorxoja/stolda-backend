#!/usr/bin/env bash
# stolda.uz — VPS'ga qo'yish / yangilash.
# Ishlatish: ./deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ .env topilmadi. deploy/.env.prod.example dan nusxa oling." >&2
  exit 1
fi

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

echo "▸ nginx konfiguratsiyasi tayyorlanmoqda ($DOMAIN)"
mkdir -p nginx/conf.d
sed "s/DOMAIN/$DOMAIN/g" nginx/nginx.conf > nginx/conf.d/stolda.conf

echo "▸ image'lar qurilmoqda"
docker compose -f docker-compose.prod.yml build

echo "▸ baza va kesh ishga tushirilmoqda"
docker compose -f docker-compose.prod.yml up -d db redis

echo "▸ migratsiyalar"
docker compose -f docker-compose.prod.yml run --rm api python manage.py migrate --noinput

echo "▸ static fayllar"
docker compose -f docker-compose.prod.yml run --rm api python manage.py collectstatic --noinput

echo "▸ hamma servis ishga tushirilmoqda"
docker compose -f docker-compose.prod.yml up -d

echo "▸ nginx qayta yuklanmoqda"
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload 2>/dev/null || true

echo "✓ tayyor — https://$DOMAIN"
