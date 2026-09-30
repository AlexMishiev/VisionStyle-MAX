#!/bin/sh
# Запуск nginx. Пока certbot не получил сертификат, используется временный самоподписанный,
# иначе nginx не запустится. Раз в 5 минут проверяем, не появился ли новый сертификат.
set -e

: "${WEBHOOK_DOMAIN:?Не задан WEBHOOK_DOMAIN в .env.docker.max}"

LIVE="/etc/letsencrypt/live/$WEBHOOK_DOMAIN"
SSL="/etc/nginx/ssl"

copy_cert() {
    cp -L "$LIVE/fullchain.pem" "$SSL/fullchain.pem"
    cp -L "$LIVE/privkey.pem" "$SSL/privkey.pem"
}

if [ -f "$LIVE/fullchain.pem" ]; then
    copy_cert
else
    echo "Сертификата для $WEBHOOK_DOMAIN ещё нет, запускаюсь с временным"
    openssl req -x509 -nodes -newkey rsa:2048 -days 7 -subj "/CN=$WEBHOOK_DOMAIN" \
        -keyout "$SSL/privkey.pem" -out "$SSL/fullchain.pem" 2> /dev/null
fi

(
    while true; do
        sleep 300
        if [ -f "$LIVE/fullchain.pem" ] && ! cmp -s "$LIVE/fullchain.pem" "$SSL/fullchain.pem"; then
            echo "Найден новый сертификат для $WEBHOOK_DOMAIN, перезагружаю nginx"
            copy_cert
            nginx -s reload
        fi
    done
) &

exec nginx -g "daemon off;"
