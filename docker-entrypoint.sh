#!/bin/sh
set -e

# Veritabanı şemasını uygula ve statik dosyaları topla; ardından asıl
# komutu (gunicorn) devral. Coolify/Dockerfile dağıtımları için gerekli.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
