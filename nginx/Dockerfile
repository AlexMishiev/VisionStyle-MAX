FROM nginx:1.27-alpine

RUN apk add --no-cache openssl && mkdir -p /etc/nginx/ssl /var/www/certbot

COPY default.conf /etc/nginx/conf.d/default.conf
COPY start.sh /start.sh

ENTRYPOINT ["/bin/sh", "/start.sh"]
