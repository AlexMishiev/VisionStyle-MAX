# VisionStyle: бот для MAX

Бот для мессенджера MAX из проекта VisionStyle (Smart Wardrobe). Пользователь загружает фото своих вещей, бот сохраняет их в гардероб и собирает образ под событие с учётом погоды и места.

Что умеет бот:

- регистрация по email и принятие пользовательского соглашения;
- загрузка вещей по фото, просмотр и редактирование гардероба;
- подбор образа: событие, геолокация, пожелания;
- история сохранённых образов;
- привязка профиля из другого мессенджера командой `/link`.

Бот взаимодействует с backend API VisionStyle.

## Команды бота

| Команда | Что делает |
|---|---|
| `/start` | главное меню |
| `/me` | профиль |
| `/outfit` | собрать образ |
| `/link` | получить код или привязать профиль (`/link КОД`) |
| `/cancel` | отменить текущее действие |

## Структура проекта

```
frontend/
  bot/
    common/        общий код: клиент API, настройки, тексты, логика
    max/           MAX-бот: клиент MAX API, обработчики, клавиатуры, запуск
    assets/
      legal/       пользовательское соглашение и согласие на обработку данных
      manual/      руководство пользователя (PDF)
      tls/         сертификаты (сюда же кладётся cert.pem от backend)
  run_max.py       точка входа
  requirements.txt
  Dockerfile.max
  .env.max.example
nginx/
  Dockerfile       образ nginx для режима webhook
  default.conf     настройки nginx
  start.sh         запуск nginx и подхват нового сертификата
  certbot.sh       получение и продление сертификата Let's Encrypt
docker-compose.yml
.env.docker.max.example
```

## Что нужно для запуска

1. Docker и Docker Compose.
2. Токен бота MAX.
3. Запущенный backend VisionStyle и учётная запись в нём с ролью `bot:max`. 
4. backend имеет самоподписанный сертификат, скопируйте его `cert.pem` в `frontend/bot/assets/tls/cert.pem`. 

## Получение проекта

Склонируйте репозиторий и перейдите в папку проекта:

```bash
git clone https://github.com/AlexMishiev/VisionStyle-MAX.git
cd VisionStyle-MAX
```

Все команды ниже выполняются из папки `VisionStyle-MAX`.

Обновить проект до последней версии и пересобрать бота:

```bash
git pull
docker compose up -d --build
```

Для режима webhook: `docker compose --profile webhook up -d --build`.

## Настройки

Настройки лежат в файле `.env.docker.max` в корне проекта. Шаблон: `.env.docker.max.example`. Переменные окружения важнее значений из файла. Другой файл настроек можно указать через `env_file` в docker-compose.yml.

| Переменная | Описание |
|---|---|
| `MAX_BOT_TOKEN` | токен бота MAX (обязательно) |
| `API_BASE_URL` | адрес backend API, только https |
| `API_TLS_CA_FILE` | путь к `cert.pem` от backend, пусто - системные сертификаты |
| `API_USERNAME`, `API_PASSWORD` | учётная запись бота в API с ролью `bot:max` (обязательно) |
| `REQUEST_TIMEOUT` | таймаут запросов в секундах, по умолчанию 60 |
| `LOG_LEVEL` | уровень логов: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAX_API_BASE_URL` | адрес MAX API, по умолчанию `https://platform-api2.max.ru` |
| `MAX_TLS_CA_FILE` | свой сертификат для MAX API, пусто - сертификат из `frontend/bot/assets/tls` |
| `MAX_MODE` | `polling` или `webhook` |
| `MAX_WEBHOOK_URL`, `MAX_WEBHOOK_SECRET` | только для режима `webhook` |
| `MAX_WEBHOOK_HOST`, `MAX_WEBHOOK_PORT` | где бот слушает webhook, в Docker `0.0.0.0:8081` |

## Запуск в Docker 

```bash
cp .env.docker.max.example .env.docker.max
# заполнить .env.docker.max
docker compose up -d --build
docker compose logs -f max
```

Остановить:

```bash
docker compose down
```

Папка `frontend/bot/assets` подключается в контейнер, поэтому документы, руководство и `cert.pem` можно менять без пересборки. После замены нужно перезапустить бота: `docker compose restart max`.

## Режим webhook

По умолчанию бот работает в режиме `polling` и сам забирает сообщения у MAX. На сервере с белым IP и доменом можно включить webhook: тогда MAX сам присылает сообщения боту через nginx.

```
MAX --https:443--> nginx --http--> max:8081 (бот)
certbot --> Let's Encrypt (проверка домена через nginx на порту 80)
```

Сертификат получать вручную не нужно: контейнер `certbot` сам получает его для домена при первом запуске и продлевает до окончания срока. nginx подхватывает новый сертификат в течение 5 минут.

1. Заведите домен или поддомен для бота, например `bot.smartwardrobe.space`, и сделайте A-запись на IP сервера с ботом. Порты 80 и 443 на этом сервере должны быть свободны и открыты снаружи.
2. В `.env.docker.max` укажите:
   ```
   MAX_MODE=webhook
   MAX_WEBHOOK_URL=https://bot.smartwardrobe.space/max
   MAX_WEBHOOK_SECRET=придумайте_строку_123
   MAX_WEBHOOK_HOST=0.0.0.0
   MAX_WEBHOOK_PORT=8081

   WEBHOOK_DOMAIN=bot.smartwardrobe.space
   CERTBOT_EMAIL=ваша@почта.ru
   CERTBOT_STAGING=0
   ```
   `MAX_WEBHOOK_URL` должен быть на домене из `WEBHOOK_DOMAIN`, на https и без номера порта. `MAX_WEBHOOK_SECRET` - от 5 до 256 символов: латинские буквы, цифры, `_` и `-`.
3. Запустите бота вместе с nginx и certbot:
   ```bash
   docker compose --profile webhook up -d --build
   ```
4. Проверьте, что сертификат получен:
   ```bash
   docker compose logs certbot
   ```

Пока сертификата нет (первые минуты после запуска), nginx работает с временным самоподписанным, и MAX может не доставить сообщения. Если сертификат не получился, certbot повторит попытку через час; причину видно в `docker compose logs certbot` (чаще всего домен ещё не указывает на сервер или закрыт порт 80).

Для проверки без лимитов Let's Encrypt можно поставить `CERTBOT_STAGING=1`: выдаётся тестовый сертификат, которому MAX не доверяет. Перед переходом на настоящий удалите тестовый: `docker compose --profile webhook down` и `docker volume rm` для тома `certbot-conf` (его точное имя видно в `docker volume ls`).

Остановить бота, nginx и certbot:

```bash
docker compose --profile webhook down
```

Если бот работал через webhook, а после запускается в режиме `polling`, сначала удалите подписку в MAX, иначе бот не запустится.
