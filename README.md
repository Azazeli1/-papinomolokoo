# Tank Creative Bot — бот для креативов от бюеров

Система для приёма креативов от медиабаеров через Telegram, автоматического создания Google Doc для ревьюера и структурированного чата.

## Как это работает

```
Бюер (Telegram)                    Ревьюер (веб-панель)
      │                                    │
      ├─ /upload → загружает креатив       │
      │                                    ├─ Видит заявку + ссылку на Google Doc
      ├─ /chat → пишет ревьюеру           ├─ Вкладка «Чат с бюером»
      │                                    ├─ Меняет статус (одобрен / правки / отклонён)
      └─ Получает уведомления              └─ Пишет правки в Google Doc и в чат
```

### Для бюеров (Telegram-бот)

| Команда | Описание |
|---------|----------|
| `/upload` | Загрузить новый креатив (название → описание → файл) |
| `/my` | Список своих заявок и статусов |
| `/chat` | Написать ревьюеру по конкретной заявке |
| `/cancel` | Отменить текущее действие |

### Для ревьюера (веб-панель)

- Список всех заявок от бюеров
- Ссылка на **Google Doc** с креативом каждого отправителя
- Вкладка **«Чат с бюером»** — переписка по заявке
- Смена статуса: Новая → На проверке → Нужны правки → Одобрен / Отклонён

## Быстрый старт

### 1. Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Telegram-бот

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot`
2. Скопируйте токен в `.env` → `TELEGRAM_BOT_TOKEN`
3. Узнайте свой Telegram ID (например через [@userinfobot](https://t.me/userinfobot)) → `ADMIN_TELEGRAM_ID`

### 3. Google Docs (опционально, но рекомендуется)

1. Создайте проект в [Google Cloud Console](https://console.cloud.google.com/)
2. Включите **Google Drive API** и **Google Docs API**
3. Создайте Service Account → скачайте JSON-ключ
4. Положите ключ в `credentials/google-service-account.json`
5. Создайте папку на Google Drive, расшарьте её на email сервис-аккаунта (роль: Редактор)
6. Скопируйте ID папки из URL → `GOOGLE_DRIVE_FOLDER_ID`
7. Укажите свой email → `REVIEWER_EMAIL` (получит доступ к документам)

### 4. Запуск

```bash
python run.py
```

- **Веб-панель:** http://localhost:8000
- **Пароль:** значение `ADMIN_PASSWORD` из `.env` (по умолчанию `admin123`)

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота от BotFather |
| `ADMIN_TELEGRAM_ID` | Ваш Telegram ID (уведомления о новых креативах) |
| `GOOGLE_CREDENTIALS_FILE` | Путь к JSON-ключу сервис-аккаунта |
| `GOOGLE_DRIVE_FOLDER_ID` | ID папки на Google Drive |
| `REVIEWER_EMAIL` | Email ревьюера для доступа к документам |
| `ADMIN_PASSWORD` | Пароль веб-панели |
| `SECRET_KEY` | Секретный ключ (для продакшена) |

### Где хранятся заявки

Каждая заявка сохраняется **в файлы** на сервере (не только в базе):

```
data/submissions/
├── 0001_GFT1833_consultanta-audit.info.txt   ← текст уведомления
├── 0001_GFT1833_consultanta-audit.info.json  ← метаданные
```

Загруженные креативы: `data/uploads/`

Весь код проекта — в репозитории на ветке `main`, не в PR.


```
app/
├── main.py           # FastAPI + запуск бота
├── bot.py            # Telegram-бот для бюеров
├── api.py            # API и веб-панель ревьюера
├── google_service.py # Создание Google Doc на каждый креатив
├── models.py         # БД: бюеры, заявки, сообщения
├── templates/
│   └── admin.html    # Панель ревьюера
run.py                # Точка входа
```

## Деплой

Для продакшена рекомендуется:
- VPS (Hetzner, DigitalOcean и т.д.)
- Nginx как reverse proxy + SSL
- systemd для автозапуска `python run.py`

Без Google API бот всё равно работает — креативы сохраняются локально, но Google Doc не создаётся.
