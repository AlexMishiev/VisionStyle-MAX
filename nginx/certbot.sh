#!/bin/sh
# Получение и продление сертификата Let's Encrypt для домена бота.
# Проверка домена идёт через nginx на порту 80 (папка /var/www/certbot).

: "${WEBHOOK_DOMAIN:?Не задан WEBHOOK_DOMAIN в .env.docker.max}"
: "${CERTBOT_EMAIL:?Не задан CERTBOT_EMAIL в .env.docker.max}"

STAGING=""
if [ "$CERTBOT_STAGING" = "1" ]; then
    STAGING="--staging"
fi

# даём nginx время запуститься
sleep 15

while [ ! -f "/etc/letsencrypt/live/$WEBHOOK_DOMAIN/fullchain.pem" ]; do
    echo "Получаю сертификат для $WEBHOOK_DOMAIN"
    if certbot certonly --webroot -w /var/www/certbot -d "$WEBHOOK_DOMAIN" \
            --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email --non-interactive $STAGING; then
        break
    fi
    # у Let's Encrypt есть лимит на неудачные попытки, поэтому не спешим
    echo "Не удалось получить сертификат, следующая попытка через час"
    sleep 3600
done

# сертификат живёт 90 дней, certbot продлевает его, когда остаётся меньше 30
while true; do
    sleep 43200
    certbot renew --webroot -w /var/www/certbot --quiet
done
