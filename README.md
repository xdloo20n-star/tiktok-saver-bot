# TikTok Saver Bot

Telegram-бот для скачивания и отправки публичных TikTok-видео.

## Стек

- Python 3.11+
- aiogram 3
- yt-dlp
- ffmpeg
- python-dotenv

## Деплой на Render (бесплатно)

### Требования

- Аккаунт на [render.com](https://render.com)
- Репозиторий на GitHub с кодом бота
- Токен бота от [@BotFather](https://t.me/BotFather)

---

### Шаг 1 — Загрузи код на GitHub

Если репозитория ещё нет:

```bash
git init
git add .
git commit -m "init"
gh repo create tiktok-saver-bot --public --push --source=.
```

Или через [github.com/new](https://github.com/new) вручную.

---

### Шаг 2 — Создай сервис на Render

1. Открой [dashboard.render.com](https://dashboard.render.com) → **New → Background Worker**
2. Подключи GitHub-репозиторий
3. Render автоматически найдёт `render.yaml` и предложит настройки
4. Убедись, что выбрано:
   - **Runtime:** Docker
   - **Plan:** Free

> Именно **Background Worker**, не Web Service — боту не нужен HTTP-порт.

---

### Шаг 3 — Задай переменную окружения

В настройках сервиса → **Environment** → добавь:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | токен от BotFather |

`MAX_FILE_SIZE_MB` и `DOWNLOAD_TIMEOUT_SEC` уже заданы в `render.yaml`.

---

### Шаг 4 — Deploy

Нажми **Deploy** — Render:

1. Соберёт Docker-образ (установит Python 3.11, ffmpeg, зависимости)
2. Запустит `python bot.py`

Логи доступны в реальном времени во вкладке **Logs**.

---

### Обновление бота

Любой `git push` в `main` автоматически запускает новый деплой.

```bash
git add .
git commit -m "fix: что-то поправил"
git push
```

---

## Запуск локально

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # вставить BOT_TOKEN
python bot.py
```

> Для локального запуска нужен установленный `ffmpeg`:
> `sudo apt install ffmpeg` / `brew install ffmpeg`

---

## Переменные окружения

| Переменная             | По умолчанию | Описание                          |
|------------------------|--------------|-----------------------------------|
| `BOT_TOKEN`            | —            | Токен Telegram-бота (обязательно) |
| `MAX_FILE_SIZE_MB`     | `50`         | Макс. размер видео в МБ           |
| `DOWNLOAD_TIMEOUT_SEC` | `60`         | Таймаут скачивания в секундах     |

---

## Структура проекта

```
tiktok_saver_bot/
├── bot.py            — точка входа, хендлеры aiogram
├── config.py         — конфигурация из переменных окружения
├── downloader.py     — скачивание видео через yt-dlp
├── validators.py     — проверка TikTok-ссылок
├── Dockerfile        — образ для Render
├── render.yaml       — конфигурация Render
├── requirements.txt
├── .env.example
└── downloads/        — временные файлы (удаляются после отправки)
```

---

## Ограничения

- Только публичные видео
- Максимальный размер: 50 МБ (настраивается)
- Один активный запрос на пользователя
- Без удаления водяных знаков
- Render free tier: 750 часов/месяц — хватает на непрерывную работу одного сервиса
