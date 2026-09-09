# NoveraShop

Telegram-магазин на Python + aiogram 3 + SQLite.

## Файлы

- `main.py` — основной код бота.
- `requirements.txt` — зависимости.
- `.env.example` — пример переменных окружения.
- `Procfile` — запуск бота.

## Переменные окружения

`BOT_TOKEN` — токен бота.

`ADMIN_ID` — Telegram ID администратора. По умолчанию `8872934046`.

`DB_PATH` — путь к SQLite, по умолчанию `data/shop.db`.

Токен не хранится в репозитории. Его нужно добавить в переменные окружения хостинга.

## Запуск

```bash
pip install -r requirements.txt
python main.py
```

Бот использует polling, поэтому отдельный веб-сервер для Telegram webhook не нужен.
